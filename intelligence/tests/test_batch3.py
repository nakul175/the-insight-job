import hashlib
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse

from intelligence.content import (
    classify_record,
    correct_types,
    decide_duplicate,
    duplicate_candidates,
    extract_fields,
    materialize,
    preview_extraction,
    process_extraction,
    save_schema,
    validate_fields,
)
from intelligence.models import (
    AuditEvent,
    Capture,
    ContentRecord,
    ContentType,
    Derivative,
    ExtractionJob,
    SourceGrant,
    WorkItem,
)
from intelligence.runs import claim_next, collect_step, start_run

from . import test_batch1 as fixtures


def field(key="budget", kind="number", **overrides):
    return {
        "key": key,
        "label": key.title(),
        "instruction": key.title(),
        "type": kind,
        "required": True,
        "evidence_required": True,
        "allowed_values": [],
        "unit": "",
        **overrides,
    }


class BatchThreeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.BatchOneTests.setUpTestData.__func__(cls)

    def login(self, user=None):
        self.client.force_login(
            user or self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )

    def captured(self, text="Tender\nBudget: 12 USD", source=None, url=None):
        source = source or self.source
        item = WorkItem.objects.create(
            workspace=source.workspace,
            source=source,
            source_version=self.version,
            actor=self.admin,
        )
        data = text.encode()
        cap = Capture.objects.create(
            workspace=source.workspace,
            source=source,
            run=item,
            url=url or source.url,
            digest=hashlib.sha256(data).hexdigest(),
            media_type="text/plain",
            original=data,
            status="captured",
        )
        record, revision, created = materialize(
            cap,
            {
                "status": "parsed",
                "segments": [{"kind": "text", "section": 1, "text": text}],
                "reason": "",
            },
        )
        return record, revision, cap, created

    def schema(
        self, key="tender", fields=None, published=False, scope="type", system=None
    ):
        schema, version = save_schema(
            self.admin,
            self.workspace.pk,
            key.title(),
            key,
            scope,
            fields or [field(unit="USD")],
            system_id=system.pk if system else None,
        )
        if published:
            schema.state = "published"
            schema.published_version = version.number
            schema.save()
            version.state = "published"
            version.save()
        return schema, version

    def test_us0504_envelope_summary_and_unknown_metadata_keep_original(self):
        record, revision, cap, _ = self.captured()
        self.assertEqual(
            revision.metadata, {"author": None, "date": None, "geography": None}
        )
        self.assertEqual(revision.language, "unknown")
        self.assertEqual(revision.completeness, "complete")
        self.assertEqual(revision.summary["segment_ids"], [0])
        self.assertIn(revision.summary["text"], revision.segments[0]["text"])
        self.assertEqual(bytes(cap.original), record.body.encode())
        self.login()
        response = self.client.get(
            reverse("record-detail", args=[self.workspace.pk, record.pk])
        )
        self.assertContains(response, "Supporting source passage")
        self.assertContains(response, "Geography Unknown")

    def test_us0603_unchanged_capture_reuses_version_changed_capture_preserves_history(
        self,
    ):
        record, old, cap, _ = self.captured()
        derivative = Derivative.objects.create(
            record=record, kind="typed", value={"value": 12}
        )
        same, same_version, second, created = self.captured()
        self.assertFalse(created)
        self.assertEqual(same.pk, record.pk)
        self.assertEqual(same_version.pk, old.pk)
        changed, new, _, created = self.captured("Tender\nBudget: 15 USD")
        self.assertTrue(created)
        self.assertEqual(changed.pk, record.pk)
        self.assertEqual(new.number, 2)
        old.refresh_from_db()
        self.assertIn("12 USD", old.body)
        self.assertIn("15 USD", new.body)
        self.assertIn("-Budget: 12 USD", new.changes["text_diff"])
        derivative.refresh_from_db()
        self.assertTrue(derivative.stale)
        cap.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(cap.content_version_id, second.content_version_id)

    def test_collection_integration_reuses_record_without_duplicate_original_events(
        self,
    ):
        self.source.state = "active"
        self.source.save()
        parsed = {
            "status": "parsed",
            "segments": [{"kind": "section", "section": 1, "text": "Notice"}],
            "reason": "",
        }
        with (
            patch("socket.getaddrinfo", return_value=fixtures.PUBLIC_DNS),
            patch("intelligence.runs.parse_document", return_value=parsed),
        ):
            for request_key in ["first", "second"]:
                start_run(self.admin, self.source, request_key)
                collect_step(
                    claim_next(),
                    fetch=lambda *a, **kw: {
                        "state": "captured",
                        "bytes": b"<p>Notice</p>",
                        "format": "text/html",
                        "missing": [],
                    },
                )
        captures = list(Capture.objects.filter(source=self.source))
        self.assertEqual(len(captures), 2)
        self.assertEqual(captures[0].record_id, captures[1].record_id)
        self.assertEqual(captures[0].content_version_id, captures[1].content_version_id)

    def test_us0702_exact_field_errors_and_draft_edit_conflicts_leave_live_version(
        self,
    ):
        with self.assertRaises(ValidationError) as error:
            validate_fields([field(), field()])
        self.assertIn("fields[1].key", error.exception.message_dict)
        with self.assertRaises(ValidationError):
            validate_fields([field(kind="enum")])
        schema, version = self.schema(published=True)
        _, draft = save_schema(
            self.admin,
            self.workspace.pk,
            schema.name,
            schema.key,
            schema.scope,
            [field("amount")],
            schema_id=schema.pk,
            expected_version=1,
        )
        schema.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(schema.published_version, 1)
        self.assertEqual(version.fields[0]["key"], "budget")
        self.assertEqual(draft.state, "draft")
        with self.assertRaises(ValidationError):
            save_schema(
                self.admin,
                self.workspace.pk,
                schema.name,
                schema.key,
                schema.scope,
                [field()],
                schema_id=schema.pk,
                expected_version=1,
            )
        self.assertEqual(schema.versions.count(), 2)

    def test_us0701_registry_empty_and_schema_owner_scope_versions(self):
        self.login()
        response = self.client.get(reverse("schemas", args=[self.workspace.pk]))
        self.assertContains(response, "No schemas yet")
        schema, _ = self.schema()
        response = self.client.get(
            reverse("schema-detail", args=[self.workspace.pk, schema.pk])
        )
        self.assertContains(response, "adminuser")
        self.assertContains(response, "draft")
        self.assertContains(response, "Budget")
        response = self.client.get(
            reverse("schema-edit", args=[self.workspace.pk, schema.pk])
        )
        self.assertContains(response, "Saving creates a new immutable draft version")

    def test_us0505_mixed_published_types_unknown_and_correction_jobs(self):
        record, _, _, _ = self.captured("Type: tender, grant\nBudget: 12 USD")
        self.schema("tender", published=True)
        self.schema("grant", published=True)
        self.schema("draft", published=False)
        classify_record(self.admin, record)
        record.refresh_from_db()
        self.assertEqual(
            set(record.type_assignments.values_list("key", flat=True)),
            {"tender", "grant"},
        )
        self.assertEqual(
            ExtractionJob.objects.filter(record=record, state="queued").count(), 2
        )
        previous = record.classification_version
        correct_types(
            self.reviewer,
            record,
            [],
            "Evidence does not support either route.",
            previous,
        )
        record.refresh_from_db()
        self.assertFalse(record.type_assignments.exists())
        self.assertTrue(
            ExtractionJob.objects.filter(
                record=record, state="action_required"
            ).exists()
        )
        with self.assertRaises(ValidationError):
            correct_types(self.reviewer, record, ["tender"], "stale editor", previous)
        self.login(self.reviewer)
        response = self.client.get(
            reverse("records", args=[self.workspace.pk]), {"type": "unknown"}
        )
        self.assertContains(response, "Type: tender, grant")

    def test_us0506_candidates_conform_conflicts_and_unsupported_facts_stay_unknown(
        self,
    ):
        record, revision, _, _ = self.captured(
            "Budget: 12 USD\nBudget: 15 USD\nCategory: tender\nOpen: true\nDeadline: 2026-12-01"
        )
        fields = [
            field(unit="USD"),
            field("category", "enum", allowed_values=["tender", "grant"]),
            field("open", "boolean"),
            field("deadline", "date"),
            field("author", "text"),
        ]
        result = extract_fields(revision, fields)
        self.assertEqual(result["budget"]["status"], "conflict")
        self.assertIsNone(result["budget"]["value"])
        self.assertEqual(len(result["budget"]["candidates"]), 2)
        self.assertEqual(result["category"]["value"], "tender")
        self.assertIs(result["open"]["value"], True)
        self.assertEqual(result["deadline"]["value"], "2026-12-01")
        self.assertEqual(result["author"]["status"], "unknown")
        self.assertEqual(result["author"]["candidates"], [])
        schema, version = self.schema(fields=fields)
        job = preview_extraction(self.reviewer, record, version)
        self.assertTrue(job.preview)
        self.assertEqual(job.state, "preview")
        self.assertFalse(Derivative.objects.filter(record=record).exists())
        self.assertEqual(schema.state, "draft")

    def test_us0506_invalid_units_enums_and_long_values_cannot_become_facts(self):
        _, revision, _, _ = self.captured(
            "Budget: 12 EUR\nCategory: unsupported\nAuthor: " + "a" * 2001
        )
        result = extract_fields(
            revision,
            [
                field(unit="USD"),
                field("category", "enum", allowed_values=["tender"]),
                field("author", "text"),
            ],
        )
        for value in result.values():
            self.assertEqual(value["status"], "unknown")
            self.assertIsNone(value["value"])
            self.assertTrue(value["candidates"][0]["error"])

    def test_us0602_field_evidence_is_versioned_and_revocation_hides_passages(self):
        record, _, _, _ = self.captured()
        _, schema_version = self.schema()
        job = preview_extraction(self.reviewer, record, schema_version)
        self.login(self.reviewer)
        url = reverse(
            "field-evidence", args=[self.workspace.pk, record.pk, job.pk, "budget"]
        )
        response = self.client.get(url)
        self.assertContains(response, "12 USD")
        self.assertContains(response, "exact_label")
        self.assertContains(response, "reviewer")
        self.captured("Budget: 20 USD")
        self.assertContains(self.client.get(url), "This result is stale")
        SourceGrant.objects.filter(membership=self.rm).delete()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_system_schema_evidence_requires_both_system_and_source_permission(self):
        record, _, _, _ = self.captured()
        _schema, version = self.schema(scope="system", system=self.a)
        with self.assertRaises(PermissionDenied):
            preview_extraction(self.admin, record, version)
        # Directly injected historical result must still be hidden from the evidence viewer.
        job = ExtractionJob.objects.create(
            workspace=self.workspace,
            record=record,
            actor=self.admin,
            classification_version=record.classification_version,
            content_version=record.revisions.first(),
            schema_version=version,
            result={"budget": {"candidates": [{"segment": 0}]}},
        )
        self.login(self.reviewer)
        self.assertEqual(
            self.client.get(
                reverse(
                    "field-evidence",
                    args=[self.workspace.pk, record.pk, job.pk, "budget"],
                )
            ).status_code,
            404,
        )

    def test_us0604_exact_identity_preserves_separate_provenance_and_acl(self):
        left, _, _, _ = self.captured("Same notice")
        hidden, _, _, _ = self.captured("Same notice", source=self.secret)
        self.assertEqual(left.identity_id, hidden.identity_id)
        candidates = duplicate_candidates(self.reviewer, left)
        self.assertFalse(candidates["exact"].exists())
        with self.assertRaises(PermissionDenied):
            decide_duplicate(self.reviewer, left, hidden, "link", "same bytes")
        self.login(self.reviewer)
        response = self.client.get(
            reverse("duplicates", args=[self.workspace.pk, left.pk])
        )
        self.assertNotContains(response, "Secret source")
        self.assertNotContains(response, str(hidden.pk))

    def test_us0604_approximate_reviewer_decision_links_identity_without_granting_access(
        self,
    ):
        left, _, _, _ = self.captured("Tender notice alpha")
        right, _, _, _ = self.captured(
            "Tender notice beta", url="https://example.com/other"
        )
        self.assertTrue(duplicate_candidates(self.reviewer, left)["approximate"])
        decide_duplicate(
            self.reviewer, left, right, "link", "Same procurement with updated heading."
        )
        left.refresh_from_db()
        right.refresh_from_db()
        self.assertEqual(left.identity_id, right.identity_id)
        self.assertNotEqual(left.pk, right.pk)
        self.assertEqual(Capture.objects.filter(record=right).count(), 1)
        self.assertTrue(AuditEvent.objects.filter(action="duplicate.decided").exists())

    def test_us0601_filters_count_return_state_and_empty_vs_failure(self):
        record, _, _, _ = self.captured("Searchable tender")
        ContentType.objects.create(record=record, key="tender")
        self.login(self.reviewer)
        params = {
            "q": "Searchable",
            "type": "tender",
            "format": "text/plain",
            "language": "unknown",
            "sort": "title",
            "page": "1",
        }
        response = self.client.get(reverse("records", args=[self.workspace.pk]), params)
        self.assertEqual(response.context["count"], 1)
        self.assertEqual(list(response.context["records"]), [record])
        self.assertContains(response, "back=")
        self.assertIn("q=Searchable", response.context["back"])
        response = self.client.get(
            reverse("record-detail", args=[self.workspace.pk, record.pk]),
            {"back": "q=Searchable&type=tender&page=1"},
        )
        self.assertContains(response, "q=Searchable&amp;type=tender&amp;page=1#record-")
        response = self.client.get(
            reverse("records", args=[self.workspace.pk]), {"q": "absent"}
        )
        self.assertContains(response, "No matching permitted content")
        self.assertEqual(response.context["count"], 0)
        with patch(
            "intelligence.content_views.filtered_records",
            side_effect=DatabaseError("unavailable"),
        ):
            response = self.client.get(reverse("records", args=[self.workspace.pk]))
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Content search unavailable", status_code=503)

    def test_us0605_export_preview_fields_restrictions_filters_and_audit(self):
        record, _, cap, _ = self.captured()
        self.login(self.reviewer)
        url = reverse("record-export", args=[self.workspace.pk])
        response = self.client.get(
            url,
            {
                "preview": "1",
                "q": "Tender",
                "type": "unknown",
                "fields": ["title", "body", "session_ciphertext"],
            },
        )
        self.assertContains(response, "Source does not grant export permission")
        self.assertContains(response, "Unsupported or restricted field")
        self.assertContains(response, 'name="type" value="unknown"')
        SourceGrant.objects.filter(membership=self.rm, source=self.source).update(
            can_export=True
        )
        response = self.client.get(
            url, {"download": "1", "fields": ["title", "originals"], "q": "Tender"}
        )
        row = response.json()["records"][0]
        self.assertEqual(set(row), {"title", "originals"})
        self.assertIn(str(cap.pk), row["originals"][0])
        event = AuditEvent.objects.filter(action="records.exported").latest(
            "created_at"
        )
        self.assertEqual(event.details["format"], "json")
        self.assertEqual(event.details["versions"][0]["version"], record.version)
        self.assertEqual(event.details["filters"]["q"], ["Tender"])
        cap.status = "quarantined"
        cap.save()
        self.assertEqual(
            self.client.get(
                url, {"fields": ["originals"], "ids": [str(record.pk)]}
            ).json()["records"][0]["originals"],
            [],
        )

    def test_us0605_csv_formula_protection_and_bounded_exports(self):
        self.login()
        self.visible.title = '=HYPERLINK("bad")'
        self.visible.save()
        response = self.client.get(
            reverse("record-export", args=[self.workspace.pk]),
            {
                "ids": [str(self.visible.pk)],
                "fields": ["title"],
                "export_format": "csv",
            },
        )
        self.assertIn("'=HYPERLINK", response.content.decode())
        ContentRecord.objects.bulk_create(
            [
                ContentRecord(
                    workspace=self.workspace, source=self.source, title=f"Row {i}"
                )
                for i in range(101)
            ]
        )
        response = self.client.get(
            reverse("record-export", args=[self.workspace.pk]), {"preview": "1"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Export at most 100 records", status_code=400)

    def test_queued_extraction_rechecks_revocation_and_stale_classification(self):
        record, _, _, _ = self.captured()
        self.schema(published=True)
        correct_types(
            self.reviewer,
            record,
            ["tender"],
            "supported",
            record.classification_version,
        )
        queued = ExtractionJob.objects.get(record=record, state="queued")
        SourceGrant.objects.filter(membership=self.rm).delete()
        self.assertEqual(process_extraction(queued.pk).state, "blocked")
        self.assertFalse(Derivative.objects.filter(record=record).exists())

    def test_malformed_schema_type_unicode_labels_and_candidate_caps_are_safe(self):
        with self.assertRaises(ValidationError):
            validate_fields([field(type={"bad": "type"})])
        _, revision, _, _ = self.captured("Straße: 12")
        result = extract_fields(revision, [field(instruction="Straße")])
        self.assertEqual(result["budget"]["value"], 12)
        _, revision, _, _ = self.captured(
            "\n".join(["Budget: 12"] * 100 + ["Budget: 15"])
        )
        result = extract_fields(revision, [field()])
        self.assertTrue(result["budget"]["candidate_limit_reached"])
        self.assertIsNone(result["budget"]["value"])

    def test_unstructured_type_mentions_remain_unknown(self):
        record, _, _, _ = self.captured("This is not a tender.")
        self.schema(published=True)
        classify_record(self.admin, record)
        self.assertFalse(record.type_assignments.exists())

    def test_preview_form_renders_field_link_after_post(self):
        record, _, _, _ = self.captured()
        _, version = self.schema()
        self.login()
        response = self.client.post(
            reverse("extract-preview", args=[self.workspace.pk, record.pk]),
            {"schema_version": str(version.pk)},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ">budget</a>")

    def test_review_classification_rejects_capture_changed_before_type_commit(self):
        record, _, _, _ = self.captured("Type: tender\nBudget: 12 USD")
        self.schema(published=True)
        original = correct_types

        def race(*args, **kwargs):
            self.captured("Type: unknown\nBudget: 20 USD")
            return original(*args, **kwargs)

        with (
            patch("intelligence.content.correct_types", side_effect=race),
            self.assertRaises(ValidationError),
        ):
            classify_record(self.admin, record)
        self.assertFalse(record.type_assignments.exists())
        self.assertFalse(ExtractionJob.objects.filter(record=record).exists())

    def test_review_recapture_invalidates_previous_types_with_history(self):
        record, _, _, _ = self.captured("Type: tender\nBudget: 12 USD")
        self.schema(published=True)
        classify_record(self.admin, record)
        self.assertTrue(record.type_assignments.exists())
        changed, _, _, _ = self.captured("Type: unknown\nBudget: 20 USD")
        changed.refresh_from_db()
        self.assertFalse(changed.type_assignments.exists())
        self.assertEqual(
            changed.classification_version,
            changed.classificationchange_set.latest("created_at").version,
        )
        self.assertTrue(
            changed.classificationchange_set.filter(
                reason__contains="require reclassification"
            ).exists()
        )

    def test_review_parse_recovery_reuses_source_version_and_pins_new_analysis(self):
        body = "Budget: 12 USD"
        item = WorkItem.objects.create(
            workspace=self.workspace,
            source=self.source,
            source_version=self.version,
            actor=self.admin,
        )
        cap = Capture.objects.create(
            workspace=self.workspace,
            source=self.source,
            run=item,
            url=self.source.url,
            digest=hashlib.sha256(body.encode()).hexdigest(),
            media_type="text/plain",
            original=body.encode(),
            status="partial",
        )
        record, version, _ = materialize(
            cap,
            {
                "status": "failed",
                "segments": [],
                "reason": "Transient parser worker failure",
            },
        )
        failed = record.current_analysis
        _, schema_version = self.schema()
        old_job = preview_extraction(self.admin, record, schema_version)
        recovered, reused, _, created = self.captured(body)
        self.assertFalse(created)
        self.assertEqual(reused.pk, version.pk)
        self.assertEqual(recovered.revisions.count(), 1)
        self.assertEqual(recovered.body, body)
        self.assertEqual(recovered.current_analysis.number, 2)
        failed.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(failed.body, "")
        self.assertEqual(version.body, "")
        new_job = preview_extraction(self.admin, recovered, schema_version)
        self.assertEqual(new_job.result["budget"]["value"], 12)
        self.assertNotEqual(old_job.analysis_id, new_job.analysis_id)
        self.captured(body)
        self.assertEqual(version.analyses.count(), 2)
        self.login()
        response = self.client.get(
            reverse("record-detail", args=[self.workspace.pk, record.pk])
        )
        self.assertContains(response, "Budget: 12 USD")
        self.assertContains(response, "Immutable processing revision 2")
        self.assertContains(
            self.client.get(
                reverse(
                    "field-evidence",
                    args=[self.workspace.pk, record.pk, old_job.pk, "budget"],
                )
            ),
            "This result is stale",
        )

    def test_review_duplicate_unlink_splits_group_and_preserves_other_links(self):
        left, _, _, _ = self.captured("Tender alpha")
        middle, _, _, _ = self.captured("Tender beta", url="https://example.com/b")
        right, _, _, _ = self.captured("Tender gamma", url="https://example.com/c")
        decide_duplicate(self.admin, left, middle, "link", "same opportunity")
        decide_duplicate(self.admin, middle, right, "link", "same opportunity")
        for record in [left, middle, right]:
            record.refresh_from_db()
        self.assertEqual(left.identity_id, right.identity_id)
        decide_duplicate(
            self.admin,
            left,
            middle,
            "separate",
            "Different source opportunities after review.",
        )
        for record in [left, middle, right]:
            record.refresh_from_db()
        self.assertNotEqual(left.identity_id, middle.identity_id)
        self.assertEqual(middle.identity_id, right.identity_id)

    def test_review_duplicate_unlink_rejects_inconsistent_remaining_path(self):
        left, _, _, _ = self.captured("Tender alpha")
        middle, _, _, _ = self.captured("Tender beta", url="https://example.com/b")
        right, _, _, _ = self.captured("Tender gamma", url="https://example.com/c")
        decide_duplicate(self.admin, left, middle, "link", "same")
        decide_duplicate(self.admin, middle, right, "link", "same")
        decide_duplicate(self.admin, left, right, "link", "same")
        with self.assertRaises(ValidationError):
            decide_duplicate(
                self.admin, left, middle, "separate", "separate despite other path"
            )

    def test_review_default_json_export_retains_legacy_body_contract(self):
        self.login()
        response = self.client.get(
            reverse("record-export", args=[self.workspace.pk]),
            {"ids": [str(self.visible.pk)]},
        )
        self.assertEqual(
            set(response.json()["records"][0]), {"id", "title", "body", "version"}
        )
        self.assertEqual(response.json()["records"][0]["body"], self.visible.body)

    def test_review_retained_separation_cannot_be_undone_by_indirect_link(self):
        left, _, _, _ = self.captured("Tender alpha")
        middle, _, _, _ = self.captured("Tender beta", url="https://example.com/b")
        right, _, _, _ = self.captured("Tender gamma", url="https://example.com/c")
        decide_duplicate(
            self.admin, left, right, "separate", "Different opportunities."
        )
        decide_duplicate(self.admin, left, middle, "link", "Same opportunity.")
        with self.assertRaises(ValidationError):
            decide_duplicate(
                self.admin,
                middle,
                right,
                "link",
                "Would silently contradict the retained separation.",
            )
        for record in [left, middle, right]:
            record.refresh_from_db()
        self.assertEqual(left.identity_id, middle.identity_id)
        self.assertNotEqual(left.identity_id, right.identity_id)
        self.assertEqual(
            left.duplicate_left.filter(decision="link").count()
            + left.duplicate_right.filter(decision="link").count(),
            1,
        )
