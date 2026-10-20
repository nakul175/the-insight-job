from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from intelligence import destinations
from intelligence.delivery import (
    deliver,
    local_receiver,
    retry_delivery,
    save_contract,
    save_mapping,
    store_result,
)
from intelligence.models import (
    DeliveryIntent,
    ReviewChange,
    ReviewTask,
    SandboxMessage,
    SandboxReceipt,
)
from intelligence.publication import (
    compare_configurations,
    publish_bundle,
    rollback_bundle,
)
from intelligence.review_workflow import complete_review, follow_up, reopen_review
from intelligence.routing import claim_review, configure_qualification, route_one
from intelligence.schema_lifecycle import save_samples

from . import test_batch1 as fixtures
from . import test_batch5 as workflow_fixtures
from .test_batch3 import field


class BatchSixTests(TestCase):
    pipeline = workflow_fixtures.BatchFiveTests.pipeline

    @classmethod
    def setUpTestData(cls):
        fixtures.BatchOneTests.setUpTestData.__func__(cls)

    def published(self, safe=True, withdrawal=True, policy="upsert"):
        record, revision, system = self.pipeline()
        schema = system.contentschema_set.get()
        definition = schema.versions.get(number=1)
        destination = destinations.register_destination(
            self.admin,
            self.workspace.pk,
            system.pk,
            "https://receiver.example.com/local-contract",
        )
        destinations.test_destination(
            self.admin, self.workspace.pk, system.pk, destination.pk
        )
        configure_qualification(
            self.admin,
            self.workspace.pk,
            system.pk,
            2,
            schema.pk,
            workflow_fixtures.rules(),
            destination.pk,
        )
        contract = save_contract(
            self.admin,
            self.workspace.pk,
            system.pk,
            destination.pk,
            0,
            [field()],
            safe,
            withdrawal,
            policy,
        )
        mapping = save_mapping(
            self.admin,
            self.workspace.pk,
            system.pk,
            definition.pk,
            contract.pk,
            [{"source": "budget", "target": "budget", "transform": "identity"}],
        )
        samples = save_samples(
            self.admin,
            self.workspace.pk,
            schema.pk,
            "Pinned funding",
            [
                {
                    "content_version": str(revision.pk),
                    "fields": {"budget": 12},
                    "routing": "include",
                    "qualification": "pass",
                }
            ],
        )
        comparison = compare_configurations(
            self.admin, self.workspace.pk, system.pk, 3, 3, samples.pk
        )
        self.assertTrue(comparison.passed)
        bundle = publish_bundle(
            self.admin, self.workspace.pk, system.pk, 3, mapping.pk, comparison.pk
        )
        result, _created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        self.assertFalse(result.preview)
        self.assertEqual(result.bundle_id, bundle.pk)
        return record, system, destination, contract, mapping, samples, bundle, result

    def test_publication_atomic_storage_local_receiver_and_stable_identity(self):
        (
            _record,
            _system,
            _destination,
            _contract,
            _mapping,
            _samples,
            bundle,
            result,
        ) = self.published()
        intent, created = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        self.assertTrue(created)
        repeated, created = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        self.assertFalse(created)
        self.assertEqual(repeated.pk, intent.pk)
        result.refresh_from_db()
        self.assertEqual(result.stored_version, result.version)
        self.assertEqual(intent.snapshot["bundle"], str(bundle.pk))
        sent = deliver(intent.pk)
        self.assertEqual(sent.state, "delivered_sandbox")
        self.assertEqual(SandboxMessage.objects.get().payload["budget"], 12)
        self.assertEqual(deliver(intent.pk).attempts, 1)
        self.assertEqual(SandboxReceipt.objects.count(), 1)

    def test_timeout_after_write_reconciles_or_holds_unsafe_dedup(self):
        *_prefix, result = self.published()
        intent, _created = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )

        def after_write(operation):
            local_receiver(operation)
            raise TimeoutError("private response must not be persisted")

        reconciled = deliver(intent.pk, transport=after_write)
        self.assertEqual(reconciled.state, "delivered_sandbox")
        self.assertEqual(reconciled.response["status"], "reconciled")
        self.assertNotIn("private", reconciled.reason)
        self.assertEqual(SandboxReceipt.objects.count(), 1)

    def test_ambiguous_without_safe_dedup_never_blindly_retries(self):
        *_prefix, result = self.published(safe=False)
        intent, _created = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )

        def after_write(operation):
            local_receiver(operation)
            raise TimeoutError("secret")

        ambiguous = deliver(intent.pk, transport=after_write)
        self.assertEqual(ambiguous.state, "ambiguous")
        self.assertEqual(deliver(intent.pk).attempts, 1)
        with self.assertRaises(ValidationError):
            retry_delivery(self.admin, self.workspace.pk, intent.pk, "Try again")

    def test_retry_budget_and_source_permission_recheck(self):
        *_prefix, result = self.published()
        intent, _created = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )

        def unavailable(_intent):
            raise OSError("Authorization: secret")

        for _ in range(3):
            DeliveryIntent.objects.filter(pk=intent.pk).update(
                next_attempt=timezone.now() - timedelta(seconds=1)
            )
            failed = deliver(intent.pk, transport=unavailable)
        self.assertEqual(failed.state, "action_required")
        self.assertEqual(failed.attempts, 3)
        self.assertNotIn("secret", str(failed.response) + failed.reason)
        retried = retry_delivery(
            self.admin,
            self.workspace.pk,
            intent.pk,
            "Receiver restored",
            attempt_budget=4,
        )
        self.assertEqual(retried.idempotency_key, intent.idempotency_key)
        from intelligence.models import SystemSourceGrant

        SystemSourceGrant.objects.filter(
            system=result.system, source=self.source
        ).update(active=False)
        blocked = deliver(intent.pk)
        self.assertEqual(blocked.state, "action_required")
        self.assertFalse(SandboxMessage.objects.exists())

    def test_destination_schema_change_blocks_sendable_intent(self):
        _record, system, destination, _contract, _mapping, _samples, _bundle, result = (
            self.published()
        )
        save_contract(
            self.admin,
            self.workspace.pk,
            system.pk,
            destination.pk,
            1,
            [field(), field(key="extra", kind="text")],
        )
        with self.assertRaisesMessage(ValidationError, "schema changed"):
            store_result(self.admin, self.workspace.pk, result.pk, result.version)
        self.assertFalse(DeliveryIntent.objects.exists())
        result.refresh_from_db()
        self.assertIsNone(result.stored_version)

    def test_mapping_rejects_unmapped_required_and_incompatible_target(self):
        _record, system, _destination, contract, mapping, _samples, _bundle, _result = (
            self.published()
        )
        with self.assertRaisesMessage(ValidationError, "text transformation"):
            save_mapping(
                self.admin,
                self.workspace.pk,
                system.pk,
                mapping.schema_version_id,
                contract.pk,
                [{"source": "budget", "target": "budget", "transform": "text"}],
            )

    def test_defer_escalate_information_return_and_immutable_completion(self):
        record, _revision, system = self.pipeline(text="Tender\nBudget: 2")
        result, _created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        claimed = claim_review(self.reviewer, self.workspace.pk, result.pk, 1)
        deferred = follow_up(
            self.reviewer,
            self.workspace.pk,
            result.pk,
            claimed.version,
            "escalate",
            self.admin,
            timezone.now() + timedelta(days=1),
            "Owner decision required",
        )
        self.assertEqual(deferred.review_state, "escalated")
        self.assertEqual(ReviewTask.objects.get().recipient_id, self.admin.pk)
        reopened = reopen_review(
            self.admin,
            self.workspace.pk,
            result.pk,
            deferred.version,
            "New information received",
        )
        self.assertEqual(reopened.review_state, "open")
        self.assertEqual(reopened.qualification["decision"], "fail")
        completed = complete_review(
            self.admin,
            self.workspace.pk,
            result.pk,
            reopened.version,
            "Reviewed final failure",
        )
        self.assertEqual(completed.review_state, "resolved")
        self.assertEqual(
            ReviewChange.objects.filter(result=result, kind="completed").count(), 1
        )
        with self.assertRaises(ValidationError):
            complete_review(
                self.admin,
                self.workspace.pk,
                result.pk,
                completed.version,
                "Duplicate approval",
            )
        with self.assertRaises(ValidationError):
            claim_review(self.reviewer, self.workspace.pk, result.pk, completed.version)

    def test_new_bundles_pin_inflight_and_rollback_does_not_rewrite_history(self):
        _record, system, _destination, _contract, mapping, samples, old, result = (
            self.published()
        )
        from intelligence.systems import current_config, save_configuration

        system.refresh_from_db()
        save_configuration(
            self.admin, self.workspace.pk, system.pk, 3, current_config(system)
        )
        before, created = route_one(
            self.admin, self.workspace.pk, result.record_id, system.pk
        )
        self.assertFalse(created)
        self.assertEqual(before.bundle_id, old.pk)
        comparison = compare_configurations(
            self.admin, self.workspace.pk, system.pk, 3, 4, samples.pk
        )
        newer = publish_bundle(
            self.admin, self.workspace.pk, system.pk, 4, mapping.pk, comparison.pk
        )
        next_result, created = route_one(
            self.admin, self.workspace.pk, result.record_id, system.pk
        )
        self.assertTrue(created)
        self.assertEqual(next_result.bundle_id, newer.pk)
        result.refresh_from_db()
        self.assertEqual(result.bundle_id, old.pk)
        rollback_bundle(
            self.admin,
            self.workspace.pk,
            system.pk,
            old.pk,
            "Restore tested prior bundle",
        )
        self.assertEqual(
            route_one(self.admin, self.workspace.pk, result.record_id, system.pk)[0].pk,
            result.pk,
        )

    def test_source_revoke_queues_supported_withdrawal_and_records_local_receipt(self):
        from intelligence.services import revoke_source

        *_prefix, result = self.published()
        intent, _created = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        deliver(intent.pk)
        revoke_source(self.admin, self.workspace.pk, self.source.pk, result.system_id)
        withdrawal = DeliveryIntent.objects.get(operation="withdraw")
        self.assertEqual(withdrawal.external_key, intent.external_key)
        self.assertEqual(deliver(withdrawal.pk).state, "delivered_sandbox")
        self.assertTrue(SandboxMessage.objects.get().withdrawn)
        self.assertEqual(SandboxMessage.objects.get().payload, {})
        self.assertEqual(SandboxReceipt.objects.filter(operation="withdraw").count(), 1)

    def test_unsupported_withdrawal_reports_residual_copy_without_deletion_claim(self):
        from intelligence.services import revoke_source

        *_prefix, result = self.published(withdrawal=False)
        intent, _created = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        deliver(intent.pk)
        revoke_source(self.admin, self.workspace.pk, self.source.pk, result.system_id)
        withdrawal = DeliveryIntent.objects.get(operation="withdraw")
        self.assertEqual(withdrawal.state, "manual_follow_up")
        self.assertFalse(SandboxMessage.objects.get().withdrawn)
        self.assertFalse(SandboxReceipt.objects.filter(operation="withdraw").exists())

    def test_failed_and_empty_comparisons_cannot_pass_publication(self):
        (
            _record,
            system,
            _destination,
            _contract,
            mapping,
            samples,
            original,
            _result,
        ) = self.published()
        from intelligence.systems import current_config, save_configuration

        system.refresh_from_db()
        config = current_config(system)
        config["relevance"] = {
            "all": ["Grant"],
            "none": [],
            "unknown": "review",
            "examples": [{"text": "Grant", "expected": "include"}],
        }
        save_configuration(self.admin, self.workspace.pk, system.pk, 3, config)
        comparison = compare_configurations(
            self.admin, self.workspace.pk, system.pk, 3, 4, samples.pk
        )
        self.assertFalse(comparison.passed)
        self.assertTrue(comparison.results[0]["false_exclusion"])
        self.assertTrue(comparison.results[0]["decision_changed"])
        with self.assertRaisesMessage(ValidationError, "passing"):
            publish_bundle(
                self.admin, self.workspace.pk, system.pk, 4, mapping.pk, comparison.pk
            )
        system.refresh_from_db()
        self.assertEqual(system.active_bundle_id, original.pk)
        samples.samples.all().delete()
        with self.assertRaises(PermissionDenied):
            compare_configurations(
                self.admin, self.workspace.pk, system.pk, 3, 4, samples.pk
            )

    def test_unmapped_required_target_reports_specific_field(self):
        (
            _record,
            system,
            destination,
            _old_contract,
            mapping,
            _samples,
            _bundle,
            _result,
        ) = self.published()
        contract = save_contract(
            self.admin,
            self.workspace.pk,
            system.pk,
            destination.pk,
            1,
            [field(), field(key="required_name", kind="text")],
        )
        with self.assertRaisesMessage(ValidationError, "required_name"):
            save_mapping(
                self.admin,
                self.workspace.pk,
                system.pk,
                mapping.schema_version_id,
                contract.pk,
                [{"source": "budget", "target": "budget", "transform": "identity"}],
            )

    def test_quarantined_base_cannot_be_requalified_or_stored(self):
        from intelligence.models import EnrichmentAttempt, EnrichmentPolicy
        from intelligence.review_workflow import apply_enrichment
        from intelligence.routing import override_decision

        *_prefix, result = self.published()
        base = result.analysis or result.content_version
        type(base).objects.filter(pk=base.pk).update(status="quarantined")
        result = claim_review(self.admin, self.workspace.pk, result.pk, result.version)
        policy = EnrichmentPolicy.objects.create(
            system=result.system, number=1, actor=self.admin
        )
        attempt = EnrichmentAttempt.objects.create(
            workspace=self.workspace,
            policy=policy,
            record=result.record,
            actor=self.admin,
            state="completed",
        )
        with self.assertRaisesMessage(ValidationError, "Immutable base"):
            apply_enrichment(
                self.admin, self.workspace.pk, result.pk, result.version, attempt.pk
            )
        with self.assertRaises(ValidationError):
            override_decision(
                self.admin,
                self.workspace.pk,
                result.pk,
                result.version,
                "pass",
                "Business approval",
            )
        with self.assertRaisesMessage(ValidationError, "Immutable base"):
            store_result(self.admin, self.workspace.pk, result.pk, result.version)
        self.assertFalse(DeliveryIntent.objects.exists())

    def test_withdrawal_follows_stored_enrichment_source_even_when_parent_differs(self):
        from uuid import uuid4

        from intelligence.delivery import queue_withdrawals

        *_prefix, result = self.published()
        intent, _ = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        deliver(intent.pk)
        enrichment_source = uuid4()
        intent.snapshot["fields"]["budget"]["candidates"].append(
            {"source": str(enrichment_source), "value": 12}
        )
        intent.save(update_fields=["snapshot"])
        queued = queue_withdrawals(self.admin, self.workspace.pk, enrichment_source)
        self.assertEqual(len(queued), 1)
        self.assertEqual(deliver(queued[0].pk).state, "delivered_sandbox")
        self.assertTrue(SandboxMessage.objects.get().withdrawn)

    def test_safe_receiver_applies_repaired_payload_under_same_logical_identity(self):
        *_prefix, result = self.published()
        intent, _ = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        local_receiver(intent)
        intent.payload = {"budget": 14}
        local_receiver(intent)
        self.assertEqual(SandboxMessage.objects.get().payload, {"budget": 14})
        self.assertEqual(SandboxReceipt.objects.count(), 2)
        local_receiver(intent)
        self.assertEqual(SandboxReceipt.objects.count(), 2)

    def test_connection_reset_after_write_is_ambiguous_and_revocation_withdraws(self):
        from django.urls import reverse

        from intelligence.services import revoke_source

        *_prefix, result = self.published(safe=False)
        intent, _ = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )

        def reset_after_write(operation):
            local_receiver(operation)
            raise ConnectionResetError("private response lost")

        uncertain = deliver(intent.pk, transport=reset_after_write)
        self.assertEqual(uncertain.state, "ambiguous")
        revoke_source(self.admin, self.workspace.pk, self.source.pk, result.system_id)
        withdrawal = DeliveryIntent.objects.get(operation="withdraw")
        self.client.force_login(
            self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )
        response = self.client.get(
            reverse("system-release", args=[self.workspace.pk, result.system_id])
        )
        self.assertContains(response, str(withdrawal.pk))
        self.assertEqual(deliver(withdrawal.pk).state, "delivered_sandbox")
        self.assertTrue(SandboxMessage.objects.get().withdrawn)

    def enrichment_capture(self, result, text, state="completed"):
        import hashlib

        from intelligence.models import (
            Capture,
            EnrichmentAttempt,
            EnrichmentPolicy,
            ParseResult,
            WorkItem,
        )

        policy = EnrichmentPolicy.objects.create(
            system=result.system, number=1, actor=self.admin
        )
        attempt = EnrichmentAttempt.objects.create(
            workspace=self.workspace,
            policy=policy,
            record=result.record,
            actor=self.admin,
            state=state,
        )
        run = WorkItem.objects.create(
            workspace=self.workspace,
            source=self.source,
            source_version=self.version,
            actor=self.admin,
            system=result.system,
            request_key=f"enrichment:{attempt.pk}:0",
        )
        cap = Capture.objects.create(
            workspace=self.workspace,
            source=self.source,
            run=run,
            url=self.source.url + "/evidence",
            digest=hashlib.sha256(text.encode()).hexdigest(),
            media_type="text/plain",
            original=text.encode(),
            status="captured",
        )
        ParseResult.objects.create(
            capture=cap,
            status="parsed",
            segments=[{"kind": "text", "section": 1, "text": text}],
        )
        attempt.coverage = [{"captures": [str(cap.pk)]}]
        attempt.save(update_fields=["coverage"])
        return attempt, cap

    def test_enrichment_conflicts_rerun_rules_preserve_both_sources_and_permissions(
        self,
    ):
        from intelligence.models import EnrichedFieldEvent, SystemSourceGrant
        from intelligence.review_workflow import apply_enrichment, evidence_for

        *_prefix, result = self.published()
        result = claim_review(self.admin, self.workspace.pk, result.pk, result.version)
        attempt, cap = self.enrichment_capture(result, "Budget: 20")
        merged, changed = apply_enrichment(
            self.admin, self.workspace.pk, result.pk, result.version, attempt.pk
        )
        self.assertTrue(changed)
        self.assertEqual(merged.fields["budget"]["status"], "conflict")
        self.assertEqual(merged.decision, "review")
        self.assertEqual(
            {c["value"] for c in merged.fields["budget"]["candidates"]}, {12, 20}
        )
        self.assertIn(str(cap.pk), str(evidence_for(self.admin, merged)))
        with self.assertRaises(ValidationError):
            store_result(self.admin, self.workspace.pk, result.pk, merged.version)
        _same, changed = apply_enrichment(
            self.admin, self.workspace.pk, result.pk, merged.version, attempt.pk
        )
        self.assertFalse(changed)
        self.assertEqual(EnrichedFieldEvent.objects.count(), 1)
        SystemSourceGrant.objects.filter(
            system=result.system, source=self.source
        ).update(active=False)
        with self.assertRaises(PermissionDenied):
            evidence_for(self.admin, merged)

    def test_enrichment_budget_end_keeps_mandatory_unknown_in_review(self):
        from intelligence.review_workflow import apply_enrichment

        record, _revision, system = self.pipeline(text="Tender without a budget")
        result, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        result = claim_review(self.admin, self.workspace.pk, result.pk, result.version)
        attempt, _cap = self.enrichment_capture(
            result, "No mandatory number available", state="cap_reached"
        )
        merged, _ = apply_enrichment(
            self.admin, self.workspace.pk, result.pk, result.version, attempt.pk
        )
        self.assertEqual(merged.decision, "review")
        self.assertIn("mandatory", merged.reason)
        self.assertFalse(DeliveryIntent.objects.exists())

    def test_negative_unknown_sample_passes_comparison_without_becoming_sendable(self):
        from . import test_batch3 as content_fixtures

        _record, system, _destination, _contract, mapping, samples, _bundle, _result = (
            self.published()
        )
        negative_record, revision, _cap, _created = (
            content_fixtures.BatchThreeTests.captured(
                self, text="Tender without a budget", url=self.source.url + "/negative"
            )
        )
        schema = mapping.schema_version.schema
        samples = save_samples(
            self.admin,
            self.workspace.pk,
            schema.pk,
            "Negative unknown",
            [
                {
                    "content_version": str(revision.pk),
                    "fields": {"budget": None},
                    "routing": "include",
                    "qualification": "review",
                }
            ],
            sample_set_id=samples.sample_set_id,
            expected_version=1,
        )
        comparison = compare_configurations(
            self.admin, self.workspace.pk, system.pk, 3, 3, samples.pk
        )
        self.assertTrue(comparison.passed, comparison.results)
        publish_bundle(
            self.admin, self.workspace.pk, system.pk, 3, mapping.pk, comparison.pk
        )
        result, _ = route_one(
            self.admin, self.workspace.pk, negative_record.pk, system.pk
        )
        self.assertEqual(result.decision, "review")
        with self.assertRaises(ValidationError):
            store_result(self.admin, self.workspace.pk, result.pk, result.version)

    def test_review_queue_limit_applies_after_evidence_permission_filter(self):
        from copy import copy
        from unittest.mock import patch

        from django.urls import reverse

        *_prefix, result = self.published()

        class Rows:
            def filter(self, **kwargs):
                return self

            def select_related(self, *args):
                return self

            def order_by(self, *args):
                return self

            def iterator(self, **kwargs):
                return iter(rows)

        rows = []
        for index in range(101):
            row = copy(result)
            row.reason = str(index)
            rows.append(row)

        def permitted(_actor, row, **kwargs):
            if row.reason != "100":
                raise PermissionDenied("Revoked enrichment evidence")

        self.client.force_login(
            self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )
        with (
            patch("intelligence.review_views.results_for", return_value=Rows()),
            patch("intelligence.review_views.live_access", side_effect=permitted),
        ):
            response = self.client.get(
                reverse("review-queue", args=[self.workspace.pk])
            )
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0].reason, "100")
