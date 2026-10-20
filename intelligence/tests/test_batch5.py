from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from intelligence.content import save_schema
from intelligence.models import SystemGrant, SystemResult
from intelligence.qualification import evaluate_rules, validate_rules
from intelligence.routing import (
    claim_review,
    configure_qualification,
    correct_field,
    override_decision,
    route_independently,
    route_one,
    set_system_state,
)
from intelligence.systems import create_system, save_configuration

from . import test_batch1 as fixtures
from . import test_batch3 as content_fixtures
from .test_batch3 import field


def rules(value=10, operator="gte", **changes):
    return {
        "group": "all",
        "unknown": "review",
        "rules": [
            {
                "field": "budget",
                "operator": operator,
                "value": value,
                "unit": "",
                "hard_fail": False,
            }
        ],
        **changes,
    }


class BatchFiveTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.BatchOneTests.setUpTestData.__func__(cls)

    def pipeline(self, name="Pilot", text="Tender\nBudget: 12"):
        record, revision, _cap, _created = content_fixtures.BatchThreeTests.captured(
            self, text=text
        )
        system = create_system(self.admin, self.workspace.pk, name, "Review tenders")
        save_configuration(
            self.admin,
            self.workspace.pk,
            system.pk,
            0,
            {
                "source_ids": [str(self.source.pk)],
                "type_keys": [],
                "unknown_type": "include",
                "relevance": {
                    "all": ["Tender"],
                    "none": [],
                    "unknown": "review",
                    "examples": [{"text": "Tender", "expected": "include"}],
                },
            },
        )
        schema, version = save_schema(
            self.admin,
            self.workspace.pk,
            name + " fields",
            name.lower(),
            "system",
            [field()],
            system_id=system.pk,
        )
        schema.state = "published"
        schema.published_version = 1
        schema.save()
        version.state = "published"
        version.save()
        configure_qualification(
            self.admin, self.workspace.pk, system.pk, 1, schema.pk, rules()
        )
        SystemGrant.objects.create(membership=self.rm, system=system)
        return record, revision, system

    def test_independent_idempotent_routing_failure_in_one_system_does_not_block_other(
        self,
    ):
        record, revision, a = self.pipeline()
        result, created = route_one(self.admin, self.workspace.pk, record.pk, a.pk)
        self.assertTrue(created)
        self.assertEqual(result.decision, "pass")
        self.assertEqual(result.content_version_id, revision.pk)
        self.assertEqual(
            route_one(self.admin, self.workspace.pk, record.pk, a.pk)[0].pk, result.pk
        )
        b = create_system(self.admin, self.workspace.pk, "Incomplete", "Missing config")
        outcomes = route_independently(
            self.admin, self.workspace.pk, record.pk, [b.pk, a.pk]
        )
        self.assertIn("error", outcomes[0])
        self.assertEqual(outcomes[1]["result"], str(result.pk))
        self.assertEqual(SystemResult.objects.count(), 1)

    def test_pause_blocks_only_affected_system_and_retains_history(self):
        record, _revision, system = self.pipeline()
        result, _created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        set_system_state(self.admin, self.workspace.pk, system.pk, "paused")
        with self.assertRaises(PermissionDenied):
            route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        self.assertTrue(SystemResult.objects.filter(pk=result.pk).exists())
        resumed = set_system_state(self.admin, self.workspace.pk, system.pk, "draft")
        self.assertEqual(resumed.state, "draft")

    def test_unknown_required_fields_do_not_pass_and_hard_failure_stops_rules(self):
        actual = {"budget": {"status": "unknown", "value": None, "unit": ""}}
        self.assertEqual(
            evaluate_rules(actual, [field()], rules(unknown="enrich"), 3)["decision"],
            "enrich",
        )
        config = rules()
        config["rules"][0]["hard_fail"] = True
        config["rules"].append({**config["rules"][0], "value": 1})
        outcome = evaluate_rules(
            {"budget": {"status": "known", "value": 2, "unit": ""}},
            [field()],
            config,
            3,
        )
        self.assertEqual(outcome["decision"], "fail")
        self.assertEqual(len(outcome["rules"]), 1)
        self.assertEqual(outcome["rules"][0]["configuration_version"], 3)

    def test_units_timezone_and_inclusive_boundary(self):
        definitions = [field(key="deadline", kind="text")]
        config = {
            "group": "all",
            "unknown": "review",
            "rules": [
                {
                    "field": "deadline",
                    "operator": "on_or_before",
                    "value": "2026-10-20T00:00:00+00:00",
                    "unit": "",
                    "hard_fail": False,
                }
            ],
        }
        fact = {
            "deadline": {
                "status": "known",
                "value": "2026-10-20T05:30:00+05:30",
                "unit": "",
            }
        }
        result = evaluate_rules(fact, definitions, config, 1)
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["rules"][0]["source_timezone"], "UTC+05:30")
        config["rules"][0]["operator"] = "before"
        self.assertEqual(
            evaluate_rules(fact, definitions, config, 1)["decision"], "fail"
        )
        fact["deadline"]["value"] = "2026-10-20"
        self.assertEqual(
            evaluate_rules(fact, definitions, config, 1)["decision"], "review"
        )
        self.assertEqual(
            evaluate_rules(
                {"budget": {"status": "known", "value": 12, "unit": "USD"}},
                [field()],
                rules(),
                1,
            )["decision"],
            "review",
        )
        with self.assertRaises(ValidationError):
            validate_rules(rules(value=True), [field()])

    def test_claim_correction_history_reruns_rules_and_rejects_stale_claim(self):
        record, _revision, system = self.pipeline()
        result, _created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        claimed = claim_review(self.reviewer, self.workspace.pk, result.pk, 1)
        with self.assertRaises(ValidationError):
            claim_review(self.admin, self.workspace.pk, result.pk, 1)
        corrected = correct_field(
            self.reviewer,
            self.workspace.pk,
            result.pk,
            claimed.version,
            "budget",
            2,
            0,
            "Source passage supports correction",
        )
        self.assertEqual(corrected.decision, "fail")
        self.assertEqual(corrected.changes.get().original["budget"]["value"], 12)
        self.assertEqual(corrected.changes.get().replacement["budget"]["value"], 2)
        with self.assertRaises(ValidationError):
            correct_field(
                self.reviewer,
                self.workspace.pk,
                result.pk,
                corrected.version,
                "budget",
                True,
                0,
                "Invalid numeric type",
            )

    def test_override_cannot_bypass_destination_or_new_content(self):
        record, _revision, system = self.pipeline()
        result, _created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        claimed = claim_review(self.reviewer, self.workspace.pk, result.pk, 1)
        with self.assertRaisesMessage(ValidationError, "Destination"):
            override_decision(
                self.reviewer,
                self.workspace.pk,
                result.pk,
                claimed.version,
                "pass",
                "Business reason",
            )
        overridden = override_decision(
            self.reviewer,
            self.workspace.pk,
            result.pk,
            claimed.version,
            "fail",
            "Business exclusion",
        )
        self.assertEqual(overridden.decision, "fail")
        content_fixtures.BatchThreeTests.captured(self, text="Tender\nBudget: 22")
        with self.assertRaises(ValidationError):
            override_decision(
                self.reviewer,
                self.workspace.pk,
                result.pk,
                overridden.version,
                "include",
                "Stale decision",
            )

    def test_new_source_and_classification_produce_fresh_result_and_resume_backlog(
        self,
    ):
        from intelligence.content import correct_types
        from intelligence.routing import backlog_preview

        record, _revision, system = self.pipeline()
        old, _created = route_one(self.admin, self.workspace.pk, record.pk, system.pk)
        system.refresh_from_db()
        self.assertNotIn(
            record.pk, [row.pk for row in backlog_preview(self.admin, system)]
        )
        content_fixtures.BatchThreeTests.captured(self, text="Tender\nBudget: 23")
        self.assertIn(
            record.pk, [row.pk for row in backlog_preview(self.admin, system)]
        )
        current, created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        self.assertTrue(created)
        self.assertNotEqual(current.pk, old.pk)
        record.refresh_from_db()
        correct_types(
            self.admin,
            record,
            [],
            "Explicit reclassification review",
            record.classification_version,
        )
        next_result, created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        self.assertTrue(created)
        self.assertNotEqual(next_result.pk, current.pk)

    def test_unknown_type_inclusion_does_not_override_excluded_relevance(self):
        record, _revision, system = self.pipeline(text="Unrelated notice\nBudget: 12")
        result, _created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        self.assertEqual(result.decision, "excluded")
        self.assertEqual(result.relevance["decision"], "exclude")

    def test_queue_filters_claim_and_original_evidence_ui(self):
        from django.urls import reverse

        record, _revision, system = self.pipeline(text="Tender\nBudget: 2")
        result, _created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        self.client.force_login(
            self.reviewer, backend="django.contrib.auth.backends.ModelBackend"
        )
        queue_url = reverse("review-queue", args=[self.workspace.pk])
        self.assertContains(
            self.client.get(queue_url, {"system": system.pk, "owner": "unassigned"}),
            record.title,
        )
        url = reverse("review-detail", args=[self.workspace.pk, result.pk])
        self.assertContains(self.client.get(url), "Pinned evidence passages")
        self.assertEqual(
            self.client.post(
                url, {"expected_version": 1, "action": "claim"}
            ).status_code,
            302,
        )
        self.assertContains(self.client.get(queue_url, {"owner": "me"}), record.title)
        from unittest.mock import patch

        from django.db import DatabaseError

        with patch(
            "intelligence.review_views.results_for",
            side_effect=DatabaseError("unavailable"),
        ):
            response = self.client.get(queue_url)
        self.assertContains(response, "could not load", status_code=503)
        self.assertNotContains(response, "No matching review tasks", status_code=503)

    def test_current_permission_revocation_blocks_review_and_result_visibility(self):
        from intelligence.models import SystemSourceGrant
        from intelligence.routing import results_for

        record, _revision, system = self.pipeline()
        result, _created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        SystemSourceGrant.objects.filter(system=system, source=self.source).update(
            active=False
        )
        self.assertFalse(
            results_for(self.reviewer, self.workspace.pk).filter(pk=result.pk).exists()
        )
        from django.http import Http404

        with self.assertRaises(Http404):
            claim_review(self.reviewer, self.workspace.pk, result.pk, 1)

    def test_review_include_override_cannot_bypass_destination(self):
        record, _revision, system = self.pipeline(text="Unrelated\nBudget: 12")
        result, _created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        claimed = claim_review(self.reviewer, self.workspace.pk, result.pk, 1)
        with self.assertRaisesMessage(ValidationError, "Destination"):
            override_decision(
                self.reviewer,
                self.workspace.pk,
                result.pk,
                claimed.version,
                "include",
                "Relevant despite original criteria",
            )
        result.refresh_from_db()
        self.assertEqual(result.decision, "excluded")

    def test_review_date_extraction_and_equality_compare_aware_instants(self):
        from types import SimpleNamespace

        from intelligence.content import extract_fields

        definition = field(key="deadline", kind="date")
        extracted = extract_fields(
            SimpleNamespace(segments=[{"text": "Deadline: 2026-10-20T05:30:00+05:30"}]),
            [definition],
        )
        self.assertEqual(extracted["deadline"]["status"], "known")
        config = {
            "group": "all",
            "unknown": "review",
            "rules": [
                {
                    "field": "deadline",
                    "operator": "eq",
                    "value": "2026-10-20T00:00:00Z",
                    "unit": "",
                    "hard_fail": False,
                }
            ],
        }
        self.assertEqual(
            evaluate_rules(extracted, [definition], config, 1)["decision"], "pass"
        )
        extracted["deadline"]["value"] = "2026-10-20"
        config["rules"][0]["value"] = "2026-10-20"
        self.assertEqual(
            evaluate_rules(extracted, [definition], config, 1)["decision"], "review"
        )

    def test_review_malformed_rule_structures_raise_validation_instead_of_server_error(
        self,
    ):
        configs = [rules(group=[]), rules(unknown={})]
        for key in ["field", "operator"]:
            config = rules()
            config["rules"][0][key] = []
            configs.append(config)
        for config in configs:
            with self.assertRaises(ValidationError):
                validate_rules(config, [field()])

    def test_reused_common_field_retains_original_segment_and_job_lineage(self):
        from intelligence.content import extract_fields
        from intelligence.models import ExtractionJob

        record, revision, system = self.pipeline()
        common, common_version = save_schema(
            self.admin,
            self.workspace.pk,
            "Common budget",
            "common-budget",
            "common",
            [field()],
        )
        common.state = "published"
        common.published_version = 1
        common.save()
        record.refresh_from_db()
        shared = ExtractionJob.objects.create(
            workspace=self.workspace,
            actor=self.admin,
            record=record,
            content_version=revision,
            analysis=record.current_analysis,
            classification_version=record.classification_version,
            schema_version=common_version,
            state="completed",
            preview=False,
            result=extract_fields(record.current_analysis, [field()]),
        )
        result, _created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        fact = result.fields["budget"]
        self.assertEqual(fact["reused_from"]["job"], str(shared.pk))
        self.assertEqual(fact["reused_from"]["content_version"], str(revision.pk))
        self.assertEqual(fact["candidates"], shared.result["budget"]["candidates"])
        self.assertEqual(result.relevance["criteria"]["all"], ["Tender"])
