import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from workspaces.models import (
    Membership,
    PortalIdentity,
    PortalPermissionReceipt,
    Workspace,
)
from workspaces.portal_enrollment import dispatch_enrollment
from workspaces.portal_permissions import dispatch


@override_settings(
    APLYD_SOURCE_WRITER_ENABLED=True, APLYD_SOURCE_ENROLLMENT_ENABLED=True
)
class EnrollmentTests(TestCase):
    def setUp(self):
        self.origin = Workspace.objects.create(name="Original")
        self.extra = Workspace.objects.create(name="Additional")
        users = get_user_model().objects
        self.actor = users.create_user("reviewer")
        self.target = users.create_user("existing")
        for space in [self.origin, self.extra]:
            Membership.objects.create(
                user=self.actor, workspace=space, role="administrator"
            )
            PortalIdentity.objects.create(
                issuer="https://accounts.invalid",
                subject="actor",
                user=self.actor,
                workspace=space,
                reviewer=True,
                review_reference="Explicit synthetic reviewer",
            )
        Membership.objects.create(
            user=self.target, workspace=self.origin, role="operator"
        )
        PortalIdentity.objects.create(
            issuer="https://accounts.invalid",
            subject="target",
            user=self.target,
            workspace=self.origin,
            review_reference="Original reviewed identity",
        )
        env = patch.dict(
            os.environ,
            APLYD_SOURCE_ISSUER="https://accounts.invalid",
            APLYD_SOURCE_ORGANISATION="org",
            APLYD_SOURCE_WORKSPACE=str(self.extra.pk),
        )
        env.start()
        self.addCleanup(env.stop)
        self.args = {
            "actor_subject": "actor",
            "target_subject": "target",
            "target_id": str(self.target.pk),
            "origin_workspace": str(self.origin.pk),
        }

    def preview(self):
        return dispatch_enrollment(self.args | {"operation": "enrollment_preview"})

    def apply(self, plan=None):
        return dispatch_enrollment(
            self.args
            | {
                "operation": "enrollment_apply",
                "request_id": str(uuid.uuid4()),
                "expected": plan or self.preview(),
            }
        )

    def test_exact_user_viewer_enrollment_then_role_cycle_preserves_home(self):
        users = list(get_user_model().objects.order_by("pk").values())
        home = list(
            Membership.objects.filter(workspace=self.origin).order_by("pk").values()
        )
        links = list(PortalIdentity.objects.order_by("pk").values())
        plan = self.preview()
        self.assertEqual(Membership.objects.count(), 3)
        result = self.apply(plan)
        self.assertEqual(result["native_id"], str(self.target.pk))
        self.assertEqual(
            Membership.objects.get(user=self.target, workspace=self.extra).role,
            "viewer",
        )
        for choice in ["operator", "viewer", "revoke"]:
            args = {"actor_subject": "actor", "target_subject": "target"}
            before = dispatch(args | {"operation": "preview"})
            after = dispatch(
                args
                | {
                    "operation": "apply",
                    "request_id": str(uuid.uuid4()),
                    "choice": choice,
                    "expected": before,
                }
            )
            self.assertEqual(after["after"]["active"], choice != "revoke")
        self.assertEqual(list(get_user_model().objects.order_by("pk").values()), users)
        self.assertEqual(
            list(
                Membership.objects.filter(workspace=self.origin).order_by("pk").values()
            ),
            home,
        )
        self.assertEqual(
            list(
                PortalIdentity.objects.filter(pk__in=[r["id"] for r in links])
                .order_by("pk")
                .values()
            ),
            links,
        )
        self.assertFalse(
            PortalIdentity.objects.get(user=self.target, workspace=self.extra).reviewer
        )

    def test_stale_reviewer_origin_and_disabled_target_fail_closed(self):
        plan = self.preview()
        Membership.objects.filter(user=self.target, workspace=self.origin).update(
            role="viewer"
        )
        with self.assertRaises(ValidationError):
            self.apply(plan)
        Membership.objects.filter(user=self.actor, workspace=self.extra).update(
            role="viewer"
        )
        with self.assertRaises(Membership.DoesNotExist):
            self.preview()
        Membership.objects.filter(user=self.actor, workspace=self.extra).update(
            role="administrator"
        )
        self.target.is_active = False
        self.target.save()
        with self.assertRaises(PermissionDenied):
            self.preview()

    def test_existing_destination_or_conflicting_subject_not_adopted(self):
        self.apply()
        with self.assertRaises(PermissionDenied):
            self.preview()
        self.assertEqual(get_user_model().objects.count(), 2)

    def test_no_email_link_and_exact_native_id_required(self):
        self.args["target_id"] = str(self.actor.pk)
        with self.assertRaises(PortalIdentity.DoesNotExist):
            self.preview()
        self.assertEqual(Membership.objects.count(), 3)

    def test_failed_audit_or_receipt_rolls_back_both_rows(self):
        for target in [
            "workspaces.portal_enrollment.audit",
            "workspaces.portal_enrollment.PortalPermissionReceipt.objects.create",
        ]:
            plan = self.preview()
            with (
                patch(target, side_effect=IntegrityError),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                self.apply(plan)
            self.assertFalse(
                Membership.objects.filter(
                    user=self.target, workspace=self.extra
                ).exists()
            )
            self.assertFalse(
                PortalIdentity.objects.filter(
                    user=self.target, workspace=self.extra
                ).exists()
            )

    @override_settings(APLYD_SOURCE_ENROLLMENT_ENABLED=False)
    def test_default_off(self):
        with self.assertRaises(PermissionDenied):
            self.preview()

    def test_same_workspace_and_self_denied(self):
        self.args["origin_workspace"] = str(self.extra.pk)
        with self.assertRaises(PermissionDenied):
            self.preview()
        self.args["origin_workspace"] = str(self.origin.pk)
        self.args["target_subject"] = "actor"
        with self.assertRaises(PermissionDenied):
            self.preview()

    def test_readonly_preview_preserves_all_rows(self):
        def rows():
            return {
                m.__name__: list(m.objects.order_by("pk").values())
                for m in [
                    Workspace,
                    Membership,
                    PortalIdentity,
                    PortalPermissionReceipt,
                ]
            }

        before = rows()
        self.preview()
        self.assertEqual(rows(), before)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row-lock acceptance")
@override_settings(
    APLYD_SOURCE_WRITER_ENABLED=True, APLYD_SOURCE_ENROLLMENT_ENABLED=True
)
class EnrollmentConcurrencyTests(TransactionTestCase):
    setUp = EnrollmentTests.setUp
    preview = EnrollmentTests.preview

    def test_two_reviewed_enrollments_have_one_atomic_winner(self):
        plan = self.preview()
        barrier = Barrier(2)

        def attempt():
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                barrier.wait(timeout=5)
                dispatch_enrollment(
                    self.args
                    | {
                        "operation": "enrollment_apply",
                        "request_id": str(uuid.uuid4()),
                        "expected": plan,
                    }
                )
                return "applied"
            except PermissionDenied:
                return "denied"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(results), ["applied", "denied"])
        self.assertEqual(
            Membership.objects.filter(user=self.target, workspace=self.extra).count(), 1
        )
        self.assertEqual(
            PortalIdentity.objects.filter(
                user=self.target, workspace=self.extra
            ).count(),
            1,
        )
        self.assertEqual(PortalPermissionReceipt.objects.count(), 1)
        self.assertEqual(get_user_model().objects.count(), 2)
