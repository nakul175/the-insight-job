import hashlib
import secrets
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from workspaces.models import Membership, Workspace

from .models import (
    AuditEvent,
    ConsumerSystem,
    DeliveryReceipt,
    Invitation,
    Schedule,
    Source,
    SourceGrant,
    SourceVersion,
    SystemGrant,
    Withdrawal,
    WorkItem,
)
from .permissions import member, sources_for, system_can_read
from .urlsafety import canonical_url


def audit(actor, workspace_id, action, obj, details=None):
    return AuditEvent.objects.create(
        actor=actor,
        workspace_id=workspace_id,
        action=action,
        object_id=str(obj),
        details=details or {},
    )


def scoped_ids(model, workspace_id, ids):
    ids = list(dict.fromkeys(str(i) for i in ids))
    if model.objects.filter(workspace_id=workspace_id, pk__in=ids).count() != len(ids):
        raise ValidationError("One or more selected scopes are unavailable.")
    return ids


@transaction.atomic
def invite(
    actor, workspace_id, email, role, source_ids=(), system_ids=(), can_export=False
):
    Workspace.objects.select_for_update().get(pk=workspace_id)
    member(actor, workspace_id, admin=True)
    if role not in {"administrator", "operator", "reviewer", "viewer"}:
        raise ValidationError("Choose a supported role.")
    sources = scoped_ids(Source, workspace_id, source_ids)
    systems = scoped_ids(ConsumerSystem, workspace_id, system_ids)
    token = secrets.token_urlsafe(32)
    invitation = Invitation.objects.create(
        workspace_id=workspace_id,
        email=email.strip().lower(),
        role=role,
        source_ids=sources,
        system_ids=systems,
        can_export=can_export,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(days=7),
        invited_by=actor,
    )
    audit(actor, workspace_id, "invitation.created", invitation.id, {"role": role})
    return invitation, token


@transaction.atomic
def accept_invitation(user, token):
    try:
        invitation = Invitation.objects.select_for_update().get(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            accepted_at__isnull=True,
            expires_at__gt=timezone.now(),
            email__iexact=user.email,
        )
    except Invitation.DoesNotExist as exc:
        raise ValidationError(
            "Invitation is unavailable, expired or belongs to another account."
        ) from exc
    # The inviter must still be an administrator at acceptance time.
    member(invitation.invited_by, invitation.workspace_id, admin=True)
    scoped_ids(Source, invitation.workspace_id, invitation.source_ids)
    scoped_ids(ConsumerSystem, invitation.workspace_id, invitation.system_ids)
    membership, created = Membership.objects.get_or_create(
        user=user,
        workspace_id=invitation.workspace_id,
        defaults={"role": invitation.role},
    )
    if not created:
        raise ValidationError(
            "This account already belongs to the workspace; an administrator must change its role explicitly."
        )
    SourceGrant.objects.bulk_create(
        [
            SourceGrant(
                membership=membership, source_id=s, can_export=invitation.can_export
            )
            for s in invitation.source_ids
        ]
    )
    SystemGrant.objects.bulk_create(
        [SystemGrant(membership=membership, system_id=s) for s in invitation.system_ids]
    )
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=["accepted_at"])
    audit(
        user,
        invitation.workspace_id,
        "invitation.accepted",
        invitation.id,
        {"role": invitation.role},
    )
    return membership


@transaction.atomic
def change_role(actor, workspace_id, membership_id, role):
    Workspace.objects.select_for_update().get(pk=workspace_id)
    member(actor, workspace_id, admin=True)
    target = Membership.objects.select_for_update().get(
        pk=membership_id, workspace_id=workspace_id
    )
    if role not in {"administrator", "operator", "reviewer", "viewer"}:
        raise ValidationError("Choose a supported role.")
    if (
        target.role == "administrator"
        and role != "administrator"
        and Membership.objects.filter(
            workspace_id=workspace_id, role="administrator"
        ).count()
        <= 1
    ):
        raise ValidationError("A workspace must retain an administrator.")
    old = target.role
    target.role = role
    target.save(update_fields=["role"])
    audit(
        actor,
        workspace_id,
        "membership.role_changed",
        target.pk,
        {"old": old, "new": role},
    )
    return target


@transaction.atomic
def revoke_membership(actor, workspace_id, membership_id):
    Workspace.objects.select_for_update().get(pk=workspace_id)
    member(actor, workspace_id, admin=True)
    target = Membership.objects.select_for_update().get(
        pk=membership_id, workspace_id=workspace_id
    )
    if (
        target.role == "administrator"
        and Membership.objects.filter(
            workspace_id=workspace_id, role="administrator"
        ).count()
        <= 1
    ):
        raise ValidationError("A workspace must retain an administrator.")
    WorkItem.objects.filter(
        workspace_id=workspace_id, actor=target.user, state__in=["queued", "running"]
    ).update(state="blocked", reason="Membership revoked")
    target_id = target.pk
    target.delete()
    audit(actor, workspace_id, "membership.revoked", target_id)


