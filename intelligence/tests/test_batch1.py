import socket
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from intelligence.collector import sample_report
from intelligence.models import (
    AuditEvent,
    Connection,
    ConsumerSystem,
    ContentRecord,
    DeliveryReceipt,
    Derivative,
    Schedule,
    Source,
    SourceGrant,
    SourceTest,
    SourceVersion,
    SystemGrant,
    SystemSourceGrant,
    Withdrawal,
    WorkItem,
)
from intelligence.permissions import records_for, sources_for, system_can_read
from intelligence.services import (
    accept_invitation,
    archive_source,
    change_role,
    check_work_permission,
    invite,
    register_source,
    revoke_membership,
    revoke_source,
    update_source_config,
)
from intelligence.source_testing import activate, test_source
from intelligence.urlsafety import canonical_url, scope_decision
from workspaces.models import Membership, Workspace

PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


class BatchOneTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.admin = U.objects.create_user(
            "adminuser", email="admin@example.com", password="test-password"
        )
        cls.reviewer = U.objects.create_user(
            "reviewer", email="reviewer@example.com", password="test-password"
        )
        cls.invitee = U.objects.create_user(
            "invitee", email="invitee@example.com", password="test-password"
        )
        cls.workspace = Workspace.objects.create(name="Desk")
        cls.other = Workspace.objects.create(name="Other")
        cls.am = Membership.objects.create(
            user=cls.admin, workspace=cls.workspace, role="administrator"
        )
        cls.rm = Membership.objects.create(
            user=cls.reviewer, workspace=cls.workspace, role="reviewer"
        )
        cls.source = Source.objects.create(
            workspace=cls.workspace,
            owner=cls.admin,
            name="Allowed",
            url="https://example.com/",
        )
        cls.secret = Source.objects.create(
            workspace=cls.workspace,
            owner=cls.admin,
            name="Secret source",
            url="https://secret.example.com/",
        )
        cls.version = SourceVersion.objects.create(
            source=cls.source,
            version=1,
            config={
                "access": "public",
                "include": ["/"],
                "exclude": ["/private"],
                "partial_policy": "reject",
            },
        )
        SourceGrant.objects.create(membership=cls.rm, source=cls.source)
        cls.a = ConsumerSystem.objects.create(workspace=cls.workspace, name="A")
        cls.b = ConsumerSystem.objects.create(workspace=cls.workspace, name="B")
        SystemGrant.objects.create(membership=cls.rm, system=cls.a)
        cls.visible = ContentRecord.objects.create(
            workspace=cls.workspace,
            source=cls.source,
            title="Visible",
            body="visible content",
            status="review",
        )
        cls.hidden = ContentRecord.objects.create(
            workspace=cls.workspace,
            source=cls.secret,
            title="TOP SECRET",
            body="secret contents",
            status="review",
        )
        Derivative.objects.create(
            record=cls.hidden, kind="summary", value={"text": "secret summary"}
        )

    def login(self, user=None):
        self.client.force_login(
            user or self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )

    def job(self, **kw):
        return WorkItem.objects.create(
            workspace=self.workspace,
            source=self.source,
            source_version=self.version,
            actor=self.admin,
            **kw,
        )

    def test_us0102_invite_acceptance_applies_role_and_scopes_single_use(self):
        inv, token = invite(
            self.admin,
            self.workspace.pk,
            self.invitee.email,
            "reviewer",
            [self.source.pk],
            [self.a.pk],
        )
        self.assertNotEqual(inv.token_hash, token)
        m = accept_invitation(self.invitee, token)
        self.assertEqual(m.role, "reviewer")
        self.assertEqual(
            list(sources_for(self.invitee, self.workspace.pk)), [self.source]
        )
        with self.assertRaises(ValidationError):
            accept_invitation(self.invitee, token)

    def test_us0102_wrong_identity_expiry_and_cross_workspace_scopes_rejected(self):
        inv, token = invite(
            self.admin, self.workspace.pk, self.invitee.email, "reviewer"
        )
        with self.assertRaises(ValidationError):
            accept_invitation(self.reviewer, token)
        inv.expires_at = timezone.now() - timedelta(seconds=1)
        inv.save()
        with self.assertRaises(ValidationError):
            accept_invitation(self.invitee, token)
        foreign = Source.objects.create(
            workspace=self.other,
            owner=self.admin,
            name="foreign",
            url="https://foreign.example.com/",
        )
        with self.assertRaises(ValidationError):
            invite(
                self.admin,
                self.workspace.pk,
                self.invitee.email,
                "reviewer",
                [foreign.pk],
            )

    def test_us0102_system_scope_denied_in_ui_and_api(self):
        self.login(self.reviewer)
        for name in ["system-detail", "system-api"]:
            self.assertEqual(
                self.client.get(
                    reverse(name, args=[self.workspace.pk, self.a.pk])
                ).status_code,
                200,
            )
            response = self.client.get(
                reverse(name, args=[self.workspace.pk, self.b.pk])
            )
            self.assertEqual(response.status_code, 404)
            self.assertNotIn(b">B<", response.content)

    def test_us0102_role_change_is_immediate_and_audited(self):
        change_role(self.admin, self.workspace.pk, self.rm.pk, "operator")
        self.rm.refresh_from_db()
        self.assertEqual(self.rm.role, "operator")
        change_role(self.admin, self.workspace.pk, self.rm.pk, "viewer")
        with self.assertRaises(PermissionDenied):
            register_source(
                self.reviewer,
                self.workspace.pk,
                "x",
                "https://example.com/",
                self.admin,
            )
        self.assertEqual(
            AuditEvent.objects.filter(action="membership.role_changed").count(), 2
        )

    def test_us0103_derivatives_search_export_file_and_direct_record_enforce_source_grants(
        self,
    ):
        self.login(self.reviewer)
        response = self.client.get(reverse("records", args=[self.workspace.pk]))
        self.assertContains(response, "Visible")
        self.assertNotContains(response, "TOP SECRET")
        for name in ["record-detail", "record-file"]:
            self.assertEqual(
                self.client.get(
                    reverse(name, args=[self.workspace.pk, self.hidden.pk])
                ).status_code,
                404,
            )
        response = self.client.get(reverse("record-export", args=[self.workspace.pk]))
        self.assertEqual(response.json()["records"], [])
        SourceGrant.objects.filter(membership=self.rm, source=self.source).update(
            can_export=True
        )
        data = self.client.get(
            reverse("record-export", args=[self.workspace.pk])
        ).json()["records"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["title"], "Visible")
        SourceGrant.objects.filter(membership=self.rm).delete()
        self.assertFalse(records_for(self.reviewer, self.workspace.pk).exists())

    def test_us0103_system_without_grant_cannot_receive_source(self):
        self.assertFalse(system_can_read(self.a, self.source))
        SystemSourceGrant.objects.create(system=self.a, source=self.source)
        self.assertTrue(system_can_read(self.a, self.source))
        self.assertFalse(system_can_read(self.a, self.secret))

    def test_us0104_membership_revocation_blocks_active_session_and_queued_work(self):
        self.rm.role = "operator"
        self.rm.save()
        job = self.job()
        job.actor = self.reviewer
        job.save()
        self.login(self.reviewer)
        revoke_membership(self.admin, self.workspace.pk, self.rm.pk)
        self.assertEqual(
            self.client.get(reverse("overview", args=[self.workspace.pk])).status_code,
            404,
        )
        job.refresh_from_db()
        self.assertEqual(job.state, "blocked")
        self.assertFalse(check_work_permission(job))

    def test_us0104_source_revocation_blocks_jobs_and_queues_truthful_withdrawals(self):
        SystemSourceGrant.objects.create(system=self.a, source=self.source)
        job = self.job(system=self.a)
        for supported in [True, False]:
            DeliveryReceipt.objects.create(
                workspace=self.workspace,
                source=self.source,
                system=self.a,
                external_key=str(supported),
                supports_withdrawal=supported,
            )
        revoke_source(self.admin, self.workspace.pk, self.source.pk, self.a.pk)
        job.refresh_from_db()
        self.assertEqual(job.state, "blocked")
        self.assertFalse(check_work_permission(job))
        self.assertEqual(
            set(Withdrawal.objects.values_list("state", flat=True)),
            {"queued", "manual_follow_up"},
        )

    def test_us0105_counts_and_drilldown_include_only_visible_recent_content(self):
        self.login(self.reviewer)
        ContentRecord.objects.create(
            workspace=self.workspace,
            source=self.source,
            title="old",
            created_at=timezone.now() - timedelta(days=10),
            status="review",
        )
        response = self.client.get(reverse("overview", args=[self.workspace.pk]))
        self.assertEqual(response.context["record_count"], 1)
        self.assertEqual(response.context["issue_count"], 1)
        response = self.client.get(
            reverse("records", args=[self.workspace.pk]),
            {"status": "review", "window": "7d"},
        )
        self.assertContains(response, "Visible")
        self.assertNotContains(response, "TOP SECRET")
        self.assertNotContains(response, ">old<")

    def test_us0105_no_sources_and_refresh_failure_are_distinct(self):
        SourceGrant.objects.filter(membership=self.rm).delete()
        self.login(self.reviewer)
        self.assertContains(
            self.client.get(reverse("overview", args=[self.workspace.pk])),
            "Start with a source",
        )
        from django.db import DatabaseError

        with patch(
            "intelligence.views.sources_for", side_effect=DatabaseError("private")
        ):
            response = self.client.get(reverse("overview", args=[self.workspace.pk]))
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Overview unavailable", status_code=503)
        self.assertNotContains(response, "private", status_code=503)

    @patch("socket.getaddrinfo", return_value=PUBLIC_DNS)
    def test_us0201_register_stable_draft_and_canonical_duplicate(self, dns):
        source, created = register_source(
            self.admin,
            self.workspace.pk,
            "Example",
            "https://EXAMPLE.org#fragment",
            self.admin,
        )
        self.assertTrue(created)
        self.assertEqual(source.state, "draft")
        duplicate, created = register_source(
            self.admin, self.workspace.pk, "Again", "https://example.org/", self.admin
        )
        self.assertFalse(created)
        self.assertEqual(duplicate.pk, source.pk)

    def test_us0201_private_unsupported_and_credential_urls_are_rejected(self):
        for url in [
            "file:///etc/passwd",
            "http://127.0.0.1",
            "http://169.254.169.254",
            "http://[::1]",
            "https://user:pass@example.com",
            "https://localhost",
            "http://2130706433",
        ]:
            with self.subTest(url=url), self.assertRaises(ValidationError):
                canonical_url(url)
        with (
            patch(
                "socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.1", 443))]
            ),
            self.assertRaises(ValidationError),
        ):
            canonical_url("https://example.com")

    @patch("socket.getaddrinfo", return_value=PUBLIC_DNS)
    def test_us0203_scope_decisions_and_pinned_versions(self, dns):
        job = self.job()
        new = update_source_config(
            self.admin,
            self.source,
            {"include": ["/notices"], "exclude": ["/notices/private"]},
        )
        self.assertEqual(
            scope_decision(
                self.source.url, "https://example.com/notices/a", new.config
            ),
            (True, "Included by /notices"),
        )
        self.assertFalse(
            scope_decision(
                self.source.url, "https://example.com/notices/private/a", new.config
            )[0]
        )
        self.assertFalse(
            scope_decision(
                self.source.url, "https://other.example.com/notices", new.config
            )[0]
        )
        job.refresh_from_db()
        self.assertEqual(job.source_version_id, self.version.pk)
        self.assertEqual(new.version, 2)
        self.assertEqual(self.version.config["include"], ["/"])

    @patch("socket.getaddrinfo", return_value=PUBLIC_DNS)
    def test_us0205_sample_has_no_delivery_and_activation_requires_current_passing_test(
        self, dns
    ):
        capture = {
            "state": "captured",
            "bytes": b"<h1>Actual notice</h1><p>"
            + (b"Useful public source text. " * 5)
            + b"</p>",
            "format": "text/html",
            "missing": [],
        }
        with patch("intelligence.source_testing.fetch_public", return_value=capture):
            result = test_source(self.admin, self.source)
        self.assertEqual(result.state, "passed")
        self.assertFalse(result.report["downstream_delivery"])
        self.assertFalse(DeliveryReceipt.objects.exists())
        activate(self.admin, self.source, result.pk)
        self.source.refresh_from_db()
        self.assertEqual(self.source.state, "active")
        update_source_config(self.admin, self.source, {"exclude": ["/new"]})
        with self.assertRaises(SourceTest.DoesNotExist):
            activate(self.admin, self.source, result.pk)

    @patch("socket.getaddrinfo", return_value=PUBLIC_DNS)
    def test_us0205_missing_media_and_empty_capture_do_not_silently_activate(self, dns):
        for body, expected in [
            (b'<video src="a.mp4"></video><p>Text</p>', "partial"),
            (b"<script>x</script>", "failed"),
        ]:
            with patch(
                "intelligence.source_testing.fetch_public",
                return_value={
                    "state": "captured",
                    "bytes": body,
                    "format": "text/html",
                    "missing": [],
                },
            ):
                result = test_source(self.admin, self.source)
            self.assertEqual(result.state, expected)
            with self.assertRaises(ValidationError):
                activate(self.admin, self.source, result.pk)

        update_source_config(
            self.admin,
            self.source,
            {
                "partial_policy": "accept_documented",
                "partial_reason": "Text-only pilot; video manually reviewed",
            },
        )
        with patch(
            "intelligence.source_testing.fetch_public",
            return_value={
                "state": "captured",
                "bytes": b"<video></video><p>"
                + (b"Useful documented text. " * 5)
                + b"</p>",
                "format": "text/html",
                "missing": [],
            },
        ):
            result = test_source(self.admin, self.source)
        activate(self.admin, self.source, result.pk)

    @patch("socket.getaddrinfo", return_value=PUBLIC_DNS)
    def test_us0205_html_shell_with_too_little_visible_text_cannot_activate(self, dns):
        with patch(
            "intelligence.source_testing.fetch_public",
            return_value={
                "state": "captured",
                "bytes": (
                    b"<html><body><div id='app'>Tenders Search</div>"
                    b"<script type='module' src='/app.js'></script></body></html>"
                ),
                "format": "text/html",
                "missing": [],
            },
        ):
            result = test_source(self.admin, self.source)

        self.assertEqual(result.state, "failed")
        self.assertEqual(result.report["completeness"], "insufficient visible text")
        self.assertEqual(result.report["text_characters"], 14)
        self.assertIn("too little visible text", result.report["missing"][0]["reason"])
        with self.assertRaises(ValidationError):
            activate(self.admin, self.source, result.pk)

    def test_us0205_short_complete_html_notice_remains_eligible(self):
        report = sample_report(
            {
                "state": "captured",
                "bytes": b"<article><h1>Award closed</h1><p>Reference 42</p></article>",
                "format": "text/html",
                "missing": [],
            }
        )

        self.assertEqual(report["state"], "passed")
        self.assertLess(report["text_characters"], 100)

    def test_us0205_app_shell_at_visible_text_boundary_remains_eligible(self):
        visible_text = b"x" * 100
        report = sample_report(
            {
                "state": "captured",
                "bytes": (
                    b"<div id='root'>"
                    + visible_text
                    + b"</div><script src='/app.js'></script>"
                ),
                "format": "text/html",
                "missing": [],
            }
        )

        self.assertEqual(report["state"], "passed")
        self.assertEqual(report["text_characters"], 100)

    def test_us0205_hidden_text_does_not_make_an_empty_app_shell_eligible(self):
        report = sample_report(
            {
                "state": "captured",
                "bytes": (
                    b"<div id='app'></div><div hidden>"
                    + (b"metadata " * 25)
                    + b"</div><script src='/app.js'></script>"
                ),
                "format": "text/html",
                "missing": [],
            }
        )

        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["text_characters"], 0)

    def test_us0205_short_server_rendered_notice_inside_root_remains_eligible(self):
        report = sample_report(
            {
                "state": "captured",
                "bytes": (
                    b"<div id='root'><article><h1>Award closed</h1>"
                    b"<p>Reference 42</p></article></div>"
                ),
                "format": "text/html",
                "missing": [],
            }
        )

        self.assertEqual(report["state"], "passed")
        self.assertLess(report["text_characters"], 100)

    def test_us0205_browser_verification_interstitial_cannot_activate(self):
        report = sample_report(
            {
                "state": "captured",
                "bytes": (
                    b"<!doctype html><html><head><title>Just a moment...</title>"
                    b"</head><body><main>Enable JavaScript and cookies to continue"
                    b"</main><script src='/challenge.js'></script></body></html>"
                ),
                "format": "text/html",
                "missing": [],
            }
        )

        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["completeness"], "access interstitial")
        self.assertIn(
            "browser-verification interstitial", report["missing"][0]["reason"]
        )

    def test_us0206_archive_stops_schedules_and_selected_work_then_restores_draft(self):
        Schedule.objects.create(
            workspace=self.workspace, source=self.source, enabled=True
        )
        queued = self.job()
        running = self.job(state="running")
        archive_source(self.admin, self.source, "stop")
        self.assertFalse(Schedule.objects.filter(enabled=True).exists())
        queued.refresh_from_db()
        running.refresh_from_db()
        self.assertEqual(queued.state, "cancelled")
        self.assertEqual(running.state, "cancel_requested")
        self.assertTrue(ContentRecord.objects.filter(pk=self.visible.pk).exists())
        self.login()
        response = self.client.post(
            reverse(
                "source-action", args=[self.workspace.pk, self.source.pk, "restore"]
            )
        )
        self.assertEqual(response.status_code, 302)
        self.source.refresh_from_db()
        self.assertEqual(self.source.state, "draft")

    def test_us0301_public_has_no_secret_api_requires_scoped_reference_unsupported_stays_draft(
        self,
    ):
        with patch("socket.getaddrinfo", return_value=PUBLIC_DNS):
            v = update_source_config(self.admin, self.source, {"access": "public"})
            self.assertIsNone(v.config["connection_id"])
            with self.assertRaises(ValidationError):
                update_source_config(
                    self.admin,
                    self.source,
                    {"access": "api", "connection_id": str(self.a.pk)},
                )
            conn = Connection.objects.create(
                bound_source=self.source,
                workspace=self.workspace,
                owner=self.admin,
                name="API",
                domain="example.com",
                method="api",
                secret_ref="TIJ_SECRET_EXAMPLE",
            )
            v = update_source_config(
                self.admin,
                self.source,
                {"access": "api", "connection_id": str(conn.pk)},
            )
            self.assertEqual(v.config["connection_id"], str(conn.pk))
            self.assertNotIn("secret", v.config)
        result = test_source(self.admin, self.source)
        self.assertEqual(result.state, "needs_access")
        self.source.refresh_from_db()
        self.assertEqual(self.source.state, "draft")

    def test_us0103_system_record_api_excludes_title_and_evidence_without_source_grant(
        self,
    ):
        self.login(self.reviewer)
        url = reverse("system-records", args=[self.workspace.pk, self.a.pk])
        self.assertEqual(self.client.get(url).json()["records"], [])
        SystemSourceGrant.objects.create(system=self.a, source=self.source)
        response = self.client.get(url)
        self.assertEqual(len(response.json()["records"]), 1)
        self.assertNotIn("TOP SECRET", response.content.decode())
        self.assertNotIn("secret summary", response.content.decode())

    def test_us0206_finish_policy_allows_only_existing_running_work(self):
        running = self.job(state="running")
        queued = self.job()
        archive_source(self.admin, self.source, "finish")
        self.assertTrue(check_work_permission(running))
        self.assertFalse(check_work_permission(queued))

    @patch("socket.getaddrinfo", return_value=PUBLIC_DNS)
    def test_collector_records_out_of_scope_redirect_without_following(self, dns):
        from unittest.mock import MagicMock

        from intelligence.collector import fetch_public

        response = MagicMock()
        response.status = 302
        response.getheader.return_value = "https://outside.example.org/secret"
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch(
            "intelligence.collector.http.client.HTTPSConnection",
            return_value=connection,
        ):
            result = fetch_public(
                "https://example.com/", "https://example.com/", self.version.config
            )
        self.assertEqual(result["state"], "partial")
        self.assertIn("Outside the approved origin", result["missing"][0]["reason"])
        self.assertEqual(connection.request.call_count, 1)

    def test_collector_rejects_private_resolution_after_initial_public_dns(self):
        from intelligence.collector import fetch_public

        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))]
        with (
            patch("socket.getaddrinfo", side_effect=[PUBLIC_DNS, PUBLIC_DNS, private]),
            patch("intelligence.collector.http.client.HTTPSConnection") as connection,
            self.assertRaises(ValidationError),
        ):
            fetch_public(
                "https://example.com/", "https://example.com/", self.version.config
            )
        connection.assert_not_called()
