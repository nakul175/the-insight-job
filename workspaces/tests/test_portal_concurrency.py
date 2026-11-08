"""Real PostgreSQL contenders must not both commit the same reviewed state."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
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
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(attempt, "operator")
            second = pool.submit(attempt, "reviewer")
            results = [first.result(timeout=10), second.result(timeout=10)]
        self.assertCountEqual(results, ["applied", "stale"])
        self.assertEqual(PortalPermissionReceipt.objects.count(), 1)
