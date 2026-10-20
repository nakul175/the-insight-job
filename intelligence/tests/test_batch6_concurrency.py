"""PostgreSQL serializes final delivery against current source revisions."""

import threading

from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature

from intelligence.delivery import deliver, store_result
from intelligence.models import ContentRecord, SandboxMessage

from . import test_batch1 as fixtures
from . import test_batch6 as delivery_fixtures


@skipUnlessDBFeature("has_select_for_update")
class DeliveryConcurrencyTests(TransactionTestCase):
    pipeline = delivery_fixtures.BatchSixTests.pipeline
    published = delivery_fixtures.BatchSixTests.published

    def setUp(self):
        fixtures.BatchOneTests.setUpTestData.__func__(self.__class__)

    def test_current_revision_lock_prevents_stale_delivery(self):
        record, *_middle, result = self.published()
        intent, _ = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        locked, started = threading.Event(), threading.Event()
        failures, states = [], []

        def revise():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                    row = ContentRecord.objects.select_for_update().get(pk=record.pk)
                    row.version += 1
                    row.save(update_fields=["version"])
                    locked.set()
                    if not started.wait(5):
                        raise RuntimeError("Delivery did not start")
            except (DatabaseError, RuntimeError) as exc:
                failures.append(str(exc))
            finally:
                connection.close()

        def send():
            close_old_connections()
            try:
                if not locked.wait(5):
                    raise RuntimeError("Revision did not acquire lock")
                started.set()
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                    states.append(deliver(intent.pk).state)
            except (DatabaseError, RuntimeError) as exc:
                failures.append(str(exc))
            finally:
                connection.close()

        threads = [threading.Thread(target=revise), threading.Thread(target=send)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(states, ["action_required"])
        self.assertFalse(SandboxMessage.objects.exists())
