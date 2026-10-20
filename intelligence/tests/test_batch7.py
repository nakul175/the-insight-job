from django.core.exceptions import ValidationError
from django.test import TestCase

from intelligence.delivery import store_result
from intelligence.models import DeliveryIntent
from intelligence.replay import enable_replay_delivery, execute_replay, preview_replay
from intelligence.review_workflow import complete_review
from intelligence.routing import claim_review

from . import test_batch1 as fixtures
from . import test_batch6 as delivery_fixtures


class BatchSevenTests(TestCase):
    pipeline = delivery_fixtures.BatchSixTests.pipeline
    published = delivery_fixtures.BatchSixTests.published

    @classmethod
    def setUpTestData(cls):
        fixtures.BatchOneTests.setUpTestData.__func__(cls)

    def test_replay_creates_history_and_requires_explicit_reviewed_delivery(self):
        record, system, _destination, _contract, _mapping, _samples, bundle, old = (
            self.published()
        )
        replay = preview_replay(
            self.admin, self.workspace.pk, system.pk, bundle.pk, [str(record.pk)]
        )
        self.assertEqual(replay.report["eligible_count"], 1)
        self.assertEqual(replay.report["delivery"], "off")
        run = execute_replay(self.admin, self.workspace.pk, replay.pk)
        result = run.results.get()
        self.assertNotEqual(old.pk, result.pk)
        self.assertTrue(result.preview)
        with self.assertRaises(ValidationError):
            store_result(self.admin, self.workspace.pk, result.pk, result.version)
        with self.assertRaises(ValidationError):
            enable_replay_delivery(
                self.admin, self.workspace.pk, run.pk, [str(result.pk)], "Unreviewed"
            )
        result = claim_review(self.admin, self.workspace.pk, result.pk, result.version)
        result = complete_review(
            self.admin, self.workspace.pk, result.pk, result.version, "Reviewed replay"
        )
        enable_replay_delivery(
            self.admin,
            self.workspace.pk,
            run.pk,
            [str(result.pk)],
            "Enable reviewed result",
        )
        self.assertFalse(DeliveryIntent.objects.exists())
        store_result(self.admin, self.workspace.pk, result.pk, result.version)
        old.refresh_from_db()
        self.assertEqual(old.version, 1)
        with self.assertRaises(ValidationError):
            execute_replay(self.admin, self.workspace.pk, replay.pk)

    def test_replay_changed_evidence_is_held_without_silent_version_upgrade(self):
        record, system, _destination, _contract, _mapping, _samples, bundle, _old = (
            self.published()
        )
        replay = preview_replay(
            self.admin, self.workspace.pk, system.pk, bundle.pk, [str(record.pk)]
        )
        record.classification_version += 1
        record.save(update_fields=["classification_version"])
        run = execute_replay(self.admin, self.workspace.pk, replay.pk)
        self.assertEqual(run.state, "completed_with_holds")
        self.assertFalse(run.results.exists())
        self.assertFalse(DeliveryIntent.objects.exists())

    def test_reject_sampling_labels_new_version_and_alerts_without_rule_changes(self):
        from datetime import timedelta

        from django.utils import timezone

        from intelligence.models import Incident
        from intelligence.observability import relabel_reject, sample_rejects

        record, _revision, system = self.pipeline(text="Unrelated\nBudget: 2")
        from intelligence.routing import route_one

        result, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        self.assertEqual(result.decision, "excluded")
        selection = sample_rejects(
            self.admin,
            self.workspace.pk,
            system.pk,
            timezone.now() - timedelta(days=1),
            timezone.now() + timedelta(minutes=1),
            10,
            0,
        )
        version = relabel_reject(
            self.admin,
            self.workspace.pk,
            selection.pk,
            result.pk,
            {"budget": 2},
            "include",
            "fail",
        )
        self.assertEqual(version.samples.count(), 1)
        self.assertEqual(Incident.objects.get().category, "false_exclusion")
        system.refresh_from_db()
        self.assertEqual(system.config_version, 2)
        with self.assertRaises(ValidationError):
            relabel_reject(
                self.admin,
                self.workspace.pk,
                selection.pk,
                result.pk,
                {"budget": 2},
                "include",
                "fail",
            )

    def test_redacted_audit_and_monitor_reconcile_scoped_durable_runs(self):
        from datetime import timedelta

        from django.utils import timezone

        from intelligence.observability import monitor, scoped_audit
        from intelligence.services import audit

        record, _revision, _system = self.pipeline()
        audit(
            self.admin,
            self.workspace.pk,
            "test.correction",
            record.pk,
            {"version": 2, "reason": "SECRET", "payload": {"private": "SECRET"}},
        )
        start, end = (
            timezone.now() - timedelta(days=1),
            timezone.now() + timedelta(minutes=1),
        )
        events = scoped_audit(self.admin, self.workspace.pk, start, end)
        self.assertNotIn("SECRET", str(events))
        self.assertEqual(events[-1]["versions"], {"version": 2})
        metrics = monitor(self.admin, self.workspace.pk, start, end)
        self.assertEqual(sum(metrics["counts"].values()), len(metrics["runs"]))
        self.assertEqual(metrics["total_matching"], len(metrics["runs"]))
        self.assertIn("Unavailable", metrics["provider_cost"])

    def test_alert_threshold_deduplicates_and_ack_resolution_is_audited(self):
        from datetime import timedelta

        from django.utils import timezone

        from intelligence.models import Incident, WorkItem
        from intelligence.observability import (
            evaluate_alerts,
            incident_action,
            save_alert_rule,
        )

        self.pipeline()
        WorkItem.objects.filter(workspace=self.workspace).update(
            state="failed", created_at=timezone.now() - timedelta(minutes=2)
        )
        rule = save_alert_rule(
            self.admin, self.workspace.pk, "failure", 1, 60, self.reviewer.pk
        )
        self.assertEqual(len(evaluate_alerts(self.admin, self.workspace.pk)), 1)
        self.assertEqual(evaluate_alerts(self.admin, self.workspace.pk), [])
        incident = Incident.objects.get(rule=rule)
        incident_action(self.reviewer, self.workspace.pk, incident.pk, "acknowledge")
        incident_action(self.admin, self.workspace.pk, incident.pk, "resolve")
        incident.refresh_from_db()
        self.assertEqual(incident.acknowledged_by_id, self.reviewer.pk)
        self.assertEqual(incident.resolved_by_id, self.admin.pk)
        self.assertIsNotNone(incident.resolved_at)
        self.assertEqual(evaluate_alerts(self.admin, self.workspace.pk), [])
        WorkItem.objects.filter(workspace=self.workspace).update(state="completed")
        evaluate_alerts(self.admin, self.workspace.pk)
        WorkItem.objects.filter(workspace=self.workspace).update(state="failed")
        self.assertEqual(len(evaluate_alerts(self.admin, self.workspace.pk)), 1)

    def test_deletion_scrubs_copies_queues_withdrawal_and_prevents_resurrection(self):
        from intelligence.content import materialize
        from intelligence.delivery import deliver
        from intelligence.lifecycle import execute_deletion, preview_deletion
        from intelligence.models import Capture, ContentAnalysis, SandboxMessage
        from intelligence.permissions import records_for

        (
            record,
            _system,
            _destination,
            _contract,
            _mapping,
            _samples,
            _bundle,
            result,
        ) = self.published()
        intent, _ = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        deliver(intent.pk)
        capture = Capture.objects.get(record=record)
        old_url = capture.url
        operation = preview_deletion(self.admin, self.workspace.pk, record.pk)
        self.assertEqual(len(operation.impact["captures"]), 1)
        completed = execute_deletion(self.admin, self.workspace.pk, operation.pk)
        self.assertEqual(completed.state, "application_removed_residuals_pending")
        self.assertEqual(completed.impact["backup_scope"], "unknown")
        capture.refresh_from_db()
        self.assertEqual(bytes(capture.original), b"")
        self.assertEqual(
            ContentAnalysis.objects.get(content_version__record=record).body, ""
        )
        self.assertFalse(
            records_for(self.admin, self.workspace.pk).filter(pk=record.pk).exists()
        )
        withdrawal = DeliveryIntent.objects.get(operation="withdraw")
        deliver(withdrawal.pk)
        self.assertTrue(SandboxMessage.objects.get().withdrawn)
        capture.url = old_url
        with self.assertRaisesMessage(ValidationError, "tombstoned"):
            materialize(
                capture,
                {"status": "parsed", "segments": [{"text": "Tender Budget: 12"}]},
            )

    def test_deletion_of_uncertain_write_without_withdrawal_keeps_residual_truth(self):
        from intelligence.delivery import deliver, local_receiver
        from intelligence.lifecycle import execute_deletion, preview_deletion
        from intelligence.models import SandboxMessage

        record, *_prefix, result = self.published(safe=False, withdrawal=False)
        intent, _ = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )

        def lost_ack(operation):
            local_receiver(operation)
            raise TimeoutError()

        self.assertEqual(deliver(intent.pk, transport=lost_ack).state, "ambiguous")
        operation = preview_deletion(
            self.admin, self.workspace.pk, record.pk, backup_scope="none"
        )
        execute_deletion(self.admin, self.workspace.pk, operation.pk)
        withdrawal = DeliveryIntent.objects.get(operation="withdraw")
        self.assertEqual(withdrawal.state, "manual_follow_up")
        self.assertFalse(SandboxMessage.objects.get().withdrawn)
        intent.refresh_from_db()
        self.assertEqual(intent.state, "blocked")
        self.assertEqual(intent.payload, {})

    def test_deletion_impact_drift_requires_new_preview(self):
        from intelligence.lifecycle import execute_deletion, preview_deletion

        record, *_prefix, result = self.published()
        operation = preview_deletion(self.admin, self.workspace.pk, record.pk)
        store_result(self.admin, self.workspace.pk, result.pk, result.version)
        with self.assertRaisesMessage(ValidationError, "impact changed"):
            execute_deletion(self.admin, self.workspace.pk, operation.pk)
        record.refresh_from_db()
        self.assertNotEqual(record.status, "deleted")

    def test_isolated_restore_exercise_and_explicit_delivery_resume(self):
        from intelligence.recovery import (
            delivery_held,
            resume_delivery,
            run_exercise,
            take_snapshot,
        )

        *_prefix, result = self.published()
        snapshot = take_snapshot(self.admin, self.workspace.pk, 60)
        self.assertNotIn('"password": "test-password"', str(snapshot.payload))
        from unittest.mock import patch

        alias = "recovery_synthetic_test"
        # Explicitly authorise only the fresh isolated target connection for this exercise.
        with (
            patch.object(type(self), "databases", self.databases | {alias}),
            patch("intelligence.recovery.new_alias", return_value=alias),
        ):
            exercise = run_exercise(
                self.admin,
                self.workspace.pk,
                snapshot.pk,
                {
                    "scope": "Retained synthetic content and outbox",
                    "failure_mode": "Local database loss",
                    "recovery_target_minutes": 5,
                },
            )
        self.assertEqual(exercise.state, "reconciled", exercise.report)
        self.assertTrue(exercise.report["isolated_restore_executed"])
        self.assertGreater(exercise.report["restored_objects"], 0)
        self.assertTrue(delivery_held(self.workspace.pk))
        with self.assertRaisesMessage(ValidationError, "Recovery"):
            store_result(self.admin, self.workspace.pk, result.pk, result.version)
        resume_delivery(self.admin, self.workspace.pk, exercise.pk)
        self.assertFalse(delivery_held(self.workspace.pk))

    def exercise(self, snapshot):
        from unittest.mock import patch

        from intelligence.recovery import run_exercise

        alias = "recovery_synthetic_extra"
        with (
            patch.object(type(self), "databases", self.databases | {alias}),
            patch("intelligence.recovery.new_alias", return_value=alias),
        ):
            return run_exercise(
                self.admin,
                self.workspace.pk,
                snapshot.pk,
                {
                    "scope": "Synthetic retained content and outbox",
                    "failure_mode": "Missing data/uncertain acknowledgement",
                    "recovery_target_minutes": 5,
                },
            )

    def test_recovery_missing_receipt_and_unsupported_deleted_copy_never_clear_hold(
        self,
    ):
        from intelligence.delivery import deliver
        from intelligence.lifecycle import execute_deletion, preview_deletion
        from intelligence.models import SandboxReceipt
        from intelligence.recovery import resume_delivery, take_snapshot

        record, *_prefix, result = self.published(withdrawal=False)
        intent, _ = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        deliver(intent.pk)
        SandboxReceipt.objects.all().delete()
        snapshot = take_snapshot(self.admin, self.workspace.pk, 60)
        failed = self.exercise(snapshot)
        self.assertEqual(failed.state, "action_required")
        self.assertIn("Missing local receipt", str(failed.report))
        with self.assertRaises(ValidationError):
            resume_delivery(self.admin, self.workspace.pk, failed.pk)
        operation = preview_deletion(self.admin, self.workspace.pk, record.pk)
        execute_deletion(self.admin, self.workspace.pk, operation.pk)
        snapshot = take_snapshot(self.admin, self.workspace.pk, 60)
        failed = self.exercise(snapshot)
        self.assertEqual(failed.state, "action_required", failed.report)
        self.assertNotIn("DeserializationError", str(failed.report))
        self.assertIn("manual reconciliation", str(failed.report))

    def test_permission_changed_draft_is_removed_and_new_write_is_rejected(self):
        import json

        from django.urls import reverse

        from intelligence.models import SourceGrant, UserDraft

        self.client.force_login(
            self.reviewer, backend="django.contrib.auth.backends.ModelBackend"
        )
        record, _revision, _system = self.pipeline()
        url = reverse(
            "scoped-draft", args=[self.workspace.pk, "record", record.pk, "form-0"]
        )
        self.assertEqual(
            self.client.post(
                url,
                json.dumps({"reason": ["Unsaved correction"]}),
                content_type="application/json",
            ).status_code,
            200,
        )
        self.assertEqual(UserDraft.objects.count(), 1)
        SourceGrant.objects.filter(membership=self.rm, source=self.source).delete()
        self.assertEqual(
            self.client.post(
                url,
                json.dumps({"reason": ["New inaccessible draft"]}),
                content_type="application/json",
            ).status_code,
            403,
        )
        self.assertFalse(UserDraft.objects.exists())

    def test_guided_source_activation_confirms_exact_tested_scope(self):
        from django.urls import reverse

        from intelligence.models import SourceActivation, SourceTest

        self.client.force_login(
            self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )
        test = SourceTest.objects.create(
            workspace=self.workspace,
            source=self.source,
            source_version=self.version,
            actor=self.admin,
            state="passed",
        )
        url = reverse("source-setup", args=[self.workspace.pk, self.source.pk])
        self.assertContains(self.client.get(url), "Confirm this exact tested scope")
        missing = self.client.post(
            url, {"expected_version": 1, "test_id": str(test.pk)}
        )
        self.assertContains(missing, "This field is required")
        self.assertFalse(SourceActivation.objects.exists())
        response = self.client.post(
            url, {"expected_version": 1, "test_id": str(test.pk), "confirm_scope": "on"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SourceActivation.objects.get().scope["source_version"], 1)
        self.source.refresh_from_db()
        self.assertEqual(self.source.state, "active")

    def test_deletion_inventory_includes_and_removes_record_and_result_drafts(self):
        from intelligence.lifecycle import execute_deletion, preview_deletion
        from intelligence.models import UserDraft

        record, *_prefix, result = self.published()
        for kind, scope in [("record", record.pk), ("result", result.pk)]:
            UserDraft.objects.create(
                workspace=self.workspace,
                actor=self.admin,
                scope_kind=kind,
                scope_id=scope,
                form_key="form-0",
                payload={"reason": ["Protected copied content"]},
            )
        operation = preview_deletion(self.admin, self.workspace.pk, record.pk)
        self.assertEqual(len(operation.impact["drafts"]), 2)
        execute_deletion(self.admin, self.workspace.pk, operation.pk)
        self.assertFalse(UserDraft.objects.exists())

    def test_receipt_digest_mismatch_and_expired_snapshot_fail_closed(self):
        from datetime import timedelta
        from io import StringIO

        from django.core.management import call_command
        from django.utils import timezone

        from intelligence.delivery import deliver
        from intelligence.models import SandboxReceipt
        from intelligence.recovery import take_snapshot

        *_prefix, result = self.published()
        intent, _ = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        deliver(intent.pk)
        SandboxReceipt.objects.update(payload_digest="0" * 64)
        snapshot = take_snapshot(self.admin, self.workspace.pk, 60)
        exercise = self.exercise(snapshot)
        self.assertEqual(exercise.state, "action_required")
        self.assertIn("Missing local receipt", str(exercise.report))
        snapshot.expires_at = timezone.now() - timedelta(minutes=1)
        snapshot.save(update_fields=["expires_at"])
        call_command(
            "run_maintenance",
            workspace=str(self.workspace.pk),
            actor=self.admin.pk,
            stdout=StringIO(),
        )
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.payload, [])
        self.assertEqual(snapshot.state, "expired")

    def test_enrichment_deletion_withdraws_dependent_result_without_deleting_parent_original(
        self,
    ):
        from intelligence.content import materialize
        from intelligence.delivery import deliver
        from intelligence.lifecycle import execute_deletion, preview_deletion
        from intelligence.models import ParseResult, SandboxMessage
        from intelligence.review_workflow import apply_enrichment, complete_review

        record, *_prefix, result = self.published()
        result = claim_review(self.admin, self.workspace.pk, result.pk, result.version)
        attempt, capture = delivery_fixtures.BatchSixTests.enrichment_capture(
            self, result, "Budget: 12"
        )
        parsed = ParseResult.objects.get(capture=capture)
        evidence_record, _revision, _created = materialize(
            capture,
            {"status": parsed.status, "segments": parsed.segments, "reason": ""},
        )
        merged, _ = apply_enrichment(
            self.admin, self.workspace.pk, result.pk, result.version, attempt.pk
        )
        merged = complete_review(
            self.admin,
            self.workspace.pk,
            result.pk,
            merged.version,
            "Confirmed both permitted sources",
        )
        intent, _ = store_result(
            self.admin, self.workspace.pk, merged.pk, merged.version
        )
        deliver(intent.pk)
        operation = preview_deletion(self.admin, self.workspace.pk, evidence_record.pk)
        self.assertIn(str(result.pk), operation.impact["results"])
        execute_deletion(self.admin, self.workspace.pk, operation.pk)
        record.refresh_from_db()
        result.refresh_from_db()
        self.assertNotEqual(record.status, "deleted")
        self.assertIn("Tender", record.body)
        self.assertEqual(result.decision, "blocked")
        self.assertEqual(result.fields, {})
        withdrawal = DeliveryIntent.objects.get(operation="withdraw")
        deliver(withdrawal.pk)
        self.assertTrue(SandboxMessage.objects.get().withdrawn)

    def test_adding_second_reject_preserves_previous_evaluation_members(self):
        from datetime import timedelta

        from django.utils import timezone

        from intelligence.observability import relabel_reject, sample_rejects
        from intelligence.routing import route_one

        from . import test_batch3 as content_fixtures

        record, _revision, system = self.pipeline(text="Unrelated Budget: 2")
        result, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        other, _revision, _cap, _created = content_fixtures.BatchThreeTests.captured(
            self, text="Another unrelated Budget: 3", url=self.source.url + "/other"
        )
        second, _ = route_one(self.admin, self.workspace.pk, other.pk, system.pk)
        selection = sample_rejects(
            self.admin,
            self.workspace.pk,
            system.pk,
            timezone.now() - timedelta(days=1),
            timezone.now() + timedelta(minutes=1),
            10,
            10,
        )
        first = relabel_reject(
            self.admin,
            self.workspace.pk,
            selection.pk,
            result.pk,
            {"budget": None},
            "exclude",
            "review",
        )
        newer = relabel_reject(
            self.admin,
            self.workspace.pk,
            selection.pk,
            second.pk,
            {"budget": None},
            "exclude",
            "review",
            first.sample_set_id,
            1,
        )
        self.assertEqual(first.samples.count(), 1)
        self.assertEqual(newer.samples.count(), 2)
        self.assertEqual(
            newer.samples.get(content_version=result.content_version).analysis_id,
            result.analysis_id,
        )

    def test_recovery_invalidates_old_leases_and_reapplies_later_tombstones(self):
        import uuid
        from datetime import timedelta

        from django.utils import timezone

        from intelligence.lifecycle import url_digest
        from intelligence.models import ContentTombstone, WorkItem
        from intelligence.recovery import take_snapshot

        record, *_prefix, _result = self.published()
        WorkItem.objects.filter(source=self.source).update(
            state="running",
            lease_token=uuid.uuid4(),
            lease_until=timezone.now() + timedelta(minutes=1),
        )
        snapshot = take_snapshot(self.admin, self.workspace.pk, 60)
        # Simulate an older retained backup plus a newer durable deletion tombstone.
        ContentTombstone.objects.create(
            workspace=self.workspace,
            source=self.source,
            record=record,
            url_hash=url_digest(record.canonical_url),
        )
        exercise = self.exercise(snapshot)
        self.assertEqual(exercise.state, "reconciled", exercise.report)
        self.assertEqual(exercise.report["tombstones_reapplied"], 1)
        self.assertGreater(exercise.report["restored_leases_invalidated"], 0)

    def test_recovery_detects_corrupted_original_and_current_message_payload(self):
        from intelligence.delivery import deliver
        from intelligence.models import Capture, SandboxMessage
        from intelligence.recovery import take_snapshot

        *_prefix, result = self.published()
        intent, _ = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        deliver(intent.pk)
        Capture.objects.filter(record=result.record).update(
            original=b"Corrupted original"
        )
        SandboxMessage.objects.update(payload={"corrupted": "wrong data"})
        exercise = self.exercise(take_snapshot(self.admin, self.workspace.pk, 60))
        self.assertEqual(exercise.state, "action_required")
        self.assertIn("Original digest mismatch", str(exercise.report))
        self.assertIn("message payload mismatch", str(exercise.report))
