import re
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from workspaces.models import Membership, Workspace, WorkspaceSelection


class WorkspaceAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "analyst", password="test-password"
        )
        cls.other = get_user_model().objects.create_user(
            "other", password="test-password"
        )
        cls.own = Workspace.objects.create(name="Research desk")
        cls.foreign = Workspace.objects.create(name="CONFIDENTIAL unrelated workspace")
        Membership.objects.create(user=cls.user, workspace=cls.own)
        Membership.objects.create(user=cls.other, workspace=cls.foreign)

    def test_ac_0101_01_sign_in_lists_only_assigned_workspaces(self):
        response = self.client.post(
            reverse("login"),
            {"username": "analyst", "password": "test-password"},
            follow=True,
        )
        self.assertContains(response, "Research desk")
        self.assertNotContains(response, self.foreign.name)
        self.assertNotContains(response, str(self.foreign.id))
        self.assertEqual(list(response.context["workspaces"]), [self.own])

    def test_ac_0101_01_selection_enters_workspace_and_audits_actor(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("select-workspace", args=[self.own.id]))
        self.assertRedirects(response, reverse("workspace", args=[self.own.id]))
        self.assertEqual(self.client.session["active_workspace"], str(self.own.id))
        self.assertEqual(WorkspaceSelection.objects.get().user, self.user)

    def test_ac_0101_02_expired_session_does_not_execute_or_replay_post(self):
        self.client.force_login(self.user)
        Session.objects.filter(session_key=self.client.session.session_key).update(
            expire_date=timezone.now() - timedelta(seconds=1)
        )
        url = reverse("select-workspace", args=[self.own.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/sign-in/"))
        self.assertFalse(WorkspaceSelection.objects.exists())
        response = self.client.post(
            reverse("login"),
            {"username": "analyst", "password": "test-password", "next": url},
        )
        self.assertRedirects(response, "/workspaces/")
        self.assertNotIn("active_workspace", self.client.session)
        self.assertFalse(WorkspaceSelection.objects.exists())

    def test_ac_0101_02_expired_protected_get_requires_sign_in(self):
        self.client.force_login(self.user)
        Session.objects.filter(session_key=self.client.session.session_key).update(
            expire_date=timezone.now() - timedelta(seconds=1)
        )
        response = self.client.get(reverse("workspace", args=[self.own.id]))
        self.assertEqual(response.status_code, 302)
        self.assertNotContains(response, self.own.name, status_code=302)

    def test_ac_0101_03_foreign_and_missing_workspace_are_indistinguishable(self):
        self.client.force_login(self.user)
        for route, method in [
            ("workspace", self.client.get),
            ("select-workspace", self.client.post),
        ]:
            foreign = method(reverse(route, args=[self.foreign.id]))
            missing = method(reverse(route, args=[uuid.uuid4()]))
            self.assertEqual(foreign.status_code, 404)

            # Django deliberately masks the CSRF token differently per response.
            def normalize(body):
                return re.sub(
                    rb'name="csrfmiddlewaretoken" value="[^"]+"',
                    b'name="csrfmiddlewaretoken" value="MASKED"',
                    body,
                )

            self.assertEqual(normalize(foreign.content), normalize(missing.content))
            self.assertNotIn(self.foreign.name.encode(), foreign.content)
        self.assertFalse(WorkspaceSelection.objects.exists())
        self.assertNotIn("active_workspace", self.client.session)

    def test_membership_revocation_takes_effect_on_next_request(self):
        self.client.force_login(self.user)
        self.client.post(reverse("select-workspace", args=[self.own.id]))
        Membership.objects.filter(user=self.user).delete()
        self.assertEqual(
            self.client.get(reverse("workspace", args=[self.own.id])).status_code, 404
        )
        self.client.get(reverse("workspaces"))
        self.assertNotIn("active_workspace", self.client.session)

    def test_staff_has_no_implicit_workspace_access(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(reverse("workspace", args=[self.foreign.id])).status_code,
            404,
        )

    def test_empty_memberships_show_recovery_guidance(self):
        Membership.objects.filter(user=self.user).delete()
        self.client.force_login(self.user)
        self.assertContains(
            self.client.get(reverse("workspaces")), "No workspace access yet"
        )

    def test_invalid_credentials_are_generic_and_do_not_sign_in(self):
        for username in ["analyst", "unknown"]:
            response = self.client.post(
                reverse("login"), {"username": username, "password": "wrong"}
            )
            self.assertContains(
                response, "Please enter a correct username and password"
            )
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_user_cannot_sign_in(self):
        self.user.is_active = False
        self.user.save()
        self.client.post(
            reverse("login"), {"username": "analyst", "password": "test-password"}
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_open_redirect_is_ignored(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": "analyst",
                "password": "test-password",
                "next": "https://example.org",
            },
        )
        self.assertRedirects(response, "/workspaces/")

    def test_selection_is_post_only_and_csrf_protected(self):
        self.client.force_login(self.user)
        url = reverse("select-workspace", args=[self.own.id])
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(url).status_code, 403)
        self.assertFalse(WorkspaceSelection.objects.exists())

    def test_logout_invalidates_session_and_protected_pages_are_not_cached(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("workspaces"))
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.client.post(reverse("logout"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get(reverse("workspaces")).status_code, 302)

    def test_readiness_reports_database_failure_without_leaking_details(self):
        from unittest.mock import patch

        from django.db import DatabaseError

        self.assertEqual(
            self.client.get(reverse("readiness")).json(), {"status": "ready"}
        )
        with patch(
            "workspaces.views.Membership.objects.exists",
            side_effect=DatabaseError("private connection detail"),
        ):
            response = self.client.get(reverse("readiness"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
