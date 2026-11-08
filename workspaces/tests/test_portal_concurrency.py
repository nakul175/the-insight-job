"""Real PostgreSQL contenders must not both commit the same reviewed state."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings

from workspaces.models import PortalPermissionReceipt

from . import test_portal_permissions as fixture


@override_settings(APLYD_SOURCE_WRITER_ENABLED=True)
class PortalPermissionConcurrencyTests(TransactionTestCase):
    preview = fixture.PortalPermissionTests.preview
    apply = fixture.PortalPermissionTests.apply

    def test_one_of_two_stale_contenders_commits(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row-lock integration profile.")
        fixture.PortalPermissionTests.setUp(self)
        before = self.preview()
        barrier = Barrier(2)

        def attempt(role):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                self.apply(role, expected=before)
                return "applied"
            except ValidationError:
                return "stale"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(attempt, "operator")
            second = pool.submit(attempt, "reviewer")
            results = [first.result(timeout=10), second.result(timeout=10)]
        self.assertCountEqual(results, ["applied", "stale"])
        self.assertEqual(PortalPermissionReceipt.objects.count(), 1)


@override_settings(APLYD_SOURCE_WRITER_ENABLED=True, APLYD_SOURCE_SCOPES_ENABLED=True)
class PortalScopeConcurrencyTests(TransactionTestCase):
    def test_scope_contenders_and_membership_contender_share_workspace_fence(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row-lock integration profile.")
        from .test_portal_scopes import PortalScopeTests

        PortalScopeTests.setUp(self)
        self.preview = lambda: PortalScopeTests.preview(self)
        before = self.preview()
        barrier = Barrier(2)

        def attempt(export):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                PortalScopeTests.apply(
                    self,
                    {"sources": {str(self.source.pk): export}, "systems": []},
                    expected=before,
                )
                return "applied"
            except ValidationError:
                return "stale"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(attempt, True)
            second = pool.submit(attempt, False)
            results = [first.result(timeout=10), second.result(timeout=10)]
        self.assertCountEqual(results, ["applied", "stale"])
        self.assertEqual(PortalPermissionReceipt.objects.count(), 1)
        role_before = fixture.PortalPermissionTests.preview(self)
        current = self.preview()
        PortalScopeTests.apply(self, {"sources": {}, "systems": []}, expected=current)
        with self.assertRaises(ValidationError):
            fixture.PortalPermissionTests.apply(self, "operator", expected=role_before)


@override_settings(
    APLYD_SOURCE_WRITER_ENABLED=True, APLYD_SOURCE_PROVISIONING_ENABLED=True
)
class PortalProvisionConcurrencyTests(TransactionTestCase):
    def test_only_one_creation_commits(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL creation serialization.")
        import uuid

        from workspaces.portal_provisioning import dispatch_provision

        from .test_portal_provisioning import ProvisionTests

        ProvisionTests.setUp(self)
        before = dispatch_provision(self.args | {"operation": "provision_preview"})
        barrier = Barrier(2)

        def attempt():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                dispatch_provision(
                    self.args
                    | {
                        "operation": "provision_apply",
                        "request_id": str(uuid.uuid4()),
                        "expected": before,
                    }
                )
                return "created"
            except ValidationError:
                return "denied"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(attempt)
            b = pool.submit(attempt)
            self.assertCountEqual(
                [a.result(timeout=12), b.result(timeout=12)], ["created", "denied"]
            )
        self.assertEqual(PortalPermissionReceipt.objects.count(), 1)
