import os
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from intelligence.permissions import member
from workspaces.models import (
    Membership,
    PortalIdentity,
    PortalPermissionReceipt,
    Workspace,
)
from workspaces.portal_permissions import dispatch


@override_settings(APLYD_SOURCE_WRITER_ENABLED=True)
class PortalPermissionTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="Synthetic workspace")
        self.actor = get_user_model().objects.create_user("reviewer")
        self.target = get_user_model().objects.create_user("target")
        Membership.objects.create(
            user=self.actor, workspace=self.workspace, role="administrator"
        )
        self.membership = Membership.objects.create(
            user=self.target, workspace=self.workspace, role="viewer"
        )
        for user, subject, reviewer in [
            (self.actor, "actor", True),
            (self.target, "target", False),
        ]:
            PortalIdentity.objects.create(
                user=user,
                subject=subject,
                issuer="https://identity.invalid",
                workspace=self.workspace,
                reviewer=reviewer,
                review_reference="explicit synthetic fixture",
            )
        p = patch.dict(
            os.environ,
            {
                "APLYD_SOURCE_ISSUER": "https://identity.invalid",
                "APLYD_SOURCE_WORKSPACE": str(self.workspace.pk),
                "APLYD_SOURCE_ORGANISATION": "synthetic-org",
            },
        )
        p.start()
        self.addCleanup(p.stop)
        self.args = {"actor_subject": "actor", "target_subject": "target"}

    def preview(self):
        return dispatch({**self.args, "operation": "preview"})

    def apply(self, choice, expected=None, request_id=None):
        return dispatch(
            {
                **self.args,
                "operation": "apply",
                "choice": choice,
                "expected": expected or self.preview(),
                "request_id": str(request_id or uuid.uuid4()),
            }
        )

    def test_grant_downgrade_revoke_regrant_retains_membership(self):
        pk = self.membership.pk
        for choice, role, active, generation in [
            ("operator", "operator", True, 0),
            ("reviewer", "reviewer", True, 0),
            ("revoke", "reviewer", False, 1),
            ("viewer", "viewer", True, 2),
        ]:
            result = self.apply(choice)
            self.assertEqual(result["after"]["role"], role)
            self.assertIs(result["after"]["active"], active)
            self.assertEqual(result["after"]["generation"], generation)
            self.assertEqual(Membership.objects.get(user=self.target).pk, pk)
        self.assertEqual(PortalPermissionReceipt.objects.count(), 4)

    def test_stale_plan_including_aba_cannot_write(self):
        before = self.preview()
        Membership.objects.filter(pk=self.membership.pk).update(role="operator")
        Membership.objects.filter(pk=self.membership.pk).update(role="viewer")
        with self.assertRaises(ValidationError):
            self.apply("administrator", before)

    @override_settings(APLYD_SOURCE_WRITER_ENABLED=False)
    def test_default_off(self):
        with self.assertRaises(PermissionDenied):
            self.preview()

    def test_disabled_target_cannot_gain_role(self):
        self.target.is_active = False
        self.target.save()
        with self.assertRaises(PermissionDenied):
            self.apply("operator")
        self.apply("revoke")

    def test_reviewer_requires_current_native_admin_and_reviewed_identity(self):
        PortalIdentity.objects.filter(user=self.actor).update(reviewer=False)
        with self.assertRaises(PermissionDenied):
            self.preview()

    def test_unknown_role_rejected(self):
        with self.assertRaises(ValidationError):
            self.apply("superuser")

    def test_native_write_denied_immediately_and_session_not_revived(self):
        self.client.force_login(
            self.target, backend="django.contrib.auth.backends.ModelBackend"
        )
        url = f"/workspaces/{self.workspace.pk}/permission-status/"
        self.assertEqual(self.client.get(url).status_code, 200)
        self.apply("operator")
        member(self.target, self.workspace.pk, write=True)
        self.apply("viewer")
        with self.assertRaises(PermissionDenied):
            member(self.target, self.workspace.pk, write=True)
        self.apply("revoke")
        self.assertEqual(self.client.get(url).status_code, 404)
        self.apply("viewer")
        self.assertEqual(self.client.get(url).status_code, 401)
        self.client.logout()
        self.client.force_login(
            self.target, backend="django.contrib.auth.backends.ModelBackend"
        )
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_receipt_failure_rolls_back_role(self):
        with (
            patch.object(
                PortalPermissionReceipt.objects,
                "create",
                side_effect=RuntimeError("fixture"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.apply("administrator")
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role, "viewer")

    def test_receipt_is_single_use(self):
        nonce = uuid.uuid4()
        self.apply("operator", request_id=nonce)
        with self.assertRaises(ValidationError):
            self.apply("operator", request_id=nonce)

    def test_preserves_source_system_and_export_grants(self):
        from intelligence.models import ConsumerSystem, Source, SourceGrant, SystemGrant

        source = Source.objects.create(
            workspace=self.workspace,
            owner=self.actor,
            name="Fixture source",
            url="https://example.invalid/",
        )
        system = ConsumerSystem.objects.create(
            workspace=self.workspace, owner=self.actor, name="Fixture system"
        )
        source_grant = SourceGrant.objects.create(
            membership=self.membership, source=source, can_export=True
        )
        system_grant = SystemGrant.objects.create(
            membership=self.membership, system=system
        )
        before = self.preview()
        self.apply("revoke")
        source_grant.refresh_from_db()
        system_grant.refresh_from_db()
        self.assertTrue(source_grant.can_export)
        self.assertEqual(self.preview()["scopes_sha256"], before["scopes_sha256"])
        self.apply("operator")
        self.assertEqual(SourceGrant.objects.count(), 1)
        self.assertEqual(SystemGrant.objects.count(), 1)

    def test_cross_workspace_and_self_change_denied(self):
        self.args["target_subject"] = "actor"
        with self.assertRaises(PermissionDenied):
            self.preview()
        self.args["target_subject"] = "unreviewed"
        with self.assertRaises(PortalIdentity.DoesNotExist):
            self.preview()

    def test_reviewer_downgrade_between_review_and_save_denied(self):
        before = self.preview()
        Membership.objects.filter(user=self.actor).update(role="viewer")
        with self.assertRaises(Membership.DoesNotExist):
            self.apply("operator", before)

    def test_suspension_blocks_only_target_work_without_deleting_checkpoints(self):
        from intelligence.models import Source, SourceVersion, WorkItem

        source = Source.objects.create(
            workspace=self.workspace,
            owner=self.actor,
            name="Synthetic source",
            url="https://example.invalid/jobs",
        )
        version = SourceVersion.objects.create(source=source, version=1)
        mine = WorkItem.objects.create(
            workspace=self.workspace,
            actor=self.target,
            source=source,
            source_version=version,
            state="running",
            checkpoint={"offset": 7},
        )
        other = WorkItem.objects.create(
            workspace=self.workspace,
            actor=self.actor,
            source=source,
            source_version=version,
            state="queued",
            checkpoint={"offset": 12},
        )
        self.apply("revoke")
        mine.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(mine.state, "blocked")
        self.assertEqual(mine.checkpoint, {"offset": 7})
        self.assertEqual(other.state, "queued")
        self.assertEqual(other.checkpoint, {"offset": 12})
        self.apply("viewer")
        mine.refresh_from_db()
        self.assertEqual(mine.state, "blocked")
