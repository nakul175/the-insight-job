"""Real PostgreSQL races for incident deduplication and deletion barriers."""

import socket
import threading
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from intelligence.delivery import deliver, store_result
from intelligence.lifecycle import execute_deletion, preview_deletion
from intelligence.models import Capture, Incident, SandboxMessage, WorkItem
from intelligence.observability import evaluate_alerts, save_alert_rule
from intelligence.runs import LeaseLost, collect_step
from workspaces.models import Workspace

from . import test_batch1 as fixtures
from . import test_batch6 as delivery_fixtures


@skipUnlessDBFeature("has_select_for_update")
class LifecycleConcurrencyTests(TransactionTestCase):
    pipeline = delivery_fixtures.BatchSixTests.pipeline
    published = delivery_fixtures.BatchSixTests.published

    def setUp(self):
        fixtures.BatchOneTests.setUpTestData.__func__(self.__class__)

    def test_two_alert_evaluators_create_one_active_incident(self):
        self.pipeline()
        WorkItem.objects.filter(workspace=self.workspace).update(state="failed")
        save_alert_rule(self.admin, self.workspace.pk, "failure", 1, 60, self.admin.pk)
        barrier = threading.Barrier(2, timeout=5)
        failures = []

        def evaluate():
            close_old_connections()
            try:
                barrier.wait()
                evaluate_alerts(self.admin, self.workspace.pk)
            except (DatabaseError, threading.BrokenBarrierError) as error:
                failures.append(str(error))
            finally:
                connection.close()

        threads = [threading.Thread(target=evaluate) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(Incident.objects.filter(state="open").count(), 1)

    def test_deletion_serializes_before_final_delivery(self):
        record, *_prefix, result = self.published()
        intent, _ = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        operation = preview_deletion(self.admin, self.workspace.pk, record.pk)
        locked, sending = threading.Event(), threading.Event()
        failures, states = [], []

        def delete():
            close_old_connections()
            try:
                with transaction.atomic():
                    Workspace.objects.select_for_update(no_key=True).get(
                        pk=self.workspace.pk
                    )
                    locked.set()
                    if not sending.wait(5):
                        raise RuntimeError("Delivery did not start")
                    execute_deletion(self.admin, self.workspace.pk, operation.pk)
            except (DatabaseError, RuntimeError) as error:
                failures.append(str(error))
            finally:
                connection.close()

        def send():
            close_old_connections()
            try:
                if not locked.wait(5):
                    raise RuntimeError("Deletion did not lock")
                sending.set()
                states.append(deliver(intent.pk).state)
            except (DatabaseError, RuntimeError) as error:
                failures.append(str(error))
            finally:
                connection.close()

        threads = [threading.Thread(target=delete), threading.Thread(target=send)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(states, ["blocked"])
        self.assertFalse(SandboxMessage.objects.exists())

    def test_inflight_capture_cannot_restore_deleted_content(self):
        record, *_prefix, _result = self.published()
        run = WorkItem.objects.create(
            workspace=self.workspace,
            source=self.source,
            source_version=self.version,
            actor=self.admin,
            state="running",
            lease_token=uuid.uuid4(),
            lease_until=timezone.now() + timedelta(minutes=1),
            started_at=timezone.now(),
            checkpoint={
                "pending": [{"url": self.source.url, "depth": 0}],
                "visited": [],
            },
            limits={
                "pages": 10,
                "depth": 1,
                "seconds": 120,
                "bytes": 100000,
                "attempts": 3,
                "provider_cost_microunits": 0,
            },
        )
        operation = preview_deletion(self.admin, self.workspace.pk, record.pk)
        fetched, deleted = threading.Event(), threading.Event()
        failures, stopped = [], []

        def fetch(*args, **kwargs):
            fetched.set()
            if not deleted.wait(5):
                raise RuntimeError("Deletion did not finish")
            return {
                "state": "captured",
                "bytes": b"Tender Budget: 12",
                "format": "text/plain",
            }

        def collect():
            close_old_connections()
            try:
                collect_step(WorkItem.objects.get(pk=run.pk), fetch=fetch)
            except LeaseLost:
                stopped.append(True)
            except (DatabaseError, RuntimeError) as error:
                failures.append(str(error))
            finally:
                connection.close()

        original_resolver = socket.getaddrinfo

        def resolver(host, *args, **kwargs):
            if host == "example.com":
                return fixtures.PUBLIC_DNS
            return original_resolver(host, *args, **kwargs)

        with patch("intelligence.urlsafety.socket.getaddrinfo", side_effect=resolver):
            worker = threading.Thread(target=collect)
            worker.start()
            self.assertTrue(fetched.wait(5))
            execute_deletion(self.admin, self.workspace.pk, operation.pk)
            deleted.set()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(stopped, [True])
        self.assertFalse(Capture.objects.filter(run=run).exists())
