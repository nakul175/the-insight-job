"""Exact Source/System/export grant replacement for one reviewed membership."""

import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from intelligence.models import (
    ConsumerSystem,
    Source,
    SourceGrant,
    SystemGrant,
    WorkItem,
)
from intelligence.services import audit

from .models import PortalPermissionReceipt
from .portal_permissions import digest, identities, plan


def scope_plan(workspace, actor, target, membership, organisation):
    result = plan(workspace, actor, target, membership, organisation)
    sources = list(
        Source.objects.filter(workspace=workspace).order_by("id").values("id", "name")
    )
    systems = list(
        ConsumerSystem.objects.filter(workspace=workspace)
        .order_by("id")
        .values("id", "name")
    )
    if len(sources) > 200 or len(systems) > 200:
        raise ValidationError("Use a separately reviewed large-workspace editor.")
    result["catalogue"] = {
        "sources": [{"id": str(s["id"]), "name": s["name"]} for s in sources],
        "systems": [{"id": str(s["id"]), "name": s["name"]} for s in systems],
    }
    result["grants"] = {
        "sources": {
            str(s.source_id): s.can_export
            for s in SourceGrant.objects.filter(membership=membership)
        },
        "systems": sorted(
            str(s)
            for s in SystemGrant.objects.filter(membership=membership).values_list(
                "system_id", flat=True
            )
        ),
    }
    # Corrupt cross-workspace rows must be reviewed, never silently removed.
    validate_grants(result["grants"], result["catalogue"])
    result["scopes_sha256"] = digest(result["grants"])
    return result


def validate_grants(grants, catalogue):
    if not isinstance(grants, dict) or set(grants) != {"sources", "systems"}:
        raise ValidationError("Invalid scopes.")
    sources, systems = grants["sources"], grants["systems"]
    if (
        not isinstance(sources, dict)
        or not isinstance(systems, list)
        or any(not isinstance(s, str) for s in systems)
        or len(systems) != len(set(systems))
        or any(type(v) is not bool for v in sources.values())
        or not set(sources) <= {s["id"] for s in catalogue["sources"]}
        or not set(systems) <= {s["id"] for s in catalogue["systems"]}
    ):
        raise ValidationError("Scopes must belong to this exact workspace.")
    return {"sources": dict(sorted(sources.items())), "systems": sorted(systems)}


@transaction.atomic
def dispatch_scopes(payload):
    if not getattr(settings, "APLYD_SOURCE_SCOPES_ENABLED", False):
        raise PermissionDenied("Scope editor disabled.")
    workspace, actor, target, membership, organisation, _ = identities(payload)
    if (
        not membership.active
        or not target.user.is_active
        or membership.role == "administrator"
    ):
        raise PermissionDenied("Select an active non-administrator membership.")
    before = scope_plan(workspace, actor, target, membership, organisation)
    if payload.get("operation") == "scopes_preview":
        return before
    if payload.get("operation") != "scopes_apply":
        raise ValidationError("Unsupported operation.")
    request_id = uuid.UUID(payload["request_id"])
    if PortalPermissionReceipt.objects.filter(pk=request_id).exists():
        raise ValidationError("Request already used.")
    if digest(payload.get("expected")) != digest(before):
        raise ValidationError("Scope review changed.")
    desired = validate_grants(payload.get("grants"), before["catalogue"])
    old = before["grants"]
    changed = desired != old
    if changed:
        SourceGrant.objects.filter(membership=membership).exclude(
            source_id__in=desired["sources"]
        ).delete()
        for source_id, can_export in desired["sources"].items():
            if source_id not in old["sources"]:
                SourceGrant.objects.create(
                    membership=membership, source_id=source_id, can_export=can_export
                )
            elif old["sources"][source_id] != can_export:
                SourceGrant.objects.filter(
                    membership=membership, source_id=source_id
                ).update(can_export=can_export)
        SystemGrant.objects.filter(membership=membership).exclude(
            system_id__in=desired["systems"]
        ).delete()
        SystemGrant.objects.bulk_create(
            [
                SystemGrant(membership=membership, system_id=s)
                for s in desired["systems"]
                if s not in old["systems"]
            ]
        )
        # Restrict pending work conservatively when any read/export scope is removed.
        # Checkpoints remain intact. Regrant never automatically resumes work.
        diminished = (
            bool(set(old["sources"]) - set(desired["sources"]))
            or bool(set(old["systems"]) - set(desired["systems"]))
            or any(
                v and not desired["sources"].get(k, False)
                for k, v in old["sources"].items()
            )
        )
        if diminished:
            WorkItem.objects.filter(
                workspace=workspace, actor=target.user, state__in=["queued", "running"]
            ).update(state="blocked", reason="Source scopes restricted")
        audit(
            actor.user,
            workspace.pk,
            "membership.portal_scopes_changed",
            membership.pk,
            {
                "request_id": str(request_id),
                "before_sha256": digest(old),
                "after_sha256": digest(desired),
            },
        )
    result = {
        "request_id": str(request_id),
        "changed": changed,
        "before": before,
        "after": scope_plan(workspace, actor, target, membership, organisation),
    }
    PortalPermissionReceipt.objects.create(request_id=request_id, result=result)
    return result
