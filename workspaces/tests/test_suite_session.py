import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspaces import suite_oidc, suite_session
from workspaces.models import Membership, PortalIdentity, Workspace


@override_settings(
    APLYD_SOURCE_OIDC_ENABLED=True,
    APLYD_ACCOUNTS_ISSUER="https://accounts.invalid",
    APLYD_SOURCE_ORIGIN="https://source.invalid",
    APLYD_SOURCE_CLIENT_ID="sourcealmighty",
    APLYD_SOURCE_CLIENT_SECRET="synthetic-only",
    ALLOWED_HOSTS=["testserver"],
)
class SuiteSessionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "synthetic", email="known@example.invalid"
        )
        self.workspace = Workspace.objects.create(name="Synthetic")
        Membership.objects.create(user=self.user, workspace=self.workspace)
        self.identity = PortalIdentity.objects.create(
            user=self.user,
            issuer="https://accounts.invalid",
            subject="known",
            workspace=self.workspace,
            review_reference="Reviewed",
        )
        self.profile = {
            "issuer": "https://accounts.invalid",
            "sub": "known",
            "uid": "current",
            "id_token": "synthetic-token",
        }

    def pending(self, **changes):
        session = self.client.session
        session["source_oidc_pending"] = {
            "created": time.time(),
            "state": "expected",
            "nonce": "nonce",
            "verifier": "verifier",
            **changes,
        }
        session.save()

    @patch("workspaces.suite_oidc.current_session", return_value=True)
    @patch("workspaces.suite_oidc.exchange_code")
    def test_exact_callback_first_login_and_same_cookie_revocation(
        self, exchange, status
    ):
        exchange.return_value = self.profile
        self.pending()
        response = self.client.get("/auth/callback/?code=code&state=expected")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(status.call_args.kwargs["local_id"], str(self.user.pk))
        session_id = self.client.cookies["sessionid"].value
        self.assertEqual(self.client.get("/workspaces/").status_code, 200)
        self.assertEqual(self.client.cookies["sessionid"].value, session_id)
        status.return_value = False
        self.assertEqual(self.client.get("/workspaces/").status_code, 401)
        self.assertNotIn("_auth_user_id", self.client.session)

    @patch("workspaces.suite_oidc.exchange_code")
    def test_expired_missing_duplicate_callback_never_exchanges(self, exchange):
        self.pending(created=time.time() - 301)
        self.assertEqual(
            self.client.get("/auth/callback/?code=c&state=s").status_code, 401
        )
        self.pending()
        self.assertEqual(
            self.client.get("/auth/callback/?code=c&code=d&state=s").status_code, 401
        )
        self.assertEqual(
            self.client.get("/auth/callback/?code=c&state=s").status_code, 401
        )
        exchange.assert_not_called()

    @patch("workspaces.suite_oidc.current_session", return_value=True)
    @patch("workspaces.suite_oidc.exchange_code")
    def test_unknown_subject_same_email_does_not_create_or_link(self, exchange, status):
        exchange.return_value = self.profile | {
            "sub": "unknown",
            "email": self.user.email,
        }
        self.pending()
        self.assertEqual(
            self.client.get("/auth/callback/?code=c&state=expected").status_code, 401
        )
        self.assertEqual(get_user_model().objects.count(), 1)
        status.assert_not_called()

    def test_disabled_or_privileged_or_ambiguous_mapping_denied(self):
        for field in ("is_active", "is_staff", "is_superuser"):
            setattr(self.user, field, field != "is_active")
            self.user.save()
            with self.assertRaises(suite_oidc.OIDCError):
                suite_session.mapped_user("known", str(self.user.pk))
            setattr(self.user, field, field == "is_active")
            self.user.save()
        other = get_user_model().objects.create_user("other")
        PortalIdentity.objects.create(
            user=other,
            issuer="https://accounts.invalid",
            subject="known",
            workspace=Workspace.objects.create(name="Other"),
            review_reference="Another",
        )
        with self.assertRaises(suite_oidc.OIDCError):
            suite_session.mapped_user("known", str(self.user.pk))

    def test_no_local_password_fallback_when_opted_in(self):
        self.assertEqual(
            self.client.post(
                "/sign-in/", {"username": "synthetic", "password": "unused"}
            ).status_code,
            405,
        )
        self.client.force_login(
            self.user, backend="django.contrib.auth.backends.ModelBackend"
        )
        self.assertEqual(self.client.get("/workspaces/").status_code, 401)

    @override_settings(APLYD_SOURCE_OIDC_ENABLED=False)
    def test_default_off_preserves_legacy_login(self):
        self.assertEqual(self.client.get("/sign-in/").status_code, 200)
        self.assertEqual(self.client.get("/auth/login/").status_code, 404)

    @override_settings(
        APLYD_SOURCE_IDENTITY_SOURCE="portal",
        APLYD_SOURCE_BINDINGS_FILE="/unused-reviewed-file",
    )
    def test_portal_authority_never_falls_back_to_legacy_file(self):
        with patch("workspaces.suite_identity.file_mapped_user") as legacy:
            self.assertEqual(
                suite_session.mapped_user("known", str(self.user.pk)), self.user
            )
            self.identity.delete()
            with self.assertRaises(suite_oidc.OIDCError):
                suite_session.mapped_user("known", str(self.user.pk))
            legacy.assert_not_called()

    @override_settings(
        APLYD_SOURCE_IDENTITY_SOURCE="file",
        APLYD_SOURCE_BINDINGS_FILE="/missing-reviewed-file",
    )
    def test_file_authority_never_falls_back_to_database_mapping(self):
        with self.assertRaises(suite_oidc.OIDCError):
            suite_session.mapped_user("known", str(self.user.pk))

    @override_settings(APLYD_SOURCE_IDENTITY_SOURCE="unreviewed")
    def test_unknown_identity_authority_denied(self):
        with self.assertRaises(suite_oidc.OIDCError):
            suite_session.mapped_user("known", str(self.user.pk))
