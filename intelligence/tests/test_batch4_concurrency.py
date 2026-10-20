"""Exercise the real PostgreSQL enrichment/configuration lock interaction."""

import threading
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature

from intelligence.enrichment import enrichment_step, start_enrichment
from intelligence.systems import save_configuration
from workspaces.models import Workspace

from . import test_batch1 as fixtures
from .test_batch4 import BatchFourTests


@skipUnlessDBFeature("has_select_for_update")
class EnrichmentLockTests(TransactionTestCase):
    draft = BatchFourTests.draft
    config = BatchFourTests.config
    policy_fixture = BatchFourTests.policy_fixture

    def setUp(self):
        fixtures.BatchOneTests.setUpTestData.__func__(self.__class__)

    def test_configuration_and_enrichment_share_workspace_first_lock_order(self):
        system, _policy = self.policy_fixture()
        attempt = start_enrichment(
            self.admin,
            self.workspace.pk,
            system.pk,
            self.visible.pk,
            "Evidence gap",
            ["https://example.com/a"],
            ["budget"],
        )
        workspace_locked = threading.Event()
        enrichment_entered = threading.Event()
        errors = []
        from intelligence.enrichment import owned_system as real_owned_system

        def observed_owned(*args, **kwargs):
            enrichment_entered.set()
            return real_owned_system(*args, **kwargs)

        def configure():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '3s'")
                    Workspace.objects.select_for_update().get(pk=self.workspace.pk)
                    workspace_locked.set()
                    # Let the other thread attempt enrichment while Workspace is held.
                    # The old order acquired System first; the corrected order waits here.
                    enrichment_entered.wait(0.3)
                    save_configuration(
                        self.admin, self.workspace.pk, system.pk, 1, self.config()
                    )
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def enrich():
            close_old_connections()
            try:
                if not workspace_locked.wait(5):
                    raise AssertionError("Configuration failed to acquire Workspace")
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '3s'")
                    enrichment_step(self.admin, self.workspace.pk, attempt.pk)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with (
            patch("intelligence.enrichment.owned_system", side_effect=observed_owned),
            patch("socket.getaddrinfo", return_value=fixtures.PUBLIC_DNS),
        ):
            threads = [
                threading.Thread(target=configure),
                threading.Thread(target=enrich),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        system.refresh_from_db()
        self.assertEqual(system.config_version, 2)
        attempt.refresh_from_db()
        self.assertEqual(attempt.state, "running")
