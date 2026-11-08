"""Create one fresh Viewer under explicit native review; never link by email."""

import os
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import F

from .models import Membership, PortalIdentity, PortalPermissionReceipt, Workspace
from .portal_permissions import digest


def text(value, limit):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= limit
        or value != value.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValidationError("Invalid identity input.")
    return value


@transaction.atomic
def dispatch_provision(payload):
    if (
        not settings.APLYD_SOURCE_WRITER_ENABLED
        or not settings.APLYD_SOURCE_PROVISIONING_ENABLED
    ):
        raise PermissionDenied("Provisioning disabled.")
    issuer = text(os.environ.get("APLYD_SOURCE_ISSUER"), 250)
    organisation = text(os.environ.get("APLYD_SOURCE_ORGANISATION"), 200)
    actor_subject = text(payload.get("actor_subject"), 200)
    subject = text(payload.get("target_subject"), 200)
    name = text(payload.get("name"), 150)
    email = text(payload.get("email"), 254)
    if actor_subject == subject or "@" not in email:
        raise PermissionDenied
    workspace = Workspace.objects.select_for_update().get(
        pk=os.environ.get("APLYD_SOURCE_WORKSPACE")
    )
    Workspace.objects.filter(pk=workspace.pk).update(
        permission_revision=F("permission_revision")
    )
    workspace.refresh_from_db()
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            for key in sorted(
                ["subject:" + issuer + "\n" + subject, "email:" + email.casefold()]
            ):
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 923178))", [key]
                )
    actor = (
        PortalIdentity.objects.select_for_update()
        .select_related("user")
        .get(issuer=issuer, subject=actor_subject, workspace=workspace, reviewer=True)
    )
    if not actor.review_reference.strip() or not actor.user.is_active:
        raise PermissionDenied
    Membership.objects.get(
        user=actor.user, workspace=workspace, active=True, role="administrator"
    )
    username = "aplyd_" + digest([issuer, subject])
    users = get_user_model().objects
    if (
        PortalIdentity.objects.filter(issuer=issuer, subject=subject).exists()
        or users.filter(username=username).exists()
        or users.filter(email__iexact=email).exists()
    ):
        raise ValidationError("Existing native identity requires separate review.")
    before = {
        "issuer": issuer,
        "organisation": organisation,
        "instance": str(workspace.pk),
        "actor_subject": actor_subject,
        "actor_id": str(actor.user_id),
        "actor_review": actor.review_reference,
        "target_subject": subject,
        "name": name,
        "email": email,
        "role": "viewer",
        "active": True,
        "absent": True,
        "revision": workspace.permission_revision,
    }
    if payload.get("operation") == "provision_preview":
        return before
    if payload.get("operation") != "provision_apply" or digest(
        payload.get("expected")
    ) != digest(before):
        raise ValidationError("Review changed.")
    request_id = uuid.UUID(payload["request_id"])
    if PortalPermissionReceipt.objects.filter(pk=request_id).exists():
        raise ValidationError("Request already used.")
    user = users.create_user(
        username=username, email=email, first_name=name, password=None
    )
    Membership.objects.create(
        user=user, workspace=workspace, role="viewer", active=True
    )
    PortalIdentity.objects.create(
        issuer=issuer,
        subject=subject,
        user=user,
        workspace=workspace,
        reviewer=False,
        review_reference="Portal creation " + str(request_id),
    )
    result = {
        "request_id": str(request_id),
        "issuer": issuer,
        "instance": str(workspace.pk),
        "subject": subject,
        "native_id": str(user.pk),
        "role": "viewer",
        "active": True,
    }
    PortalPermissionReceipt.objects.create(request_id=request_id, result=result)
    return result
