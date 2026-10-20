from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from workspaces.models import Membership

from .models import ConsumerSystem, ContentRecord, Source, SourceGrant, SystemGrant

WRITE_ROLES = {"administrator", "operator"}


def member(user, workspace_id, write=False, admin=False):
    m = get_object_or_404(Membership, user=user, workspace_id=workspace_id)
    if admin and m.role != "administrator":
        raise PermissionDenied("Administrator access required.")
    if write and m.role not in WRITE_ROLES:
        raise PermissionDenied("Your role cannot perform this action.")
    return m


def sources_for(user, workspace_id, export=False):
    m = member(user, workspace_id)
    qs = Source.objects.filter(workspace_id=workspace_id)
    if m.role == "administrator":
        return qs
    grants = SourceGrant.objects.filter(membership=m, source__workspace_id=workspace_id)
    if export:
        grants = grants.filter(can_export=True)
    return qs.filter(id__in=grants.values("source_id"))


def systems_for(user, workspace_id):
    m = member(user, workspace_id)
    qs = ConsumerSystem.objects.filter(workspace_id=workspace_id)
    if m.role == "administrator":
        return qs
    return qs.filter(
        id__in=SystemGrant.objects.filter(
            membership=m, system__workspace_id=workspace_id
        ).values("system_id")
    )


def records_for(user, workspace_id, export=False):
    return ContentRecord.objects.filter(
        workspace_id=workspace_id,
        source_id__in=sources_for(user, workspace_id, export=export).values("id"),
        source__workspace_id=workspace_id,
    )


def system_can_read(system, source):
    return (
        system.workspace_id == source.workspace_id
        and system.state not in {"paused", "retired"}
        and source.systemsourcegrant_set.filter(system=system, active=True).exists()
    )


def runs_for(user, workspace_id):
    from django.db.models import Q

    from .models import WorkItem

    return WorkItem.objects.filter(
        workspace_id=workspace_id, source__in=sources_for(user, workspace_id)
    ).filter(Q(system__isnull=True) | Q(system__in=systems_for(user, workspace_id)))
