import json
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse

from intelligence.models import SourceVersion, WorkItem
from intelligence.systems import create_system

from . import test_batch1 as fixtures
from . import test_batch6 as delivery_fixtures


class BatchEightTests(TestCase):
    pipeline = delivery_fixtures.BatchSixTests.pipeline
    published = delivery_fixtures.BatchSixTests.published

    @classmethod
    def setUpTestData(cls):
        fixtures.BatchOneTests.setUpTestData.__func__(cls)

    def login(self, actor=None):
        self.client.force_login(
            actor or self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )

    def test_resume_setup_is_scoped_and_publication_links_missing_mapping(self):
        _record, _revision, system = self.pipeline()
        unrelated = create_system(
            self.admin, self.workspace.pk, "Unrelated", "Another draft"
        )
        self.login()
        response = self.client.get(
            reverse("system-setup", args=[self.workspace.pk, system.pk])
        )
        self.assertContains(response, "Destination mapping")
        self.assertContains(response, "Action required")
        release = reverse("system-release", args=[self.workspace.pk, system.pk])
        held = self.client.post(
            release,
            {"action": "publish", "payload": json.dumps({"expected_configuration": 2})},
        )
        self.assertContains(held, release + "#mappings")
        self.assertContains(held, "Publication held")
        self.client.get(reverse("system-setup", args=[self.workspace.pk, unrelated.pk]))
        system.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertEqual(system.config_version, 2)
        self.assertEqual(unrelated.config_version, 0)

    def test_overview_next_actions_exclude_restricted_source_existence(self):
        secret_version = SourceVersion.objects.create(
            source=self.secret, version=1, config={"access": "public"}
        )
        WorkItem.objects.create(
            workspace=self.workspace,
            source=self.source,
            source_version=self.version,
            actor=self.admin,
            state="needs_access",
            reason="Reconnect the permitted source account",
        )
        WorkItem.objects.create(
            workspace=self.workspace,
            source=self.secret,
            source_version=secret_version,
            actor=self.admin,
            state="failed",
            reason="Restricted source failed",
        )
        self.login(self.reviewer)
        response = self.client.get(reverse("overview", args=[self.workspace.pk]))
        self.assertEqual(response.context["source_count"], 1)
        self.assertContains(response, "Reconnect source account")
        self.assertNotContains(response, self.secret.name)
        self.assertNotContains(response, str(self.secret.pk))
        WorkItem.objects.filter(source=self.source).update(state="completed")
        from intelligence.models import ContentRecord

        ContentRecord.objects.filter(source=self.source).update(status="parsed")
        calm = self.client.get(reverse("overview", args=[self.workspace.pk]))
        self.assertContains(calm, "No operational exceptions")
        self.assertContains(calm, "start the next bounded collection")

    def test_collection_empty_filtered_and_failed_states_are_distinct(self):
        self.login()
        from workspaces.models import Membership

        Membership.objects.create(
            workspace=self.other, user=self.admin, role="administrator"
        )
        self.assertContains(
            self.client.get(reverse("records", args=[self.other.pk])),
            "No content collected yet",
        )
        url = reverse("records", args=[self.workspace.pk])
        self.pipeline()
        filtered = self.client.get(url, {"q": "never matches anything"})
        self.assertContains(filtered, "Clear filters")
        self.assertNotContains(filtered, "No content collected yet")
        with patch(
            "intelligence.content_views.filtered_records", side_effect=DatabaseError()
        ):
            failed = self.client.get(url)
        self.assertEqual(failed.status_code, 503)
        self.assertContains(failed, "Retry content search", status_code=503)
        self.assertNotContains(failed, "0 matching", status_code=503)

    def test_resume_detects_revoked_system_source_grant(self):
        from intelligence.decision_support import setup_progress
        from intelligence.models import SystemSourceGrant

        _record, _revision, system = self.pipeline()
        system.refresh_from_db()
        self.assertTrue(
            setup_progress(self.admin, self.workspace.pk, system)[0]["complete"]
        )
        SystemSourceGrant.objects.filter(system=system).update(active=False)
        step = setup_progress(self.admin, self.workspace.pk, system)[0]
        self.assertFalse(step["complete"])
        self.assertIn("active system source grants", step["reason"])

    def test_new_collection_run_does_not_claim_old_evaluation(self):
        from intelligence.routing import route_one

        from . import test_batch3 as content_fixtures

        record, _revision, system = self.pipeline()
        old, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        updated, revision, capture, _ = content_fixtures.BatchThreeTests.captured(
            self, text="Tender\nBudget: 24"
        )
        self.assertEqual(updated.pk, record.pk)
        self.assertNotEqual(revision.pk, old.content_version_id)
        self.login()
        response = self.client.get(
            reverse("run-detail", args=[self.workspace.pk, capture.run_id])
        )
        self.assertEqual(response.context["evaluations"], [])
        self.assertEqual(response.context["collected_count"], 1)

    def test_deletion_removes_reason_only_delivery_draft(self):
        from intelligence.delivery import store_result
        from intelligence.lifecycle import execute_deletion, preview_deletion
        from intelligence.models import UserDraft

        record, *_prefix, result = self.published()
        intent, _created = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )
        draft = UserDraft.objects.create(
            workspace=self.workspace,
            actor=self.admin,
            scope_kind="delivery",
            scope_id=intent.pk,
            form_key="form-0",
            payload={"reason": ["Copied evidence"]},
        )
        operation = preview_deletion(self.admin, self.workspace.pk, record.pk)
        self.assertIn(str(draft.pk), [row["id"] for row in operation.impact["drafts"]])
        execute_deletion(self.admin, self.workspace.pk, operation.pk)
        self.assertFalse(UserDraft.objects.filter(pk=draft.pk).exists())

    def test_stale_review_keeps_proposal_until_explicit_current_revision(self):
        from intelligence.models import UserDraft
        from intelligence.routing import claim_review, route_one

        record, _revision, system = self.pipeline()
        result, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        old_version = result.version
        result = claim_review(self.admin, self.workspace.pk, result.pk, result.version)
        self.login()
        url = reverse("review-detail", args=[self.workspace.pk, result.pk])
        proposal = {
            "expected_version": old_version,
            "action": "correct",
            "key": "budget",
            "value": "15",
            "segment": "0",
            "reason": "Retain this human proposal",
        }
        conflict = self.client.post(url, proposal)
        self.assertContains(conflict, "Version conflict")
        self.assertEqual(conflict.context["form"]["reason"].value(), proposal["reason"])
        self.assertTrue(
            UserDraft.objects.filter(scope_id=result.pk, form_key="conflict").exists()
        )
        recovered = self.client.get(url)
        self.assertEqual(recovered.context["form"]["value"].value(), "15")
        proposal["expected_version"] = result.version
        accepted = self.client.post(url, proposal)
        self.assertEqual(accepted.status_code, 302)
        result.refresh_from_db()
        self.assertEqual(result.fields["budget"]["value"], 15)
        self.assertFalse(
            UserDraft.objects.filter(scope_id=result.pk, form_key="conflict").exists()
        )

    def test_ambiguous_delivery_reconciles_existing_receipt_without_writing(self):
        from intelligence.delivery import (
            deliver,
            local_receiver,
            reconcile_delivery,
            store_result,
        )
        from intelligence.models import SandboxMessage, SandboxReceipt

        *_prefix, result = self.published(safe=False)
        intent, _created = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )

        def acknowledgement_lost(operation):
            local_receiver(operation)
            raise TimeoutError("Private receiver response")

        intent = deliver(intent.pk, transport=acknowledgement_lost)
        self.assertEqual(intent.state, "ambiguous")
        self.login()
        url = reverse("delivery-detail", args=[self.workspace.pk, intent.pk])
        response = self.client.get(url)
        self.assertContains(response, "Outcome unknown")
        self.assertNotContains(response, 'value="retry"')
        with patch(
            "intelligence.delivery.local_receiver",
            side_effect=AssertionError("No second write"),
        ):
            confirmed = reconcile_delivery(
                self.admin, self.workspace.pk, intent.pk, "Inspected existing receipt"
            )
            repeated = reconcile_delivery(
                self.admin, self.workspace.pk, intent.pk, "Confirm again"
            )
        self.assertEqual(confirmed.state, "delivered_sandbox")
        self.assertEqual(repeated.attempts, 1)
        self.assertEqual(SandboxReceipt.objects.count(), 1)
        self.assertEqual(SandboxMessage.objects.count(), 1)

    def test_same_bytes_new_processing_does_not_claim_prior_analysis(self):
        from intelligence.routing import route_one

        from . import test_batch3 as content_fixtures

        record, revision, system = self.pipeline()
        old, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        _record, repeated, capture, _ = content_fixtures.BatchThreeTests.captured(
            self, text="Tender\nBudget: 12"
        )
        self.assertEqual(repeated.pk, revision.pk)
        from intelligence.content import materialize

        materialize(
            capture,
            {
                "status": "parsed",
                "segments": [
                    {
                        "kind": "text",
                        "section": 1,
                        "text": "Tender\nBudget: 12\nRecovered section",
                    }
                ],
                "reason": "",
            },
        )
        self.assertNotEqual(capture.contentanalysis_set.get().pk, old.analysis_id)
        self.login()
        response = self.client.get(
            reverse("run-detail", args=[self.workspace.pk, capture.run_id])
        )
        self.assertEqual(response.context["evaluations"], [])

    def test_historical_protected_evidence_denies_review_and_draft_recovery(self):
        from django.core.exceptions import PermissionDenied

        from intelligence.models import ReviewChange, SystemSourceGrant
        from intelligence.routing import live_access, route_one

        from . import test_batch3 as content_fixtures

        record, _revision, system = self.pipeline()
        result, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        # The protected capture has left current fields but remains in immutable history.
        secret_version = SourceVersion.objects.create(
            source=self.secret, version=1, config={"access": "public"}
        )
        old_source_version = self.version
        self.version = secret_version
        try:
            _record, _revision, capture, _ = content_fixtures.BatchThreeTests.captured(
                self, source=self.secret
            )
        finally:
            self.version = old_source_version
        SystemSourceGrant.objects.create(system=system, source=self.secret)
        ReviewChange.objects.create(
            workspace=self.workspace,
            result=result,
            actor=self.admin,
            kind="field",
            result_version=result.version,
            original={"budget": {"candidates": [{"capture": str(capture.pk)}]}},
            replacement={},
            evidence={},
            reason="Historical protected evidence",
        )
        with self.assertRaises(PermissionDenied):
            live_access(self.reviewer, result)
        self.login(self.reviewer)
        self.assertEqual(
            self.client.get(
                reverse("review-detail", args=[self.workspace.pk, result.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    "scoped-draft",
                    args=[self.workspace.pk, "result", result.pk, "form-0"],
                )
            ).status_code,
            403,
        )

    def test_change_summary_links_unresolved_rules_and_replay_has_no_writes(self):
        from types import SimpleNamespace

        from intelligence.decision_support import comparison_summary
        from intelligence.replay import preview_replay

        comparison = SimpleNamespace(
            right=SimpleNamespace(number=4),
            evaluation_version=SimpleNamespace(
                samples=SimpleNamespace(count=lambda: 1)
            ),
            results=[
                {
                    "sample": "sample-1",
                    "decision_changed": True,
                    "passed": False,
                    "left": {"fields": {"budget": 12}},
                    "right": {
                        "fields": {"budget": 12},
                        "qualification": {
                            "decision": "review",
                            "rules": [{"field": "budget", "result": "unknown"}],
                        },
                    },
                }
            ],
        )
        summary = comparison_summary(comparison)
        self.assertEqual(
            (summary["changed"], summary["unchanged"], summary["unresolved"]), (1, 0, 1)
        )
        self.assertEqual(summary["errors"][0]["anchor"], "configuration-4")
        record, system, _destination, _contract, _mapping, _samples, bundle, _result = (
            self.published()
        )
        preview = preview_replay(
            self.admin, self.workspace.pk, system.pk, bundle.pk, [str(record.pk)]
        )
        self.assertEqual(
            preview.report["destination_effects"]["writes_during_replay"], 0
        )
