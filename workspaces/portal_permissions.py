"""OS-local reviewed bridge: no public writer, no identity inference."""

import hashlib
import json
import os
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F

from intelligence.models import SourceGrant, SystemGrant, WorkItem
from intelligence.services import audit

from .models import Membership, PortalIdentity, PortalPermissionReceipt, Workspace

ROLES = {"administrator", "operator", "reviewer", "viewer"}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def identities(payload):
    if not settings.APLYD_SOURCE_WRITER_ENABLED:
        raise PermissionDenied("Connector disabled.")
    issuer = os.environ.get("APLYD_SOURCE_ISSUER", "")
    workspace_id = os.environ.get("APLYD_SOURCE_WORKSPACE", "")
    organisation = os.environ.get("APLYD_SOURCE_ORGANISATION", "")
    if not issuer or not workspace_id or not organisation:
        raise PermissionDenied("Connector not provisioned.")
    workspace = Workspace.objects.get(pk=workspace_id)
    # A real write obtains SQLite's writer lock as well as PostgreSQL row locks.
    Workspace.objects.filter(pk=workspace.pk).update(
        permission_revision=F("permission_revision")
    )
    workspace.refresh_from_db()
    refs = [
        PortalIdentity.objects.select_for_update()
        .select_related("user")
        .get(issuer=issuer, subject=payload[k], workspace=workspace)
        for k in ("actor_subject", "target_subject")
    ]
    actor, target = refs
    if (
        not actor.reviewer
        or actor.user_id == target.user_id
        or any(not r.review_reference.strip() for r in refs)
        or not actor.user.is_active
    ):
        raise PermissionDenied("Reviewed distinct identities required.")
    reviewer = Membership.objects.get(
        user=actor.user, workspace=workspace, active=True, role="administrator"
    )
    membership = Membership.objects.select_for_update().get(
        user=target.user, workspace=workspace
    )
    return workspace, actor, target, membership, organisation, reviewer


def plan(workspace, actor, target, membership, organisation):
    workspace.refresh_from_db()
    membership.refresh_from_db()
    return {
        "issuer": actor.issuer,
        "organisation": organisation,
        "instance": str(workspace.pk),
        "actor_subject": actor.subject,
        "target_subject": target.subject,
        "actor_id": str(actor.user_id),
        "target_id": str(target.user_id),
        "role": membership.role,
        "active": membership.active,
        "generation": membership.generation,
        "revision": workspace.permission_revision,
        "scopes_sha256": digest(
            {
                "sources": list(
                    SourceGrant.objects.filter(membership=membership)
                    .order_by("pk")
                    .values("source_id", "can_export")
                ),
                "systems": list(
                    SystemGrant.objects.filter(membership=membership)
                    .order_by("pk")
                    .values_list("system_id", flat=True)
                ),
            }
        ),
    }


@transaction.atomic
def dispatch(payload):
    workspace, actor, target, membership, organisation, _reviewer = identities(payload)
    before = plan(workspace, actor, target, membership, organisation)
    if payload.get("operation") == "preview":
        return before
    if payload.get("operation") != "apply":
        raise ValidationError("Unsupported operation.")
    request_id = uuid.UUID(payload["request_id"])
    # Never repeat an uncertain or previously committed request, even if identical.
    if PortalPermissionReceipt.objects.filter(pk=request_id).exists():
        raise ValidationError("Request already used.")
    if digest(payload.get("expected")) != digest(before):
        raise ValidationError("Native permissions changed; review again.")
    choice = payload.get("choice")
    if choice not in ROLES | {"revoke"}:
        raise ValidationError("Unsupported role.")
    if choice != "revoke" and not target.user.is_active:
        raise PermissionDenied("Disabled account.")
    active = choice != "revoke"
    role = membership.role if not active else choice
    if (
        membership.active
        and membership.role == "administrator"
        and (not active or role != "administrator")
        and Membership.objects.filter(
            workspace=workspace, active=True, role="administrator"
        ).count()
        <= 1
    ):
        raise ValidationError("Retain a workspace administrator.")
    changed = membership.active != active or membership.role != role
    if changed:
        membership.active = active
        membership.role = role
        membership.save(update_fields=["active", "role"])
        if not active:
            WorkItem.objects.filter(
                workspace=workspace, actor=target.user, state__in=["queued", "running"]
            ).update(state="blocked", reason="Membership suspended")
        audit(
            actor.user,
            workspace.pk,
            "membership.portal_permission_changed",
            membership.pk,
            {
                "old_role": before["role"],
                "role": role,
                "active": active,
                "request_id": str(request_id),
            },
        )
    result = {
        "request_id": str(request_id),
        "changed": changed,
        "before": before,
        "after": plan(workspace, actor, target, membership, organisation),
    }
    PortalPermissionReceipt.objects.create(request_id=request_id, result=result)
    return result
