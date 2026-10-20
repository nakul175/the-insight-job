"""Requirement-led synthetic probes added during the 237-criterion R1 audit.

These are diagnostic acceptance checks, not independent product sign-off.
Fixture builders only are reused; each probe asserts a requirement directly.
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.http import Http404
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from intelligence.content import save_schema
from intelligence.models import (
    AuditEvent,
    Incident,
    Schedule,
    SystemGrant,
    SystemSourceGrant,
    WorkItem,
)
from intelligence.observability import monitor, save_alert_rule
from intelligence.qualification import validate_rules
from intelligence.review_workflow import follow_up
from intelligence.routing import (
    claim_review,
    override_decision,
    route_one,
    set_system_state,
)
from intelligence.systems import create_system, save_configuration

from . import test_batch1 as base
from . import test_batch3 as content
from . import test_batch5 as routing
from . import test_batch6 as delivery


class AcceptanceAuditTests(TestCase):
    pipeline = delivery.BatchSixTests.pipeline
    published = delivery.BatchSixTests.published

    @classmethod
    def setUpTestData(cls):
        base.BatchOneTests.setUpTestData.__func__(cls)

    def login(self, actor=None):
        self.client.force_login(
            actor or self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )

    def test_ac0105_visible_interval_drilldown_and_timestamped_failure(self):
        self.login(self.reviewer)
        url = reverse("overview", args=[self.workspace.pk])
        response = self.client.get(url)
        self.assertContains(response, "Content window:")
        self.assertIsNotNone(response.context["start"])
        self.assertIsNotNone(response.context["as_of"])
        queue = reverse("records", args=[self.workspace.pk])
        self.assertContains(response, queue + "?status=review&amp;window=7d")
        filtered = self.client.get(queue, {"status": "review", "window": "7d"})
        self.assertEqual(filtered.context["count"], response.context["issue_count"])
        with patch(
            "intelligence.views.sources_for", side_effect=DatabaseError("private")
        ):
            failed = self.client.get(url)
        self.assertContains(failed, "Refresh failed at", status_code=503)
        self.assertIsNotNone(failed.context["as_of"])
        self.assertNotContains(failed, "private", status_code=503)

    def test_ac0203_scope_preview_reports_each_matching_rule(self):
        self.login()
        with patch("socket.getaddrinfo", return_value=base.PUBLIC_DNS):
            response = self.client.post(
                reverse("scope-preview", args=[self.workspace.pk, self.source.pk]),
                {
                    "urls": "https://example.com/notice\nhttps://example.com/private/report\nhttps://other.example.com/"
                },
            )
        self.assertContains(response, "Included by /")
        self.assertContains(response, "Excluded by /private")
        self.assertContains(response, "Outside")

    def test_ac0206_archive_preview_lists_runs_schedules_and_dependencies(self):
        WorkItem.objects.create(
            workspace=self.workspace,
            source=self.source,
            source_version=self.version,
            actor=self.admin,
            state="queued",
        )
        Schedule.objects.create(
            workspace=self.workspace, source=self.source, enabled=True
        )
        SystemSourceGrant.objects.create(system=self.a, source=self.source)
        self.login()
        response = self.client.get(
            reverse("source-detail", args=[self.workspace.pk, self.source.pk])
        )
        self.assertContains(response, "1 active or queued runs; 1 enabled schedules")
        self.assertContains(response, self.a.name)
        self.assertContains(response, 'value="finish"')
        self.assertContains(response, 'value="stop"')

    def test_ac0504_common_envelope_pins_capture_format_and_identity(self):
        record, version, capture, _ = content.BatchThreeTests.captured(self)
        self.assertEqual(version.record_id, record.pk)
        self.assertEqual(capture.content_version_id, version.pk)
        self.assertEqual(capture.source_id, record.source_id)
        self.assertEqual(version.media_type, "text/plain")
        self.assertEqual(version.language, "unknown")
        self.assertEqual(version.completeness, "complete")
        self.assertIsNotNone(capture.created_at)
        self.assertEqual(len(capture.digest), 64)

    def type_schema_with_dependants(self):
        schema, _ = save_schema(
            self.admin,
            self.workspace.pk,
            "Tender type",
            "tender",
            "type",
            [content.field()],
        )
        # Published fixture state only: publication gates are exercised in BatchFourTests.
        schema.state, schema.published_version = "published", 1
        schema.save()
        system = create_system(
            self.admin, self.workspace.pk, "Permitted dependency", "Audit"
        )
        hidden = create_system(
            self.admin, self.workspace.pk, "PRIVATE dependency", "Audit"
        )
        for item in [system, hidden]:
            save_configuration(
                self.admin,
                self.workspace.pk,
                item.pk,
                0,
                {
                    "source_ids": [str(self.source.pk)],
                    "type_keys": ["tender"],
                    "unknown_type": "review",
                    "relevance": {
                        "all": ["Tender"],
                        "none": [],
                        "unknown": "review",
                        "examples": [{"text": "Tender", "expected": "include"}],
                    },
                },
            )
        SystemGrant.objects.create(membership=self.rm, system=system)
        return schema, system, hidden

    def test_ac0701_type_schema_lists_permitted_dependent_systems(self):
        schema, system, hidden = self.type_schema_with_dependants()
        self.login(self.reviewer)
        response = self.client.get(
            reverse("schema-detail", args=[self.workspace.pk, schema.pk])
        )
        self.assertContains(response, system.name)
        self.assertNotContains(response, hidden.name)
        self.assertNotContains(response, str(hidden.pk))

    def test_ac0804_two_successful_consumers_share_source_not_decision_identity(self):
        record, version, a = self.pipeline()
        a.refresh_from_db()
        original = a.configurations.get(number=a.config_version)
        # Use a second complete system fixture, then route the same original item.
        _, _, b = self.pipeline(name="Other")
        first, _ = route_one(self.admin, self.workspace.pk, record.pk, a.pk)
        second, _ = route_one(self.admin, self.workspace.pk, record.pk, b.pk)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.content_version_id, version.pk)
        self.assertEqual(second.content_version_id, version.pk)
        self.assertEqual(first.configuration_id, original.pk)
        self.assertNotEqual(first.configuration_id, second.configuration_id)

    def test_ac0806_retirement_retains_results_and_blocks_new_routing(self):
        record, _, system = self.pipeline()
        result, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        set_system_state(self.admin, self.workspace.pk, system.pk, "retired")
        result.refresh_from_db()
        self.assertEqual(result.system_id, system.pk)
        self.login()
        response = self.client.get(
            reverse("system-detail", args=[self.workspace.pk, system.pk])
        )
        self.assertContains(response, system.name)
        with self.assertRaises(ValidationError):
            set_system_state(self.admin, self.workspace.pk, system.pk, "draft")

    def test_ac0902_incompatible_rule_types_and_semantic_operator_fail_closed(self):
        for operator, value in [("contains", 10), ("gte", "ten"), ("semantic", 10)]:
            with (
                self.subTest(operator=operator, value=value),
                self.assertRaises(ValidationError),
            ):
                validate_rules(
                    routing.rules(operator=operator, value=value), [content.field()]
                )

    def test_ac1004_defer_requires_reason_owner_date_and_retains_audit_version(self):
        record, _, system = self.pipeline(text="Tender\nBudget: 2")
        result, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        claimed = claim_review(self.admin, self.workspace.pk, result.pk, result.version)
        future = timezone.now() + timedelta(days=1)
        for date, reason in [
            (future, ""),
            (None, "Need evidence"),
            (timezone.now() - timedelta(seconds=1), "Need evidence"),
        ]:
            with self.assertRaises(ValidationError):
                follow_up(
                    self.admin,
                    self.workspace.pk,
                    result.pk,
                    claimed.version,
                    "defer",
                    self.admin,
                    date,
                    reason,
                )
        deferred = follow_up(
            self.admin,
            self.workspace.pk,
            result.pk,
            claimed.version,
            "defer",
            self.admin,
            future,
            "Need evidence",
        )
        self.assertEqual(deferred.review_state, "deferred")
        event = deferred.changes.get(kind="defer")
        self.assertEqual(event.actor_id, self.admin.pk)
        self.assertEqual(event.result_version, deferred.version)
        self.assertEqual(event.reason, "Need evidence")

    def test_ac1401_correction_appends_audit_without_mutating_prior_event(self):
        from intelligence.routing import correct_field

        record, _, system = self.pipeline()
        result, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        claimed = claim_review(self.admin, self.workspace.pk, result.pk, result.version)
        before = list(AuditEvent.objects.order_by("pk").values())
        corrected = correct_field(
            self.admin,
            self.workspace.pk,
            claimed.pk,
            claimed.version,
            "budget",
            15,
            0,
            "Business correction supported by passage",
        )
        self.assertEqual(
            list(
                AuditEvent.objects.filter(pk__in=[r["id"] for r in before])
                .order_by("pk")
                .values()
            ),
            before,
        )
        event = AuditEvent.objects.get(
            action="review.field_corrected", object_id=str(result.pk)
        )
        self.assertEqual(event.actor_id, self.admin.pk)
        self.assertEqual(event.details["version"], corrected.version)
        self.assertEqual(event.details["field"], "budget")
        self.assertEqual(AuditEvent.objects.count(), len(before) + 1)

    def test_ac1402_monitor_filters_usage_timestamp_and_correlation(self):
        from intelligence.delivery import deliver, store_result
        from intelligence.models import Capture, DeliveryIntent

        record, system, *_prefix, result = self.published()
        capture = Capture.objects.get(
            record=record,
            content_version=result.content_version,
            contentanalysis=result.analysis,
        )
        run = capture.run
        run.system, run.state = system, "action_required"
        run.usage = {"pages": 2, "as_of": "2026-10-20T12:00:00Z"}
        run.save()
        intent, _ = store_result(
            self.admin, self.workspace.pk, result.pk, result.version
        )

        def unavailable(_intent):
            raise OSError("Synthetic receiver unavailable")

        for _ in range(3):
            DeliveryIntent.objects.filter(pk=intent.pk).update(
                next_attempt=timezone.now() - timedelta(seconds=1)
            )
            failed = deliver(intent.pk, transport=unavailable)
        self.assertEqual(failed.state, "action_required")
        metrics = monitor(
            self.admin,
            self.workspace.pk,
            timezone.now() - timedelta(days=1),
            timezone.now() + timedelta(seconds=1),
            self.source.pk,
            system.pk,
        )
        self.assertEqual(metrics["total_matching"], 1)
        row = metrics["runs"][0]
        self.assertEqual(row["correlation_id"], str(run.pk))
        self.assertEqual(row["record_ids"], [str(record.pk)])
        self.assertEqual(row["delivery_ids"], [str(intent.pk)])
        self.assertEqual(row["state"], "action_required")
        self.assertEqual(row["usage_as_of"], run.usage["as_of"])
        self.assertEqual(sum(metrics["counts"].values()), len(metrics["runs"]))
        self.assertEqual(row["limits"], run.limits)

    def test_ac1403_nonmember_cannot_receive_workspace_incident(self):
        with self.assertRaises(Http404):
            save_alert_rule(
                self.admin, self.workspace.pk, "failure", 1, 60, self.invitee.pk
            )
        self.assertFalse(Incident.objects.exists())

    def test_ac0805_uncertain_relevance_is_reviewable_and_exclusion_can_be_overturned(
        self,
    ):
        record, system, *_prefix, old = self.published()
        # An empty-but-explicitly-partial fixture exercises unknown relevance;
        # no model judgement is substituted for unavailable content.
        record, _, _, _ = content.BatchThreeTests.captured(self, text="")
        uncertain, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        self.assertEqual(uncertain.relevance["decision"], "review")
        self.assertEqual(uncertain.decision, "review")
        self.login()
        queue = self.client.get(reverse("review-queue", args=[self.workspace.pk]))
        self.assertIn(uncertain.pk, [r.pk for r in queue.context["rows"]])
        record, _, _, _ = content.BatchThreeTests.captured(
            self, text="Unrelated notice\nBudget: 12"
        )
        excluded, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        self.assertEqual(excluded.decision, "excluded")
        claimed = claim_review(
            self.admin, self.workspace.pk, excluded.pk, excluded.version
        )
        changed = override_decision(
            self.admin,
            self.workspace.pk,
            claimed.pk,
            claimed.version,
            "include",
            "Authorised sample review confirms relevance",
        )
        self.assertEqual(changed.decision, "pass")
        event = changed.changes.get(kind="override")
        self.assertEqual(event.actor_id, self.admin.pk)
        self.assertEqual(event.original["relevance"]["decision"], "exclude")
        self.assertEqual(event.replacement["relevance"]["decision"], "include")
        self.assertEqual(event.reason, "Authorised sample review confirms relevance")
        old.refresh_from_db()
        self.assertEqual(old.version, 1)

    def test_ac1001_combined_reason_system_age_owner_filters_and_foreign_scope(self):
        record, _, system = self.pipeline(text="Tender\nBudget: 2")
        result, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        result.created_at = timezone.now() - timedelta(hours=3)
        result.reason = "Diagnostic missing evidence"
        result.save(update_fields=["created_at", "reason"])
        self.login(self.reviewer)
        url = reverse("review-queue", args=[self.workspace.pk])
        params = {
            "reason": "missing",
            "system": str(system.pk),
            "age_hours": 2,
            "owner": "unassigned",
        }
        response = self.client.get(url, params)
        self.assertEqual([r.pk for r in response.context["rows"]], [result.pk])
        self.assertEqual(
            list(self.client.get(url, {**params, "age_hours": 4}).context["rows"]), []
        )
        self.assertEqual(
            list(
                self.client.get(url, {**params, "reason": "different"}).context["rows"]
            ),
            [],
        )
        SystemGrant.objects.filter(membership=self.rm, system=system).delete()
        rejected = self.client.get(url, params)
        self.assertEqual(rejected.status_code, 400)
        self.assertNotContains(rejected, record.title, status_code=400)

    def test_ac1002_missing_reason_and_evidence_cannot_change_reviewed_value(self):
        from intelligence.routing import correct_field

        record, _, system = self.pipeline()
        result, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        claimed = claim_review(self.admin, self.workspace.pk, result.pk, result.version)
        for segment, reason in [
            (0, ""),
            (9999, "Unsupported passage"),
            (None, "Missing passage"),
        ]:
            with self.assertRaises(ValidationError):
                correct_field(
                    self.admin,
                    self.workspace.pk,
                    claimed.pk,
                    claimed.version,
                    "budget",
                    15,
                    segment,
                    reason,
                )
        claimed.refresh_from_db()
        self.assertEqual(claimed.fields["budget"]["value"], 12)
        self.assertFalse(claimed.changes.filter(kind="field").exists())

    def test_ac0205_sample_report_exposes_format_missing_scope_and_partial_policy(self):
        from intelligence.source_testing import activate, test_source

        capture = {
            "state": "captured",
            "bytes": b'<h1>Notice</h1><img src="evidence.png">',
            "format": "text/html",
            "missing": [],
        }
        with patch("intelligence.source_testing.fetch_public", return_value=capture):
            sampled = test_source(self.admin, self.source)
        self.assertEqual(sampled.state, "partial")
        self.assertEqual(sampled.report["formats"], ["text/html"])
        self.assertTrue(sampled.report["missing"])
        self.assertFalse(sampled.report["downstream_delivery"])
        with self.assertRaises(ValidationError):
            activate(self.admin, self.source, sampled.pk)
        self.login()
        screen = self.client.get(
            reverse("source-detail", args=[self.workspace.pk, self.source.pk])
        )
        self.assertContains(screen, "Embedded media")
        self.assertContains(screen, "text/html")

    def test_ac1102_cyclic_enrichment_link_does_not_enqueue_a_second_capture(self):
        from intelligence.enrichment import enrichment_step, start_enrichment
        from intelligence.models import Capture

        from . import test_batch4 as enrichment_fixtures

        # Use the established policy builder without inheriting its test methods.
        self.draft = lambda: enrichment_fixtures.BatchFourTests.draft(self)
        self.config = lambda: enrichment_fixtures.BatchFourTests.config(self)
        system, _ = enrichment_fixtures.BatchFourTests.policy_fixture(self, pages=3)
        url = "https://example.com/a"
        attempt = start_enrichment(
            self.admin,
            self.workspace.pk,
            system.pk,
            self.visible.pk,
            "Evidence gap",
            [url],
            ["budget"],
        )
        with patch("socket.getaddrinfo", return_value=base.PUBLIC_DNS):
            enrichment_step(self.admin, self.workspace.pk, attempt.pk)
        job = WorkItem.objects.get(request_key=f"enrichment:{attempt.pk}:0")
        Capture.objects.create(
            workspace=self.workspace,
            run=job,
            source=self.source,
            url=url,
            final_url=url,
            digest="a" * 64,
            media_type="text/html",
            original=b'<a href="https://example.com/a">Cycle</a>',
            status="captured",
        )
        job.state, job.usage = "completed", {"bytes": 55, "seconds": 1}
        job.save()
        with patch("socket.getaddrinfo", return_value=base.PUBLIC_DNS):
            finished = enrichment_step(self.admin, self.workspace.pk, attempt.pk)
        self.assertEqual(finished.pending, [])
        self.assertEqual(finished.visited, [url])
        self.assertEqual(
            WorkItem.objects.filter(
                request_key__startswith=f"enrichment:{attempt.pk}:"
            ).count(),
            1,
        )

    def test_ac1507_resumed_missing_evidence_creates_new_traceable_evaluation(self):
        from intelligence.models import Capture

        record, _, system = self.pipeline(
            text="Tender\nRequired attachment unavailable"
        )
        old, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        self.assertEqual(old.decision, "review")
        self.assertEqual(old.fields["budget"]["status"], "unknown")
        first_capture = Capture.objects.get(
            record=record, content_version=old.content_version
        )
        first_capture.missing = [
            {
                "url": "https://example.com/budget.pdf",
                "reason": "Required attachment unavailable",
            }
        ]
        first_capture.save(update_fields=["missing"])
        updated, version, resumed, _ = content.BatchThreeTests.captured(
            self, text="Tender\nBudget: 24"
        )
        current, created = route_one(
            self.admin, self.workspace.pk, updated.pk, system.pk
        )
        self.assertTrue(created)
        self.assertNotEqual(current.pk, old.pk)
        self.assertEqual(current.content_version_id, version.pk)
        self.assertEqual(current.analysis_id, resumed.contentanalysis_set.get().pk)
        self.assertEqual(current.fields["budget"]["value"], 24)
        self.assertEqual(current.decision, "pass")
        old.refresh_from_db()
        self.assertEqual(old.decision, "review")
        self.assertEqual(old.fields["budget"]["status"], "unknown")
        self.login()
        response = self.client.get(
            reverse("run-detail", args=[self.workspace.pk, resumed.run_id])
        )
        self.assertEqual([r.pk for r in response.context["evaluations"]], [current.pk])

    def test_ac1305_sampling_excludes_outside_interval_and_unpermitted_sources(self):
        from intelligence.models import SourceVersion
        from intelligence.observability import sample_rejects
        from intelligence.systems import current_config

        record, _, system = self.pipeline(text="Unrelated notice\nBudget: 2")
        in_scope, _ = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        other, _, _, _ = content.BatchThreeTests.captured(
            self, text="Earlier unrelated\nBudget: 2", url="https://example.com/earlier"
        )
        earlier, _ = route_one(self.admin, self.workspace.pk, other.pk, system.pk)
        earlier.created_at = timezone.now() - timedelta(days=5)
        earlier.save(update_fields=["created_at"])
        system.refresh_from_db()
        config = current_config(system)
        config["source_ids"].append(str(self.secret.pk))
        system.refresh_from_db()
        save_configuration(
            self.admin, self.workspace.pk, system.pk, system.config_version, config
        )
        self.version = SourceVersion.objects.create(
            source=self.secret, version=1, config={"access": "public", "include": ["/"]}
        )
        hidden, _, _, _ = content.BatchThreeTests.captured(
            self, text="PRIVATE unrelated\nBudget: 2", source=self.secret
        )
        restricted, _ = route_one(self.admin, self.workspace.pk, hidden.pk, system.pk)
        self.rm.role = "operator"
        self.rm.save(update_fields=["role"])
        system.owner = self.reviewer
        system.save(update_fields=["owner"])
        sampled = sample_rejects(
            self.reviewer,
            self.workspace.pk,
            system.pk,
            timezone.now() - timedelta(days=1),
            timezone.now() + timedelta(seconds=1),
            10,
            0,
        )
        self.assertEqual(sampled.result_ids, [str(in_scope.pk)])
        self.assertNotIn(str(restricted.pk), sampled.result_ids)
        self.assertNotIn(str(earlier.pk), sampled.result_ids)

    def test_ac1403_restricted_recipient_receives_only_generic_incident(self):
        from intelligence.models import SourceVersion
        from intelligence.observability import evaluate_alerts

        version = SourceVersion.objects.create(
            source=self.secret, version=1, config={"access": "public"}
        )
        job = WorkItem.objects.create(
            workspace=self.workspace,
            actor=self.admin,
            source=self.secret,
            source_version=version,
            state="action_required",
            reason="PRIVATE incident evidence excerpt",
        )
        save_alert_rule(
            self.admin,
            self.workspace.pk,
            "failure",
            1,
            60,
            self.reviewer.pk,
            source_id=self.secret.pk,
        )
        incidents = evaluate_alerts(self.admin, self.workspace.pk)
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].recipient_id, self.reviewer.pk)
        self.login(self.reviewer)
        response = self.client.get(reverse("governance", args=[self.workspace.pk]))
        self.assertContains(response, str(incidents[0].pk))
        self.assertContains(response, "failure")
        for withheld in [
            self.secret.name,
            str(self.secret.pk),
            str(job.pk),
            job.reason,
            self.hidden.title,
        ]:
            self.assertNotContains(response, withheld)

    def test_ac0902_rule_builder_exposes_field_operator_value_group_unknown(self):
        _record, _, system = self.pipeline()
        self.login()
        response = self.client.get(
            reverse("qualification", args=[self.workspace.pk, system.pk])
        )
        self.assertContains(response, "Rules")
        self.assertContains(response, "No semantic inference or unit conversion")
        # The declared prototype builder is a labelled JSON field. Its visible
        # example must expose every required rule component, not hide defaults.
        for component in [
            "group",
            "all",
            "unknown",
            "review",
            "field",
            "budget",
            "operator",
            "gte",
            "value",
            "unit",
        ]:
            self.assertContains(response, component)
        self.assertContains(response, 'for="id_rules"')
        self.assertContains(response, 'id="id_rules"')
