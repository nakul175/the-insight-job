from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from intelligence.models import (
    AuditEvent,
    ContentSchema,
    SystemGrant,
    SystemSourceGrant,
)
from intelligence.systems import (
    activation_gaps,
    create_system,
    current_config,
    duplicate_system,
    request_activation,
    save_configuration,
    validate_relevance,
)

from . import test_batch1 as fixtures


def relevance(**changes):
    return {
        "all": ["tender"],
        "none": ["cancelled"],
        "unknown": "review",
        "examples": [
            {"text": "tender", "expected": "include"},
            {"text": "cancelled tender", "expected": "exclude"},
        ],
        **changes,
    }


class BatchFourTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.BatchOneTests.setUpTestData.__func__(cls)

    def draft(self):
        return create_system(
            self.admin, self.workspace.pk, "Opportunities", "Review permitted tenders"
        )

    def config(self):
        return {
            "source_ids": [str(self.source.pk)],
            "type_keys": [],
            "unknown_type": "review",
            "relevance": relevance(),
        }

    def test_system_starts_draft_owned_and_activation_lists_all_gaps(self):
        system = self.draft()
        self.assertEqual(system.state, "draft")
        self.assertEqual(system.owner, self.admin)
        self.assertEqual(len(activation_gaps(system)), 4)
        with self.assertRaisesMessage(ValidationError, "Published qualification rules"):
            request_activation(self.admin, self.workspace.pk, system.pk)
        system.refresh_from_db()
        self.assertEqual(system.state, "draft")

    def test_duplicate_copies_logic_without_grants_dependencies_or_secrets(self):
        system = self.draft()
        save_configuration(self.admin, self.workspace.pk, system.pk, 0, self.config())
        SystemSourceGrant.objects.get_or_create(system=system, source=self.source)
        copy = duplicate_system(self.admin, self.workspace.pk, system.pk)
        self.assertEqual(current_config(copy)["source_ids"], [])
        self.assertEqual(current_config(copy)["type_keys"], [])
        self.assertEqual(current_config(copy)["relevance"], relevance())
        self.assertFalse(copy.systemsourcegrant_set.exists())
        self.assertEqual(SystemGrant.objects.filter(system=copy).count(), 1)
        self.assertEqual(copy.state, "draft")

    def test_system_configuration_versions_reject_stale_write(self):
        system = self.draft()
        save_configuration(self.admin, self.workspace.pk, system.pk, 0, self.config())
        with self.assertRaisesMessage(ValidationError, "draft changed"):
            save_configuration(
                self.admin, self.workspace.pk, system.pk, 0, self.config()
            )
        self.assertEqual(system.configurations.count(), 1)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="system.draft_saved", object_id=str(system.pk)
            ).exists()
        )

    def test_cross_workspace_and_viewer_writes_are_denied(self):
        system = self.draft()
        with self.assertRaises(PermissionDenied):
            save_configuration(
                self.reviewer, self.workspace.pk, system.pk, 0, self.config()
            )
        config = self.config()
        config["source_ids"] = [
            str(
                fixtures.Source.objects.create(
                    workspace=self.other,
                    owner=self.admin,
                    name="Elsewhere",
                    url="https://other.example.com",
                ).pk
            )
        ]
        with self.assertRaisesMessage(ValidationError, "no longer permitted"):
            save_configuration(self.admin, self.workspace.pk, system.pk, 0, config)

    def test_relevance_validation_checks_examples_contradictions_and_unknown(self):
        validate_relevance(relevance())
        for config in [
            relevance(none=["tender"]),
            relevance(unknown="include"),
            relevance(examples=[]),
            relevance(examples=[{"text": "not matching", "expected": "include"}]),
        ]:
            with self.assertRaises(ValidationError):
                validate_relevance(config)

    def test_types_require_published_nonretired_schema_and_unknown_policy(self):
        system = self.draft()
        schema = ContentSchema.objects.create(
            workspace=self.workspace,
            owner=self.admin,
            name="Tender",
            key="tender",
            scope="type",
        )
        config = self.config()
        config["type_keys"] = ["tender"]
        with self.assertRaisesMessage(ValidationError, "unpublished or retired"):
            save_configuration(self.admin, self.workspace.pk, system.pk, 0, config)
        schema.state = "published"
        schema.published_version = 1
        schema.save()
        save_configuration(self.admin, self.workspace.pk, system.pk, 0, config)
        config["unknown_type"] = "guess"
        with self.assertRaisesMessage(ValidationError, "Unknown"):
            save_configuration(self.admin, self.workspace.pk, system.pk, 1, config)

    def test_system_ui_create_configure_activation_and_duplicate(self):
        self.client.force_login(
            self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )
        response = self.client.post(
            reverse("systems", args=[self.workspace.pk]),
            {"name": "Pilot", "purpose": "Tenders"},
        )
        self.assertEqual(response.status_code, 302)
        system = fixtures.ConsumerSystem.objects.get(name="Pilot")
        self.assertContains(
            self.client.get(response.url), "Published qualification rules"
        )
        config_url = reverse("system-configure", args=[self.workspace.pk, system.pk])
        self.assertContains(self.client.get(config_url), "Unknown")
        response = self.client.post(
            reverse("system-activate", args=[self.workspace.pk, system.pk])
        )
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Activation blocked", status_code=409)
        self.assertEqual(
            self.client.get(
                reverse("system-duplicate", args=[self.workspace.pk, system.pk])
            ).status_code,
            405,
        )

    def sample_fixture(self):
        from intelligence.content import save_schema
        from intelligence.schema_lifecycle import save_samples

        from .test_batch3 import BatchThreeTests, field

        _record, revision, _cap, _created = BatchThreeTests.captured(
            self, text="Budget: 12"
        )
        schema, draft = save_schema(
            self.admin, self.workspace.pk, "Budget", "budget", "type", [field()]
        )
        samples = [
            {
                "content_version": str(revision.pk),
                "fields": {"budget": 12},
                "routing": "include",
                "qualification": "pass",
            }
        ]
        version = save_samples(
            self.admin, self.workspace.pk, schema.pk, "Budget samples", samples
        )
        return schema, draft, version, samples

    def test_schema_publish_requires_passing_samples_and_pins_version(self):
        from intelligence.schema_lifecycle import check_schema, publish_schema

        schema, draft, version, _ = self.sample_fixture()
        check = check_schema(
            self.admin, self.workspace.pk, schema.pk, draft.number, version.pk
        )
        self.assertTrue(check.passed)
        published = publish_schema(
            self.admin, self.workspace.pk, schema.pk, draft.number, check.pk
        )
        self.assertEqual(published.published_version, 1)
        draft.refresh_from_db()
        self.assertEqual(draft.state, "published")

    def test_sample_label_edit_versions_and_invalidates_old_check(self):
        from intelligence.schema_lifecycle import (
            check_schema,
            publish_schema,
            save_samples,
        )

        schema, _draft, version, samples = self.sample_fixture()
        check = check_schema(self.admin, self.workspace.pk, schema.pk, 1, version.pk)
        samples[0]["fields"]["budget"] = 99
        next_version = save_samples(
            self.admin,
            self.workspace.pk,
            schema.pk,
            "Budget samples",
            samples,
            version.sample_set_id,
            1,
        )
        self.assertEqual(version.samples.get().expected_fields["budget"], 12)
        self.assertEqual(next_version.number, 2)
        with self.assertRaisesMessage(ValidationError, "labels changed"):
            publish_schema(self.admin, self.workspace.pk, schema.pk, 1, check.pk)
        self.assertFalse(
            check_schema(
                self.admin, self.workspace.pk, schema.pk, 1, next_version.pk
            ).passed
        )

    def test_empty_expected_fields_cannot_vacuously_pass_and_revocation_hides_checks(
        self,
    ):
        from intelligence.models import SourceGrant
        from intelligence.schema_lifecycle import (
            check_schema,
            permitted_samples,
            save_samples,
        )

        schema, _, version, samples = self.sample_fixture()
        samples[0]["fields"] = {}
        version = save_samples(
            self.admin, self.workspace.pk, schema.pk, "Empty labels", samples
        )
        self.assertFalse(
            check_schema(self.admin, self.workspace.pk, schema.pk, 1, version.pk).passed
        )
        permitted_samples(self.reviewer, version)
        SourceGrant.objects.filter(membership=self.rm, source=self.source).delete()
        with self.assertRaises(PermissionDenied):
            permitted_samples(self.reviewer, version)
        self.client.force_login(
            self.reviewer, backend="django.contrib.auth.backends.ModelBackend"
        )
        response = self.client.get(
            reverse("schema-lifecycle", args=[self.workspace.pk, schema.pk])
        )
        self.assertContains(response, "No permitted sample sets")
        self.assertNotContains(response, "Empty labels")

    def test_breaking_schema_publish_and_retire_list_dependencies(self):
        from intelligence.content import save_schema
        from intelligence.schema_lifecycle import (
            check_schema,
            publish_schema,
            retire_schema,
        )

        from .test_batch3 import field

        schema, _draft, version, _ = self.sample_fixture()
        check = check_schema(self.admin, self.workspace.pk, schema.pk, 1, version.pk)
        publish_schema(self.admin, self.workspace.pk, schema.pk, 1, check.pk)
        system = self.draft()
        config = self.config()
        config["type_keys"] = ["budget"]
        save_configuration(self.admin, self.workspace.pk, system.pk, 0, config)
        schema, newer = save_schema(
            self.admin,
            self.workspace.pk,
            "Budget",
            "budget",
            "type",
            [field(kind="integer")],
            schema_id=schema.pk,
            expected_version=1,
        )
        checked = check_schema(
            self.admin, self.workspace.pk, schema.pk, newer.number, version.pk
        )
        with self.assertRaisesMessage(ValidationError, "Opportunities"):
            publish_schema(
                self.admin, self.workspace.pk, schema.pk, newer.number, checked.pk
            )
        with self.assertRaisesMessage(ValidationError, "Retirement blocked"):
            retire_schema(self.admin, self.workspace.pk, schema.pk)
        self.assertEqual(schema.versions.get(number=1).fields[0]["type"], "number")

    def test_retired_schema_history_readable_but_new_selection_blocked(self):
        from intelligence.schema_lifecycle import (
            check_schema,
            publish_schema,
            retire_schema,
        )

        schema, _, version, _ = self.sample_fixture()
        check = check_schema(self.admin, self.workspace.pk, schema.pk, 1, version.pk)
        publish_schema(self.admin, self.workspace.pk, schema.pk, 1, check.pk)
        retire_schema(self.admin, self.workspace.pk, schema.pk)
        self.client.force_login(
            self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )
        self.assertContains(
            self.client.get(
                reverse("schema-detail", args=[self.workspace.pk, schema.pk])
            ),
            "Version 1",
        )
        config = self.config()
        config["type_keys"] = ["budget"]
        with self.assertRaisesMessage(ValidationError, "retired"):
            save_configuration(
                self.admin, self.workspace.pk, self.draft().pk, 0, config
            )

    def policy_fixture(self, pages=1):
        from intelligence.enrichment import save_policy

        system = self.draft()
        self.source.state = "active"
        self.source.save()
        save_configuration(self.admin, self.workspace.pk, system.pk, 0, self.config())
        policy = save_policy(
            self.admin,
            self.workspace.pk,
            system.pk,
            0,
            {
                "domains": ["example.com"],
                "depth": 1,
                "pages": pages,
                "seconds": 60,
                "bytes": 10000,
                "media_seconds": 0,
                "provider_cost_microunits": 0,
            },
        )
        return system, policy

    def test_destination_declared_mode_is_non_transmitting_and_credentials_block(self):
        from intelligence.destinations import register_destination, test_destination

        system = self.draft()
        destination = register_destination(
            self.admin,
            self.workspace.pk,
            system.pk,
            "https://receiver.example.com/test",
        )
        result = test_destination(
            self.admin, self.workspace.pk, system.pk, destination.pk
        )
        self.assertEqual(result.state, "test_passed")
        self.assertFalse(result.test_report["transmitted"])
        self.assertEqual(result.test_report["reachable"], "unverified")
        with self.assertRaises(ValidationError):
            register_destination(
                self.admin, self.workspace.pk, system.pk, "http://127.0.0.1/"
            )
        destination = register_destination(
            self.admin,
            self.workspace.pk,
            system.pk,
            "https://receiver.example.com/test",
            secret_ref="vault:destination/key",
        )
        self.assertEqual(
            test_destination(
                self.admin, self.workspace.pk, system.pk, destination.pk
            ).state,
            "blocked",
        )

    def test_enrichment_requires_domain_grants_and_zero_provider_media_caps(self):
        from intelligence.enrichment import save_policy

        system, policy = self.policy_fixture()
        for config in [
            {**policy.config, "domains": ["new.example.com"]},
            {**policy.config, "media_seconds": 10},
            {**policy.config, "provider_cost_microunits": 1},
        ]:
            with self.assertRaises(ValidationError):
                save_policy(self.admin, self.workspace.pk, system.pk, 1, config)

    def test_enrichment_empty_trigger_path_and_permission_checked_single_page(self):
        from unittest.mock import patch

        from intelligence.enrichment import enrichment_step, start_enrichment
        from intelligence.models import WorkItem

        system, _ = self.policy_fixture()
        attempt = start_enrichment(
            self.admin,
            self.workspace.pk,
            system.pk,
            self.visible.pk,
            "No missing facts",
            [],
            [],
        )
        self.assertEqual(attempt.state, "completed")
        attempt = start_enrichment(
            self.admin,
            self.workspace.pk,
            system.pk,
            self.visible.pk,
            "Review budget evidence",
            ["https://example.com/evidence"],
            ["budget"],
        )
        with patch("socket.getaddrinfo", return_value=fixtures.PUBLIC_DNS):
            enrichment_step(self.admin, self.workspace.pk, attempt.pk)
        job = WorkItem.objects.get(request_key=f"enrichment:{attempt.pk}:0")
        self.assertEqual(job.limits["pages"], 1)
        self.assertEqual(job.limits["depth"], 0)
        self.assertEqual(job.system_id, system.pk)
        grant = SystemSourceGrant.objects.get(system=system, source=self.source)
        grant.active = False
        grant.save()
        with self.assertRaises(PermissionDenied):
            enrichment_step(self.admin, self.workspace.pk, attempt.pk)
        from intelligence.services import check_work_permission

        self.assertFalse(check_work_permission(job))

    def test_enrichment_coverage_cap_resume_preserves_completed_capture(self):
        from unittest.mock import patch

        from intelligence.enrichment import (
            enrichment_step,
            resume_enrichment,
            save_policy,
            start_enrichment,
        )
        from intelligence.models import Capture, WorkItem

        system, policy = self.policy_fixture()
        attempt = start_enrichment(
            self.admin,
            self.workspace.pk,
            system.pk,
            self.visible.pk,
            "Missing budget",
            ["https://example.com/one", "https://example.com/two"],
            ["budget"],
        )
        with patch("socket.getaddrinfo", return_value=fixtures.PUBLIC_DNS):
            enrichment_step(self.admin, self.workspace.pk, attempt.pk)
        job = WorkItem.objects.get(request_key=f"enrichment:{attempt.pk}:0")
        Capture.objects.create(
            workspace=self.workspace,
            run=job,
            source=self.source,
            url="https://example.com/one",
            final_url="https://example.com/one",
            digest="a" * 64,
            media_type="text/plain",
            original=b"Budget: 12",
            status="captured",
        )
        job.state = "completed"
        job.usage = {"bytes": 10, "seconds": 1}
        job.save()
        result = enrichment_step(self.admin, self.workspace.pk, attempt.pk)
        self.assertEqual(result.state, "cap_reached")
        self.assertIn("pages cap 1", result.reason)
        self.assertEqual(
            result.coverage[0]["captures"], [str(job.capture_set.get().pk)]
        )
        save_policy(
            self.admin, self.workspace.pk, system.pk, 1, {**policy.config, "pages": 2}
        )
        resumed = resume_enrichment(
            self.admin, self.workspace.pk, attempt.pk, "Owner raised page cap"
        )
        self.assertEqual(resumed.usage["pages"], 1)
        self.assertEqual(len(resumed.pending), 1)
        self.assertEqual(resumed.visited, ["https://example.com/one"])
        with patch("socket.getaddrinfo", return_value=fixtures.PUBLIC_DNS):
            enrichment_step(self.admin, self.workspace.pk, resumed.pk)
        self.assertEqual(
            WorkItem.objects.get(request_key=f"enrichment:{resumed.pk}:1").checkpoint[
                "pending"
            ][0]["url"],
            "https://example.com/two",
        )

    def test_system_removing_source_blocks_pending_work_and_does_not_regrant_revoked(
        self,
    ):
        from intelligence.models import WorkItem

        system, _ = self.policy_fixture()
        job = WorkItem.objects.create(
            workspace=self.workspace,
            source=self.source,
            source_version=self.version,
            actor=self.admin,
            system=system,
        )
        config = self.config()
        config["source_ids"] = []
        save_configuration(self.admin, self.workspace.pk, system.pk, 1, config)
        job.refresh_from_db()
        self.assertEqual(job.state, "blocked")
        with self.assertRaisesMessage(ValidationError, "revoked"):
            save_configuration(
                self.admin, self.workspace.pk, system.pk, 2, self.config()
            )

    def test_review_enrichment_duplicate_initial_links_and_failed_transfer_charge(self):
        from unittest.mock import patch

        from intelligence.enrichment import enrichment_step, start_enrichment
        from intelligence.models import WorkItem

        system, _policy = self.policy_fixture()
        attempt = start_enrichment(
            self.admin,
            self.workspace.pk,
            system.pk,
            self.visible.pk,
            "Evidence gap",
            ["https://example.com/a", "https://example.com/a", "https://example.com/b"],
            ["budget"],
        )
        self.assertEqual(len(attempt.pending), 2)
        with patch("socket.getaddrinfo", return_value=fixtures.PUBLIC_DNS):
            enrichment_step(self.admin, self.workspace.pk, attempt.pk)
        job = WorkItem.objects.get(request_key=f"enrichment:{attempt.pk}:0")
        job.state = "action_required"
        job.usage = {"bytes": 0, "seconds": 1}
        job.save()
        result = enrichment_step(self.admin, self.workspace.pk, attempt.pk)
        self.assertEqual(result.state, "cap_reached")
        self.assertEqual(result.usage["pages"], 1)
        self.assertEqual(result.usage["bytes"], 10000)
        self.assertEqual(result.usage["seconds"], 1)
        self.assertEqual(result.coverage[0]["state"], "uncertain_transfer")
        self.assertEqual(WorkItem.objects.filter(system=system).count(), 1)

    def test_review_expired_enrichment_connection_records_gap_without_crashing(self):
        from unittest.mock import patch

        from intelligence.access import NeedsAccess
        from intelligence.enrichment import enrichment_step, start_enrichment

        system, _policy = self.policy_fixture()
        attempt = start_enrichment(
            self.admin,
            self.workspace.pk,
            system.pk,
            self.visible.pk,
            "Evidence gap",
            ["https://example.com/a"],
            ["budget"],
        )
        with (
            patch("socket.getaddrinfo", return_value=fixtures.PUBLIC_DNS),
            patch(
                "intelligence.access.headers_for",
                side_effect=NeedsAccess("Reconnect source"),
            ),
        ):
            result = enrichment_step(self.admin, self.workspace.pk, attempt.pk)
        self.assertEqual(result.coverage[0]["state"], "blocked")
        self.assertIn("Reconnect source", result.coverage[0]["reason"])

    def test_review_completed_enrichment_capture_cannot_be_read_after_source_revocation(
        self,
    ):
        from intelligence.enrichment import enrichment_step, start_enrichment
        from intelligence.models import Capture, Source, SourceVersion, WorkItem

        system, _ = self.policy_fixture(pages=3)
        attempt = start_enrichment(
            self.admin,
            self.workspace.pk,
            system.pk,
            self.visible.pk,
            "Evidence gap",
            ["https://evidence.example.com/a"],
            ["budget"],
        )
        source = Source.objects.create(
            workspace=self.workspace,
            owner=self.admin,
            name="Evidence",
            url="https://evidence.example.com/",
            state="active",
        )
        version = SourceVersion.objects.create(
            source=source, version=1, config={"access": "public", "include": ["/"]}
        )
        grant = SystemSourceGrant.objects.create(system=system, source=source)
        job = WorkItem.objects.create(
            workspace=self.workspace,
            actor=self.admin,
            source=source,
            source_version=version,
            system=system,
            state="completed",
            request_key=f"enrichment:{attempt.pk}:0",
            usage={"bytes": 100, "seconds": 1},
        )
        Capture.objects.create(
            workspace=self.workspace,
            run=job,
            source=source,
            url=source.url,
            digest="b" * 64,
            media_type="text/html",
            original=b'<a href="https://example.com/hidden-discovered-link">hidden</a>',
            status="captured",
        )
        grant.active = False
        grant.save()
        result = enrichment_step(self.admin, self.workspace.pk, attempt.pk)
        self.assertEqual(result.state, "blocked")
        self.assertEqual(result.coverage, [])
        self.assertEqual(WorkItem.objects.filter(system=system).count(), 1)

    def test_review_sample_gate_distinguishes_boolean_and_number_labels(self):
        from intelligence.content import save_schema
        from intelligence.schema_lifecycle import check_schema, save_samples

        from .test_batch3 import BatchThreeTests, field

        _record, revision, _capture, _created = BatchThreeTests.captured(
            self, text="Open: true"
        )
        schema, _draft = save_schema(
            self.admin,
            self.workspace.pk,
            "Open",
            "open",
            "type",
            [field(key="open", kind="boolean")],
        )
        version = save_samples(
            self.admin,
            self.workspace.pk,
            schema.pk,
            "Wrong boolean label",
            [
                {
                    "content_version": str(revision.pk),
                    "fields": {"open": 1},
                    "routing": "include",
                    "qualification": "pass",
                }
            ],
        )
        self.assertFalse(
            check_schema(self.admin, self.workspace.pk, schema.pk, 1, version.pk).passed
        )
