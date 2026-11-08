import uuid
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from intelligence.models import (
    ConsumerSystem,
    Source,
    SourceGrant,
    SourceVersion,
    WorkItem,
)
from intelligence.permissions import sources_for, systems_for
from workspaces.models import Membership, PortalPermissionReceipt, Workspace
from workspaces.portal_scopes import dispatch_scopes
from workspaces.tests.test_portal_permissions import PortalPermissionTests


@override_settings(APLYD_SOURCE_WRITER_ENABLED=True, APLYD_SOURCE_SCOPES_ENABLED=True)
class PortalScopeTests(TestCase):
    def setUp(self):
        PortalPermissionTests.setUp(self)
        self.source = Source.objects.create(
            workspace=self.workspace,
            owner=self.actor,
            name="Source",
            url="https://example.invalid/",
        )
        self.system = ConsumerSystem.objects.create(
            workspace=self.workspace, owner=self.actor, name="System"
        )
        self.grants = {
            "sources": {str(self.source.pk): True},
            "systems": [str(self.system.pk)],
        }

    def preview(self):
        return dispatch_scopes({**self.args, "operation": "scopes_preview"})

    def apply(self, grants, expected=None, nonce=None):
        return dispatch_scopes(
            {
                **self.args,
                "operation": "scopes_apply",
                "grants": grants,
                "expected": expected or self.preview(),
                "request_id": str(nonce or uuid.uuid4()),
            }
        )

    def test_source_export_system_grant_downgrade_revoke_with_same_session(self):
        self.client.force_login(
            self.target, backend="django.contrib.auth.backends.ModelBackend"
        )
        url = f"/workspaces/{self.workspace.pk}/permission-status/"
        first = self.client.get(url).json()
        self.apply(self.grants)
        self.assertTrue(
            sources_for(self.target, self.workspace.pk, export=True).exists()
        )
        self.assertTrue(systems_for(self.target, self.workspace.pk).exists())
        self.assertNotEqual(self.client.get(url).json(), first)
        self.apply({"sources": {str(self.source.pk): False}, "systems": []})
        self.assertTrue(sources_for(self.target, self.workspace.pk).exists())
        self.assertFalse(
            sources_for(self.target, self.workspace.pk, export=True).exists()
        )
        self.assertFalse(systems_for(self.target, self.workspace.pk).exists())
        self.apply({"sources": {}, "systems": []})
        self.assertFalse(sources_for(self.target, self.workspace.pk).exists())
        self.assertEqual(self.client.get(url).status_code, 200)
        self.membership.refresh_from_db()
        self.assertEqual(
            (self.membership.role, self.membership.active, self.membership.generation),
            ("viewer", True, 0),
        )

    def test_unknown_foreign_and_malformed_scopes_are_atomic_rejection(self):
        other = Workspace.objects.create(name="Other")
        foreign = Source.objects.create(
            workspace=other,
            owner=self.actor,
            name="Foreign",
            url="https://example.invalid/",
        )
        for grants in [
            {"sources": {str(foreign.pk): True}, "systems": []},
            {"sources": {str(self.source.pk): 1}, "systems": []},
            {"sources": {}, "systems": [str(self.system.pk)] * 2},
            {"sources": {}, "systems": [None]},
            {"sources": {}, "systems": [], "unknown": True},
        ]:
            with self.assertRaises(ValidationError):
                self.apply(grants)
        self.assertEqual(SourceGrant.objects.count(), 0)
        self.assertEqual(PortalPermissionReceipt.objects.count(), 0)

    def test_catalogue_drift_and_grant_aba_require_new_review(self):
        before = self.preview()
        Source.objects.filter(pk=self.source.pk).update(name="Renamed")
        with self.assertRaises(ValidationError):
            self.apply(self.grants, before)
        before = self.preview()
        grant = SourceGrant.objects.create(
            membership=self.membership, source=self.source
        )
        grant.delete()
        with self.assertRaises(ValidationError):
            self.apply(self.grants, before)

    def test_disabled_account_inactive_membership_and_admin_are_held(self):
        for attrs in [{"active": False}, {"role": "administrator"}]:
            Membership.objects.filter(pk=self.membership.pk).update(**attrs)
            with self.assertRaises(PermissionDenied):
                self.preview()
            Membership.objects.filter(pk=self.membership.pk).update(
                active=True, role="viewer"
            )
        self.target.is_active = False
        self.target.save()
        with self.assertRaises(PermissionDenied):
            self.preview()

    @override_settings(APLYD_SOURCE_SCOPES_ENABLED=False)
    def test_default_off(self):
        with self.assertRaises(PermissionDenied):
            self.preview()

    def test_reviewer_demotion_and_replay_denied(self):
        nonce = uuid.uuid4()
        self.apply(self.grants, nonce=nonce)
        with self.assertRaises(ValidationError):
            self.apply(self.grants, nonce=nonce)
        Membership.objects.filter(user=self.actor).update(role="viewer")
        with self.assertRaises(Membership.DoesNotExist):
            self.preview()

    def test_receipt_failure_rolls_back_scopes_and_revision(self):
        before = self.preview()
        with (
            patch.object(
                PortalPermissionReceipt.objects, "create", side_effect=RuntimeError
            ),
            self.assertRaises(RuntimeError),
        ):
            self.apply(self.grants)
        self.assertEqual(self.preview(), before)

    def test_noop_preserves_row_ids_and_revision(self):
        self.apply(self.grants)
        before = self.preview()
        pk = SourceGrant.objects.get().pk
        result = self.apply(self.grants)
        self.assertFalse(result["changed"])
        self.assertEqual(result["after"], before)
        self.assertEqual(SourceGrant.objects.get().pk, pk)

    def test_restriction_blocks_only_target_work_preserving_checkpoints_and_other_grants(
        self,
    ):
        self.apply(self.grants)
        other = Membership.objects.get(user=self.actor)
        retained = SourceGrant.objects.create(
            membership=other, source=self.source, can_export=True
        )
        version = SourceVersion.objects.create(source=self.source, version=1, config={})
        queued = WorkItem.objects.create(
            source_version=version,
            workspace=self.workspace,
            actor=self.target,
            source=self.source,
            kind="extract",
            state="queued",
            checkpoint={"offset": 7},
        )
        unrelated = WorkItem.objects.create(
            source_version=version,
            workspace=self.workspace,
            actor=self.actor,
            source=self.source,
            kind="extract",
            state="queued",
            checkpoint={"offset": 9},
        )
        self.apply({"sources": {str(self.source.pk): False}, "systems": []})
        queued.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertEqual((queued.state, queued.checkpoint), ("blocked", {"offset": 7}))
        self.assertEqual(unrelated.state, "queued")
        self.assertTrue(
            SourceGrant.objects.filter(pk=retained.pk, can_export=True).exists()
        )
        self.apply(self.grants)
        queued.refresh_from_db()
        self.assertEqual(queued.state, "blocked")