def check_work_permission(item):
    item.refresh_from_db()
    try:
        member(item.actor, item.workspace_id, write=True)
        if (
            not sources_for(item.actor, item.workspace_id)
            .filter(pk=item.source_id)
            .exists()
        ):
            raise PermissionDenied()
        if (
            item.source.state == "archived"
            and not (item.source.archive_policy == "finish" and item.state == "running")
        ) or (item.system_id and not system_can_read(item.system, item.source)):
            raise PermissionDenied()
    except (PermissionDenied, Http404):
        # Missing membership raises Http404; permission checks fail closed.
        item.state = "blocked"
        item.reason = "Current access does not permit execution"
        item.save(update_fields=["state", "reason"])
        return False
    return True


@transaction.atomic
def revoke_source(actor, workspace_id, source_id, system_id):
    Workspace.objects.select_for_update().get(pk=workspace_id)
    member(actor, workspace_id, admin=True)
    source = Source.objects.get(pk=source_id, workspace_id=workspace_id)
    system = ConsumerSystem.objects.get(pk=system_id, workspace_id=workspace_id)
    source.systemsourcegrant_set.filter(system=system).update(active=False)
    WorkItem.objects.filter(
        source=source, system=system, state__in=["queued", "running"]
    ).update(state="blocked", reason="Source permission revoked")
    for receipt in DeliveryReceipt.objects.filter(
        source=source, system=system, state="delivered"
    ):
        Withdrawal.objects.get_or_create(
            receipt=receipt,
            defaults={
                "workspace_id": workspace_id,
                "state": "queued"
                if receipt.supports_withdrawal
                else "manual_follow_up",
                "reason": "Source permission revoked",
            },
        )
    audit(
        actor,
        workspace_id,
        "source.access_revoked",
        source.id,
        {"system_id": str(system.id)},
    )


@transaction.atomic
def register_source(actor, workspace_id, name, url, owner):
    member(actor, workspace_id, write=True)
    member(owner, workspace_id)
    url = canonical_url(url)
    existing = Source.objects.filter(workspace_id=workspace_id, url=url).first()
    if existing:
        if not sources_for(actor, workspace_id).filter(pk=existing.pk).exists():
            raise PermissionDenied(
                "This source cannot be registered with your current access."
            )
        return existing, False
    source = Source.objects.create(
        workspace_id=workspace_id, name=name, url=url, owner=owner
    )
    SourceVersion.objects.create(
        source=source,
        version=1,
        config={
            "access": "public",
            "connection_id": None,
            "include": ["/"],
            "exclude": [],
            "partial_policy": "reject",
        },
    )
    m = member(actor, workspace_id)
    if m.role != "administrator":
        SourceGrant.objects.create(membership=m, source=source, can_export=False)
    audit(actor, workspace_id, "source.registered", source.id)
    return source, True


@transaction.atomic
def update_source_config(actor, source, changes):
    member(actor, source.workspace_id, write=True)
    source = Source.objects.select_for_update().get(pk=source.pk)
    if not sources_for(actor, source.workspace_id).filter(pk=source.pk).exists():
        raise PermissionDenied()
    config = dict(source.versions.get(version=source.config_version).config)
    config.update(changes)
    if config.get("partial_policy") not in {"reject", "accept_documented"}:
        raise ValidationError("Choose a partial-capture policy.")
    if (
        config.get("partial_policy") == "accept_documented"
        and not config.get("partial_reason", "").strip()
    ):
        raise ValidationError("Document why partial capture is acceptable.")
    for key in ["include", "exclude"]:
        if (
            not isinstance(config.get(key), list)
            or len(config[key]) > 30
            or any(
                not isinstance(x, str) or not x.startswith("/") or len(x) > 300
                for x in config[key]
            )
        ):
            raise ValidationError(
                "Scope rules must be up to 30 absolute path prefixes."
            )
    if config.get("access") not in {"public", "api", "session"}:
        raise ValidationError("Unsupported access method.")
    if config["access"] != "public":
        from urllib.parse import urlsplit

        from .models import Connection

        connection = Connection.objects.filter(
            pk=config.get("connection_id"),
            workspace=source.workspace,
            method=config["access"],
            domain=urlsplit(source.url).hostname,
        ).first()
        if not connection:
            raise ValidationError(
                "Select a connection scoped to this source domain and workspace."
            )
    else:
        config["connection_id"] = None
    source.config_version += 1
    source.state = "draft"
    source.save(update_fields=["config_version", "state"])
    version = SourceVersion.objects.create(
        source=source, version=source.config_version, config=config
    )
    audit(
        actor,
        source.workspace_id,
        "source.configuration_changed",
        source.id,
        {"version": version.version},
    )
    return version


@transaction.atomic
def archive_source(actor, source, policy):
    member(actor, source.workspace_id, write=True)
    if not sources_for(actor, source.workspace_id).filter(pk=source.pk).exists():
        raise PermissionDenied()
    if policy not in {"stop", "finish"}:
        raise ValidationError("Choose stop or finish for active runs.")
    Source.objects.filter(pk=source.pk).update(state="archived", archive_policy=policy)
    Schedule.objects.filter(source=source).update(enabled=False)
    WorkItem.objects.filter(source=source, state="queued").update(
        state="cancelled", reason="Source archived"
    )
    if policy == "stop":
        WorkItem.objects.filter(source=source, state="running").update(
            state="cancel_requested", reason="Source archived"
        )
    audit(
        actor,
        source.workspace_id,
        "source.archived",
        source.id,
        {"active_policy": policy},
    )
