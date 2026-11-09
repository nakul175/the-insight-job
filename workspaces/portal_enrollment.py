"""Add a Viewer membership for an exact, already reviewed native identity."""

import os
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F

from intelligence.services import audit

from .models import Membership, PortalIdentity, PortalPermissionReceipt, Workspace
from .portal_permissions import digest
from .portal_provisioning import text


@transaction.atomic
def dispatch_enrollment(payload):
    if not settings.APLYD_SOURCE_WRITER_ENABLED or not getattr(
        settings, "APLYD_SOURCE_ENROLLMENT_ENABLED", False
    ):
        raise PermissionDenied("Workspace enrollment disabled.")
    issuer = text(os.environ.get("APLYD_SOURCE_ISSUER"), 250)
    organisation = text(os.environ.get("APLYD_SOURCE_ORGANISATION"), 200)
    destination = uuid.UUID(text(os.environ.get("APLYD_SOURCE_WORKSPACE"), 36))
    origin = uuid.UUID(text(payload.get("origin_workspace"), 36))
    actor_subject = text(payload.get("actor_subject"), 200)
    subject = text(payload.get("target_subject"), 200)
    native_id = text(payload.get("target_id"), 100)
    if origin == destination or actor_subject == subject:
        raise PermissionDenied("Distinct reviewed scope and people required.")
    spaces = list(
        Workspace.objects.select_for_update()
        .filter(pk__in=[origin, destination])
        .order_by("pk")
    )
    if len(spaces) != 2:
        raise PermissionDenied("Reviewed workspaces unavailable.")
    # Acquire the SQLite writer lock before reading identity and absence proofs.
    Workspace.objects.filter(pk__in=[origin, destination]).update(
        permission_revision=F("permission_revision")
    )
    actor = (
        PortalIdentity.objects.select_for_update()
        .select_related("user")
        .get(
            issuer=issuer,
            subject=actor_subject,
            workspace_id=destination,
            reviewer=True,
        )
    )
    if not actor.review_reference.strip() or not actor.user.is_active:
        raise PermissionDenied("Current reviewed administrator required.")
    admin = Membership.objects.select_for_update().get(
        user=actor.user, workspace_id=destination, active=True, role="administrator"
    )
    target = (
        PortalIdentity.objects.select_for_update()
        .select_related("user")
        .get(issuer=issuer, subject=subject, workspace_id=origin, user_id=native_id)
    )
    home = Membership.objects.select_for_update().get(
        user=target.user, workspace_id=origin, active=True
    )
    identities = list(
        PortalIdentity.objects.select_for_update()
        .filter(issuer=issuer, subject=subject)
        .order_by("pk")
        .values("id", "user_id", "workspace_id", "reviewer", "review_reference")
    )
    if (
        not target.review_reference.strip()
        or not target.user.is_active
        or target.user.is_staff
        or target.user.is_superuser
        or target.user_id == actor.user_id
        or any(
            str(row["user_id"]) != native_id or not row["review_reference"].strip()
            for row in identities
        )
        or Membership.objects.filter(
            user=target.user, workspace_id=destination
        ).exists()
        or PortalIdentity.objects.filter(
            workspace_id=destination, subject=subject, issuer=issuer
        ).exists()
        or PortalIdentity.objects.filter(
            workspace_id=destination, user=target.user
        ).exists()
    ):
        raise PermissionDenied(
            "Existing destination access or ambiguous identity requires review."
        )
    for workspace in spaces:
        workspace.refresh_from_db()
    before = {
        "issuer": issuer,
        "organisation": organisation,
        "instance": str(destination),
        "origin_workspace": str(origin),
        "actor_subject": actor_subject,
        "actor_id": str(actor.user_id),
        "actor_review": actor.review_reference,
        "actor_generation": admin.generation,
        "target_subject": subject,
        "target_id": native_id,
        "origin_membership": {
            "id": home.pk,
            "role": home.role,
            "generation": home.generation,
        },
        "identity_sha256": digest(identities),
        "workspace_revisions": {str(w.pk): w.permission_revision for w in spaces},
        "role": "viewer",
        "active": True,
        "absent": True,
    }
    if payload.get("operation") == "enrollment_preview":
        return before
    if payload.get("operation") != "enrollment_apply" or digest(
        payload.get("expected")
    ) != digest(before):
        raise ValidationError("Enrollment review changed.")
    request_id = uuid.UUID(payload["request_id"])
    if (
        request_id.int == 0
        or PortalPermissionReceipt.objects.filter(pk=request_id).exists()
    ):
        raise ValidationError("Request already used or invalid.")
    membership = Membership.objects.create(
        user=target.user, workspace_id=destination, role="viewer", active=True
    )
    PortalIdentity.objects.create(
        issuer=issuer,
        subject=subject,
        user=target.user,
        workspace_id=destination,
        reviewer=False,
        review_reference="Portal enrollment " + str(request_id),
    )
    membership.refresh_from_db()
    result = {
        "request_id": str(request_id),
        "before": before,
        "issuer": issuer,
        "instance": str(destination),
        "subject": subject,
        "native_id": native_id,
        "membership_id": membership.pk,
        "generation": membership.generation,
        "role": "viewer",
        "active": True,
    }
    audit(
        actor.user,
        destination,
        "membership.portal_enrolled",
        membership.pk,
        {
            "request_id": str(request_id),
            "origin_workspace": str(origin),
            "role": "viewer",
        },
    )
    PortalPermissionReceipt.objects.create(request_id=request_id, result=result)
    return result
