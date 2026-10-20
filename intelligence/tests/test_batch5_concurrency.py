"""Competing real database claims must select exactly one reviewer."""

import threading

from django.core.exceptions import ValidationError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature

from intelligence.routing import claim_review, route_one

from . import test_batch1 as fixtures
from . import test_batch5 as workflow_fixtures


@skipUnlessDBFeature("has_select_for_update")
class ReviewClaimTests(TransactionTestCase):
    pipeline = workflow_fixtures.BatchFiveTests.pipeline

    def setUp(self):
        fixtures.BatchOneTests.setUpTestData.__func__(self.__class__)

    def test_two_reviewers_one_claim_with_current_assignment(self):
        record, _revision, system = self.pipeline()
        result, _created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        ready = threading.Barrier(2, timeout=5)
        winners, conflicts, failures = [], [], []

        def claim(actor):
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '3s'")
                    ready.wait()
                    claimed = claim_review(actor, self.workspace.pk, result.pk, 1)
                    winners.append(claimed.assigned_to_id)
            except ValidationError as exc:
                conflicts.append(str(exc))
            except (DatabaseError, threading.BrokenBarrierError) as exc:
                failures.append(str(exc))
            finally:
                connection.close()

        threads = [
            threading.Thread(target=claim, args=(actor,))
            for actor in [self.admin, self.reviewer]
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(conflicts), 1)
        result.refresh_from_db()
        self.assertEqual(result.assigned_to_id, winners[0])
        self.assertEqual(result.version, 2)
