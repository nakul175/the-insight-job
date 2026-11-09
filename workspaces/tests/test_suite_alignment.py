import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspaces.models import Membership, Workspace
from workspaces.suite_identity import mapped_user
from workspaces.suite_oidc import OIDCError


@override_settings(
    APLYD_SOURCE_OIDC_ENABLED=True,
    APLYD_ACCOUNTS_ISSUER="https://accounts.test.invalid",
    APLYD_SOURCE_ORIGIN="https://source.test.invalid",
    APLYD_SOURCE_CLIENT_ID="sourcealmighty",
    APLYD_SOURCE_CLIENT_SECRET="synthetic-unit-test-client-secret",
)
class SuiteAlignmentTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.file = Path(self.directory.name) / "bindings.json"
        self.settings_override = override_settings(
            APLYD_SOURCE_BINDINGS_FILE=str(self.file)
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = get_user_model().objects.create_user(
            "synthetic-viewer", password="unit-only-password"
        )
        self.other = get_user_model().objects.create_user("synthetic-other")
        self.workspace = Workspace.objects.create(name="Synthetic SSO workspace")
        self.control = Workspace.objects.create(name="Unassigned workspace")
        Membership.objects.create(
            workspace=self.workspace, user=self.user, role="viewer"
        )
        Membership.objects.create(
            workspace=self.control, user=self.other, role="viewer"
        )
        self.data = {
            "version": 1,
            "issuer": "https://accounts.test.invalid",
            "origin": "https://source.test.invalid",
            "client_id": "sourcealmighty",
            "bindings": [
                {
                    "subject": "synthetic-sub",
                    "native_user_id": str(self.user.pk),
                    "enabled": True,
                    "review_reference": "synthetic owner review",
                }
            ],
        }
        self.write()
        self.profile = {
            "sub": "synthetic-sub",
            "uid": "synthetic-session",
            "issuer": self.data["issuer"],
        }

    def write(self):
        self.file.write_text(json.dumps(self.data))

    def pending(self, **patches):
        session = self.client.session
        session["source_oidc_pending"] = dict(
            state="state",
            verifier="verifier",
            nonce="nonce",
            created=time.time(),
            **patches,
        )
        session.save()

    def sign_in(self):
        self.assertEqual(self.client.get("/auth/login/").status_code, 302)
        state = self.client.session["source_oidc_pending"]["state"]
        with (
            patch(
                "workspaces.suite_session.oidc.exchange_code", return_value=self.profile
            ),
            patch(
                "workspaces.suite_session.oidc.current_session", return_value=True
            ) as current,
        ):
            response = self.client.get(
                "/auth/callback/", {"code": "code", "state": state}
            )
            self.assertRedirects(
                response, "/workspaces/", fetch_redirect_response=False
            )
            current.assert_called_once_with(
                sub="synthetic-sub", uid="synthetic-session", local_id=str(self.user.pk)
            )
        return response

    def test_button_visible_and_native_login_preserved(self):
        response = self.client.get("/sign-in/")
        self.assertContains(response, "Continue with Aplyd")
        with patch(
            "workspaces.suite_session.oidc.current_session",
            side_effect=AssertionError("Native login must not call Accounts"),
        ):
            response = self.client.post(
                "/sign-in/",
                {"username": "synthetic-viewer", "password": "unit-only-password"},
            )
            self.assertRedirects(
                response, "/workspaces/", fetch_redirect_response=False
            )
            self.assertEqual(self.client.get("/workspaces/").status_code, 200)
            self.assertNotIn("source_suite", self.client.session)

    @override_settings(APLYD_SOURCE_OIDC_ENABLED=False)
    def test_feature_off_is_inert(self):
        self.assertNotContains(self.client.get("/sign-in/"), "Continue with Aplyd")
        self.assertEqual(self.client.get("/auth/login/").status_code, 404)
        self.assertEqual(self.client.get("/auth/callback/").status_code, 404)

    def test_start_has_pkce_nonce_and_exact_callback(self):
        response = self.client.get("/auth/login/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(self.data["issuer"] + "/auth?"))
        self.assertIn("code_challenge_method=S256", response.url)
        self.assertIn(
            "redirect_uri=https%3A%2F%2Fsource.test.invalid%2Fauth%2Fcallback%2F",
            response.url,
        )
        self.assertIn("nonce=", response.url)
        self.assertIn("state=", response.url)

    def test_existing_native_identity_and_workspace_boundary(self):
        counts = (get_user_model().objects.count(), Membership.objects.count())
        self.sign_in()
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))
        with patch("workspaces.suite_session.oidc.current_session", return_value=True):
            response = self.client.get("/workspaces/")
            self.assertContains(response, self.workspace.name)
            self.assertNotContains(response, self.control.name)
            self.assertEqual(
                self.client.get(f"/workspaces/{self.control.pk}/").status_code, 404
            )
        self.assertEqual(
            (get_user_model().objects.count(), Membership.objects.count()), counts
        )

    def test_callback_cannot_replay_or_invent_identity(self):
        self.sign_in()
        with (
            patch("workspaces.suite_session.oidc.current_session", return_value=True),
            patch("workspaces.suite_session.oidc.exchange_code") as exchange,
        ):
            self.assertEqual(
                self.client.get("/auth/callback/?code=code&state=state").status_code,
                401,
            )
            exchange.assert_not_called()
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_missing_expired_duplicate_and_wrong_issuer_callbacks_denied(self):
        cases = [
            (None, "code=x&state=y"),
            (time.time() - 301, "code=x&state=y"),
            (time.time(), "code=x&code=z&state=y"),
            (time.time(), "code=x&state=y&state=z"),
            (time.time(), "code=x&state=y&iss=https://wrong.invalid"),
        ]
        for created, query in cases:
            if created is not None:
                session = self.client.session
                session["source_oidc_pending"] = {"created": created, "state": "y"}
                session.save()
            with patch("workspaces.suite_session.oidc.exchange_code") as exchange:
                self.assertEqual(
                    self.client.get("/auth/callback/?" + query).status_code, 401
                )
                exchange.assert_not_called()
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_unreviewed_subject_and_current_proof_failure_denied(self):
        for profile, active in [
            (dict(self.profile, sub="unassigned"), True),
            (self.profile, False),
        ]:
            self.pending()
            with (
                patch(
                    "workspaces.suite_session.oidc.exchange_code", return_value=profile
                ),
                patch(
                    "workspaces.suite_session.oidc.current_session", return_value=active
                ),
            ):
                self.assertEqual(
                    self.client.get("/auth/callback/?code=x&state=state").status_code,
                    401,
                )
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_staff_or_superuser_cannot_receive_sso(self):
        for patch_values in [
            {"is_active": False},
            {"is_staff": True},
            {"is_superuser": True},
        ]:
            get_user_model().objects.filter(pk=self.user.pk).update(**patch_values)
            with self.assertRaises(OIDCError):
                mapped_user("synthetic-sub")
            get_user_model().objects.filter(pk=self.user.pk).update(
                is_active=True, is_staff=False, is_superuser=False
            )

    def test_missing_ambiguous_disabled_and_cross_realm_mappings_denied(self):
        original = json.loads(json.dumps(self.data))
        variants = [
            dict(original, bindings=[]),
            dict(original, issuer="https://wrong.invalid"),
            dict(original, origin="https://other.invalid"),
            dict(original, client_id="other"),
            dict(original, version=True),
            dict(original, bindings=original["bindings"] * 2),
        ]
        for field, value in [
            ("enabled", False),
            ("review_reference", ""),
            ("native_user_id", "unknown"),
        ]:
            variant = json.loads(json.dumps(original))
            variant["bindings"][0][field] = value
            variants.append(variant)
        for data in variants:
            self.file.write_text(json.dumps(data))
            with self.assertRaises(OIDCError):
                mapped_user("synthetic-sub")
        self.file.write_text('{"version":1,"version":1}')
        with self.assertRaises(OIDCError):
            mapped_user("synthetic-sub")

    def test_current_revocation_and_outage_clear_sso_session(self):
        for proof in [False, OIDCError("offline")]:
            self.sign_in()
            with patch(
                "workspaces.suite_session.oidc.current_session",
                **(
                    {"side_effect": proof}
                    if isinstance(proof, Exception)
                    else {"return_value": proof}
                ),
            ):
                self.assertEqual(self.client.get("/workspaces/").status_code, 401)
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_mapping_revocation_clears_sso_without_role_mutation(self):
        self.sign_in()
        self.data["bindings"][0]["enabled"] = False
        self.write()
        self.assertEqual(self.client.get("/workspaces/").status_code, 401)
        self.assertEqual(Membership.objects.get(user=self.user).role, "viewer")

    def test_invalid_callback_is_not_cross_site_logout(self):
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get("/auth/callback/?code=forged&state=forged").status_code, 401
        )
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))
