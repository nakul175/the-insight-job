import json
import os
from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from intelligence import external_delivery as service
from intelligence import external_http
from intelligence.delivery import store_result
from intelligence.models import ExternalAttempt, ExternalOperation

from . import test_batch1 as fixtures
from . import test_batch6 as delivery_fixtures


class ExternalDeliveryTests(TransactionTestCase):
    pipeline = delivery_fixtures.BatchSixTests.pipeline
    published = delivery_fixtures.BatchSixTests.published

    def setUp(self):
        fixtures.BatchOneTests.setUpTestData.__func__(self.__class__)
        self.env = patch.dict(
            os.environ,
            {
                "TIJ_EXTERNAL_HOSTS": "receiver.example.com",
                "TIJ_SECRET_RECEIVER": "synthetic-token",
                "TIJ_EXTERNAL_BINDINGS": json.dumps(
                    {
                        str(self.workspace.pk): [
                            {
                                "endpoint": "https://receiver.example.com/evidence",
                                "secret_ref": "TIJ_SECRET_RECEIVER",
                            }
                        ]
                    }
                ),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        import socket

        original_resolver = socket.getaddrinfo

        def resolver(host, *args, **kwargs):
            if host in {"example.com", "example.org"} or host.endswith(
                (".example.com", ".example.org")
            ):
                return fixtures.PUBLIC_DNS
            return original_resolver(host, *args, **kwargs)

        self.dns = patch("socket.getaddrinfo", side_effect=resolver)
        self.dns.start()
        self.addCleanup(self.dns.stop)
        *_, self.result = self.published()
        self.intent, _ = store_result(
            self.admin, self.workspace.pk, self.result.pk, self.result.version
        )
        self.target = service.configure(
            self.admin,
            self.workspace.pk,
            self.intent.destination_id,
            "https://receiver.example.com/evidence",
            "TIJ_SECRET_RECEIVER",
            "test-only approval",
            "receiver-v1 tested",
            True,
        )

    def queued(self):
        return service.queue(
            self.admin, self.workspace.pk, self.intent.pk, self.target.pk
        )

    def receipt(self, item, applied=True, deleted=False):
        return {
            "operation_key": item.operation_key,
            "payload_digest": item.payload_digest,
            "receipt": "receiver-1",
            "status": "accepted",
            "applied": applied,
            "deleted": deleted,
        }

    def activate(self):
        service.activate(self.admin, self.workspace.pk, self.target.pk, True)

    def test_activation_is_explicit_and_sandbox_stays_available(self):
        self.assertFalse(self.target.active)
        item = self.queued()
        with patch("intelligence.external_http.request") as transport:
            outcome = service.send(self.admin, self.workspace.pk, item.pk)
        self.assertEqual(outcome.state, "manual_follow_up")
        transport.assert_not_called()
        from intelligence.delivery import deliver

        self.assertEqual(deliver(self.intent.pk).state, "delivered_sandbox")

    def test_receiving_once_outside_transaction_and_bound_digest(self):
        self.activate()
        item = self.queued()

        def receive(target, operation, lookup):
            self.assertFalse(connection.in_atomic_block)
            return self.receipt(operation)

        with patch(
            "intelligence.external_http.request", side_effect=receive
        ) as transport:
            self.assertEqual(
                service.send(self.admin, self.workspace.pk, item.pk).state,
                "acknowledged",
            )
            service.send(self.admin, self.workspace.pk, item.pk)
        self.assertEqual(transport.call_count, 1)
        item.payload["fields"]["budget"]["value"] = 99
        item.save(update_fields=["payload"])
        with self.assertRaises(ValidationError):
            self.queued()

    def test_response_lost_reconciles_one_receiver_record_and_changed_key_refused(self):
        self.activate()
        item = self.queued()
        records = {}

        def receive(target, operation, lookup):
            key = operation.operation_key
            if lookup:
                return records[key]
            if (
                key in records
                and records[key]["payload_digest"] != operation.payload_digest
            ):
                raise ValueError("changed payload")
            records[key] = self.receipt(operation)
            raise OSError("committed response lost")

        with patch(
            "intelligence.external_http.request", side_effect=receive
        ) as transport:
            self.assertEqual(
                service.send(self.admin, self.workspace.pk, item.pk).state, "uncertain"
            )
            with self.assertRaises(ValidationError):
                service.send(self.admin, self.workspace.pk, item.pk)
            self.assertEqual(
                service.send(self.admin, self.workspace.pk, item.pk, lookup=True).state,
                "acknowledged",
            )
        self.assertEqual(len(records), 1)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(
            list(
                ExternalAttempt.objects.filter(operation=item).values_list(
                    "outcome", flat=True
                )
            ),
            ["uncertain", "acknowledged"],
        )

    def test_without_receiver_receipts_no_blind_retry(self):
        self.activate()
        self.target.receipt_contract_ref = ""
        self.target.save()
        item = self.queued()
        with patch("intelligence.external_http.request", side_effect=OSError("lost")):
            service.send(self.admin, self.workspace.pk, item.pk)
        with patch("intelligence.external_http.request") as transport:
            self.assertEqual(
                service.send(self.admin, self.workspace.pk, item.pk, lookup=True).state,
                "manual_follow_up",
            )
            transport.assert_not_called()

    def test_provenance_access_rechecked_and_only_permitted_fields(self):
        item = self.queued()
        package = item.payload
        self.assertEqual(package["result_version"], self.result.version)
        self.assertEqual(package["source"], str(self.source.pk))
        self.assertEqual(package["receiver_approval"], "not_implied")
        self.assertNotIn("https://", json.dumps(package))
        self.activate()
        self.source.systemsourcegrant_set.all().update(active=False)
        with patch("intelligence.external_http.request") as transport:
            self.assertEqual(
                service.send(self.admin, self.workspace.pk, item.pk).state,
                "manual_follow_up",
            )
            transport.assert_not_called()

    def test_unknown_and_conflict_statuses_preserved_and_other_fields_omitted(self):
        # Optional mapped field status is retained even when no mapped value can be emitted.
        from intelligence.models import SystemResult

        fields = self.result.fields.copy()
        fields["private_unmapped"] = {"status": "known", "value": "private"}
        SystemResult.objects.filter(pk=self.result.pk).update(fields=fields)
        item = self.queued()
        self.assertNotIn("private_unmapped", item.payload["fields"])
        self.assertEqual(item.payload["fields"]["budget"]["status"], "known")
        self.assertIn("configuration_version", item.payload)

    def test_withdrawal_minimal_and_acknowledgement_application_separate(self):
        self.activate()
        item = self.queued()
        with patch(
            "intelligence.external_http.request", return_value=self.receipt(item)
        ):
            service.send(self.admin, self.workspace.pk, item.pk)
        notice = service.queue(
            self.admin, self.workspace.pk, self.intent.pk, self.target.pk, item.pk, True
        )
        self.assertNotIn("fields", notice.payload)
        self.assertNotIn("mapped_values", notice.payload)
        with patch(
            "intelligence.external_http.request",
            return_value=self.receipt(notice, False),
        ):
            outcome = service.send(self.admin, self.workspace.pk, notice.pk)
        self.assertEqual(outcome.state, "acknowledged")
        self.assertFalse(outcome.applied)
        self.assertFalse(outcome.deletion_confirmed)
        with patch(
            "intelligence.external_http.request",
            return_value=self.receipt(notice, True, True),
        ):
            outcome = service.send(
                self.admin, self.workspace.pk, notice.pk, lookup=True
            )
        self.assertTrue(outcome.applied)
        self.assertTrue(outcome.deletion_confirmed)

    def test_stale_attempt_recovery_never_resends(self):
        self.activate()
        item = self.queued()
        item.state = "sending"
        item.attempted_at = timezone.now() - timedelta(seconds=40)
        item.save()
        service.recover(
            self.admin,
            self.workspace.pk,
            item.pk,
            "Worker interrupted; inspect receiver",
        )
        with self.assertRaises(ValidationError):
            service.send(self.admin, self.workspace.pk, item.pk)
        with transaction.atomic(), self.assertRaises(ValidationError):
            service.send(self.admin, self.workspace.pk, item.pk, lookup=True)

    def test_deletion_scrubs_outbox_and_keeps_notice_identity(self):
        self.activate()
        item = self.queued()
        with patch(
            "intelligence.external_http.request", return_value=self.receipt(item)
        ):
            service.send(self.admin, self.workspace.pk, item.pk)
        with transaction.atomic():
            service.revoke_source(
                self.admin, self.workspace.pk, intent_ids=[self.intent.pk], scrub=True
            )
        item.refresh_from_db()
        self.assertEqual(item.payload, {})
        self.assertEqual(item.state, "blocked")
        self.assertTrue(
            ExternalOperation.objects.filter(
                previous=item, operation="withdrawal"
            ).exists()
        )

    def test_ui_scoped_and_authenticated(self):
        url = reverse(
            "external-delivery", args=[self.workspace.pk, self.result.system_id]
        )
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url), "external delivery")
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_http_targets_redirects_and_private_dns_refused(self):
        for url in [
            "http://receiver.example.com/evidence",
            "https://evil.example/evidence",
            "https://127.0.0.1/",
            "https://receiver.example.com:444/",
        ]:
            with self.assertRaises(ValidationError):
                external_http.endpoint_url(url)
        self.activate()
        item = self.queued()
        with (
            patch(
                "socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.1", 443))]
            ),
            patch("http.client.HTTPSConnection") as conn,
        ):
            with self.assertRaises(ValidationError):
                external_http.request(self.target, item)
            conn.assert_not_called()
        response = Mock(status=302)
        with patch("http.client.HTTPSConnection") as conn:
            conn.return_value.getresponse.return_value = response
            with self.assertRaises(OSError):
                external_http.request(self.target, item)
        response.read1.assert_not_called()

    def test_http_pins_public_ip_and_receipt_budget(self):
        item = self.queued()
        response = Mock(status=200)
        response.read1.side_effect = [json.dumps(self.receipt(item)).encode(), b""]
        with (
            patch("http.client.HTTPSConnection") as conn,
            patch("socket.create_connection") as connect,
        ):
            conn.return_value.getresponse.return_value = response
            data = external_http.request(self.target, item)
            conn.return_value._create_connection(("receiver.example.com", 443), 5)
            self.assertEqual(connect.call_args.args[0][0], fixtures.PUBLIC_DNS[0][4][0])
            self.assertEqual(data["payload_digest"], item.payload_digest)
        response.read1.side_effect = [b"x" * 8193]
        with patch("http.client.HTTPSConnection") as conn:
            conn.return_value.getresponse.return_value = response
            with self.assertRaises(OSError):
                external_http.request(self.target, item)

    def test_optional_unknown_uncertain_and_conflicting_values_survive(self):
        from intelligence.models import SystemResult

        schema = self.result.schema_version
        schema.fields[0]["required"] = False
        schema.save(update_fields=["fields"])
        contract = self.intent.bundle.mapping.contract
        contract.fields[0]["required"] = False
        contract.save(update_fields=["fields"])
        for status in ["missing", "unknown", "uncertain", "conflict"]:
            fields = {"budget": {"status": status, "value": None, "unit": ""}}
            SystemResult.objects.filter(pk=self.result.pk).update(fields=fields)
            self.intent.result.refresh_from_db()
            self.intent.payload = {}
            package = service.evidence_package(self.admin, self.intent, self.target)
            self.assertEqual(package["fields"]["budget"]["status"], status)
            self.assertIsNone(package["fields"]["budget"]["value"])

    def test_missing_supporting_capture_blocks_export(self):
        import uuid

        from intelligence.models import SystemResult

        fields = self.result.fields.copy()
        fields["budget"]["candidates"] = [
            {"capture": str(uuid.uuid4()), "source": str(self.source.pk), "value": 12}
        ]
        SystemResult.objects.filter(pk=self.result.pk).update(fields=fields)
        with self.assertRaises(PermissionDenied):
            self.queued()

    def test_slow_receipt_deadline_and_dns_budget(self):
        item = self.queued()
        response = Mock(status=200)
        response.read1.return_value = b"x"
        with (
            patch("http.client.HTTPSConnection") as conn,
            patch("intelligence.external_http.time.monotonic", side_effect=[0, 0, 21]),
        ):
            conn.return_value.getresponse.return_value = response
            with self.assertRaises(OSError):
                external_http.request(self.target, item)
        with patch("intelligence.external_http.DNS_POOL") as pool:
            from concurrent.futures import TimeoutError

            pool.submit.return_value.result.side_effect = TimeoutError()
            with self.assertRaises(OSError):
                external_http.request(self.target, item)

    def test_actual_deletion_inventory_tracks_and_scrubs_external_copy(self):
        from intelligence.lifecycle import execute_deletion, preview_deletion

        self.activate()
        item = self.queued()
        with patch(
            "intelligence.external_http.request", return_value=self.receipt(item)
        ):
            service.send(self.admin, self.workspace.pk, item.pk)
        preview = preview_deletion(self.admin, self.workspace.pk, self.result.record_id)
        self.assertTrue(preview.impact["external_operations"])
        execute_deletion(self.admin, self.workspace.pk, preview.pk)
        item.refresh_from_db()
        self.assertEqual(item.payload, {})
        notice = ExternalOperation.objects.get(previous=item)
        self.assertEqual(notice.operation, "withdrawal")

    def test_ui_send_is_autocommit_and_revocation_during_request_is_safe(self):
        self.activate()
        item = self.queued()

        def receive(target, operation, lookup):
            from intelligence.delivery import queue_withdrawals

            self.assertFalse(connection.in_atomic_block)
            queue_withdrawals(self.admin, self.workspace.pk, self.source.pk)
            return self.receipt(operation)

        url = reverse(
            "external-delivery", args=[self.workspace.pk, self.result.system_id]
        )
        self.client.force_login(self.admin)
        with patch("intelligence.external_http.request", side_effect=receive):
            response = self.client.post(url, {"action": "send", "target": str(item.pk)})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.state, "blocked")
        with transaction.atomic():
            service.revoke_source(
                self.admin, self.workspace.pk, intent_ids=[self.intent.pk], scrub=True
            )
        self.assertEqual(ExternalOperation.objects.filter(previous=item).count(), 1)

    def test_recovery_hold_blocks_withdrawal_but_not_receipt_lookup(self):
        from intelligence.models import RecoveryControl

        self.activate()
        item = self.queued()
        with patch(
            "intelligence.external_http.request", return_value=self.receipt(item)
        ):
            service.send(self.admin, self.workspace.pk, item.pk)
        notice = service.queue(
            self.admin, self.workspace.pk, self.intent.pk, self.target.pk, item.pk, True
        )
        RecoveryControl.objects.create(workspace=self.workspace, delivery_hold=True)
        with patch("intelligence.external_http.request") as transport:
            self.assertEqual(
                service.send(self.admin, self.workspace.pk, notice.pk).state,
                "manual_follow_up",
            )
            transport.assert_not_called()

    def test_watchdog_interrupts_stalled_headers(self):
        item = self.queued()
        callbacks = []

        class Timer:
            def __init__(self, seconds, callback):
                callbacks.append(callback)

            def start(self):
                pass

            def cancel(self):
                pass

        with (
            patch("intelligence.external_http.threading.Timer", Timer),
            patch("http.client.HTTPSConnection") as conn,
        ):

            def stalled():
                callbacks[0]()
                self.assertTrue(conn.return_value.sock.shutdown.called)
                raise OSError("closed by watchdog")

            conn.return_value.getresponse.side_effect = stalled
            with self.assertRaises(OSError):
                external_http.request(self.target, item)

    def test_restore_keeps_uncertain_external_writes_unreconciled(self):
        from intelligence.recovery import run_exercise, take_snapshot

        self.activate()
        item = self.queued()
        with patch("intelligence.external_http.request", side_effect=OSError("lost")):
            service.send(self.admin, self.workspace.pk, item.pk)
        snapshot = take_snapshot(self.admin, self.workspace.pk, 60)
        alias = "external_recovery_test"
        with (
            patch.object(type(self), "databases", self.databases | {alias}),
            patch("intelligence.recovery.new_alias", return_value=alias),
        ):
            exercise = run_exercise(
                self.admin,
                self.workspace.pk,
                snapshot.pk,
                {
                    "scope": "External outbox",
                    "failure_mode": "Lost receipt",
                    "recovery_target_minutes": 5,
                },
            )
        self.assertNotEqual(exercise.state, "passed")
        self.assertIn(
            "External write requires receiver reconciliation", str(exercise.report)
        )

    def test_corrected_field_provenance_and_new_version_supersession(self):
        self.activate()
        item = self.queued()
        with patch(
            "intelligence.external_http.request", return_value=self.receipt(item)
        ):
            service.send(self.admin, self.workspace.pk, item.pk)
        result = self.result
        result.fields["budget"]["value"] = 13
        result.fields["budget"]["method"] = "human_correction"
        result.version += 1
        result.save()
        intent, _ = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        correction = service.queue(
            self.admin, self.workspace.pk, intent.pk, self.target.pk, item.pk
        )
        self.assertEqual(
            correction.payload["fields"]["budget"]["method"], "human_correction"
        )
        self.assertEqual(correction.payload["supersedes"], str(item.pk))
        self.assertNotEqual(correction.operation_key, item.operation_key)

    def test_postgres_concurrent_revocation_cancels_claim_without_deadlock(self):
        if not connection.features.has_select_for_update:
            self.skipTest("Requires PostgreSQL row locks")
        import threading

        from django.db import DatabaseError, close_old_connections

        from intelligence.delivery import queue_withdrawals

        self.activate()
        item = self.queued()
        started = threading.Event()
        release = threading.Event()
        failures = []

        def receive(target, operation, lookup):
            started.set()
            if not release.wait(10):
                raise RuntimeError("Revocation failed to finish")
            return self.receipt(operation)

        def worker():
            close_old_connections()
            try:
                service.send(self.admin, self.workspace.pk, item.pk)
            except (
                DatabaseError,
                RuntimeError,
                ValidationError,
                PermissionDenied,
            ) as exc:
                failures.append(type(exc).__name__)
            finally:
                connection.close()

        with patch("intelligence.external_http.request", side_effect=receive):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(started.wait(10))
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '3s'")
                    queue_withdrawals(self.admin, self.workspace.pk, self.source.pk)
            finally:
                release.set()
                thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        item.refresh_from_db()
        self.assertEqual(item.state, "blocked")
        self.assertEqual(ExternalOperation.objects.filter(previous=item).count(), 1)

    def test_another_tenants_credential_reference_is_not_authorized(self):
        with self.assertRaises(ValidationError):
            service.configure(
                self.admin,
                self.workspace.pk,
                self.intent.destination_id,
                "https://receiver.example.com/evidence",
                "TIJ_SECRET_OTHER_TENANT",
                "approval",
            )
        self.target.secret_ref = "TIJ_SECRET_OTHER_TENANT"
        self.target.save()
        with self.assertRaises(ValidationError):
            self.activate()
        item = self.queued()
        with patch("http.client.HTTPSConnection") as conn:
            with self.assertRaises(ValidationError):
                external_http.request(self.target, item)
            conn.assert_not_called()

    def test_unchanged_recapture_does_not_change_pinned_package(self):
        from intelligence.models import Capture

        self.activate()
        item = self.queued()
        original = Capture.objects.filter(
            content_version=self.result.content_version
        ).first()
        duplicate = Capture.objects.get(pk=original.pk)
        duplicate.pk = None
        duplicate.url = original.url + "?later=same-bytes"
        duplicate.save()
        with patch(
            "intelligence.external_http.request", return_value=self.receipt(item)
        ):
            outcome = service.send(self.admin, self.workspace.pk, item.pk)
        self.assertEqual(outcome.state, "acknowledged")
        self.assertEqual(self.queued().pk, item.pk)
