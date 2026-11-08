import os
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from workspaces.models import (
    Membership,
    PortalIdentity,
    PortalPermissionReceipt,
    Workspace,
)
from workspaces.portal_permissions import dispatch
from workspaces.portal_provisioning import dispatch_provision


@override_settings(
    APLYD_SOURCE_WRITER_ENABLED=True, APLYD_SOURCE_PROVISIONING_ENABLED=True
)
class ProvisionTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="Synthetic")
        self.actor = get_user_model().objects.create_user("reviewer")
        Membership.objects.create(
            user=self.actor, workspace=self.workspace, role="administrator"
        )
        PortalIdentity.objects.create(
            issuer="https://accounts.invalid",
            subject="actor",
            user=self.actor,
            workspace=self.workspace,
            reviewer=True,
            review_reference="Reviewed synthetic owner",
        )
        env = patch.dict(
            os.environ,
            APLYD_SOURCE_ISSUER="https://accounts.invalid",
            APLYD_SOURCE_ORGANISATION="synthetic-org",
            APLYD_SOURCE_WORKSPACE=str(self.workspace.pk),
        )
        env.start()
        self.addCleanup(env.stop)
        self.args = {
            "actor_subject": "actor",
            "target_subject": "new-subject",
            "name": "New synthetic person",
            "email": "new@example.invalid",
        }

    def preview(self):
        return dispatch_provision(self.args | {"operation": "provision_preview"})

    def apply(self, expected=None):
        return dispatch_provision(
            self.args
            | {
                "operation": "provision_apply",
                "request_id": str(uuid.uuid4()),
                "expected": expected or self.preview(),
            }
        )

    def test_create_first_viewer_then_native_grant_downgrade_revoke(self):
        before = list(get_user_model().objects.values())
        result = self.apply()
        user = get_user_model().objects.get(pk=result["native_id"])
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(Membership.objects.get(user=user).role, "viewer")
        from intelligence.models import SourceGrant, SystemGrant

        self.assertFalse(SourceGrant.objects.filter(membership__user=user).exists())
        self.assertFalse(SystemGrant.objects.filter(membership__user=user).exists())
        for role in ("operator", "viewer", "revoke"):
            args = {"actor_subject": "actor", "target_subject": "new-subject"}
            plan = dispatch(args | {"operation": "preview"})
            changed = dispatch(
                args
                | {
                    "operation": "apply",
                    "request_id": str(uuid.uuid4()),
                    "choice": role,
                    "expected": plan,
                }
            )
            self.assertEqual(changed["after"]["active"], role != "revoke")
        self.assertEqual(
            before, list(get_user_model().objects.filter(pk=self.actor.pk).values())
        )

    def test_disabled_stale_review_and_self_denied(self):
        with (
            override_settings(APLYD_SOURCE_PROVISIONING_ENABLED=False),
            self.assertRaises(PermissionDenied),
        ):
            self.preview()
        old = self.preview()
        Membership.objects.create(
            user=get_user_model().objects.create_user("other"), workspace=self.workspace
        )
        with self.assertRaises(ValidationError):
            self.apply(old)
        self.args["target_subject"] = "actor"
        with self.assertRaises(PermissionDenied):
            self.preview()

    def test_existing_email_or_subject_is_not_adopted(self):
        user = get_user_model().objects.create_user(
            "existing", email="NEW@example.invalid"
        )
        with self.assertRaises(ValidationError):
            self.preview()
        user.email = ""
        user.save()
        PortalIdentity.objects.create(
            issuer="https://accounts.invalid",
            subject="new-subject",
            user=user,
            workspace=Workspace.objects.create(name="Elsewhere"),
            review_reference="Other mapping",
        )
        with self.assertRaises(ValidationError):
            self.preview()
        self.assertEqual(get_user_model().objects.count(), 2)

    def test_atomic_failure_and_reviewer_downgrade(self):
        plan = self.preview()
        with (
            patch.object(
                PortalPermissionReceipt.objects, "create", side_effect=IntegrityError
            ),
            self.assertRaises(IntegrityError),
            transaction.atomic(),
        ):
            self.apply(plan)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(PortalIdentity.objects.count(), 1)
        Membership.objects.filter(user=self.actor).update(role="viewer")
        with self.assertRaises(Membership.DoesNotExist):
            self.apply(plan)

    def test_no_repeat_or_extra_role_choice(self):
        self.apply()
        with self.assertRaises(ValidationError):
            self.apply()
        self.assertEqual(get_user_model().objects.count(), 2)
