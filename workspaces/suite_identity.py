"""Explicit operator-reviewed identity bindings; never match users by email."""

import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model

from .suite_oidc import OIDCError


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate binding key")
        result[key] = value
    return result


def file_mapped_user(subject, expected_local_id=None):
    try:
        path = Path(settings.APLYD_SOURCE_BINDINGS_FILE)
        if not path.is_absolute():
            raise ValueError("Absolute binding file required")
        with path.open("rb") as stream:
            raw = stream.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("Oversized binding file")
        data = json.loads(raw, object_pairs_hook=unique_object)
        if (
            type(data.get("version")) is not int
            or data["version"] != 1
            or data.get("issuer") != settings.APLYD_ACCOUNTS_ISSUER
            or data.get("origin") != settings.APLYD_SOURCE_ORIGIN
            or data.get("client_id") != settings.APLYD_SOURCE_CLIENT_ID
            or not isinstance(data.get("bindings"), list)
            or not isinstance(subject, str)
            or not 0 < len(subject) <= 200
        ):
            raise ValueError("Binding context mismatch")
        rows = [
            r
            for r in data["bindings"]
            if isinstance(r, dict) and r.get("subject") == subject
        ]
        if len(rows) != 1:
            raise ValueError("Missing or ambiguous binding")
        row = rows[0]
        local = row.get("native_user_id")
        review = row.get("review_reference")
        if (
            row.get("enabled") is not True
            or not isinstance(local, str)
            or not 0 < len(local) <= 200
            or local != local.strip()
            or not isinstance(review, str)
            or not review.strip()
            or (expected_local_id is not None and local != expected_local_id)
        ):
            raise ValueError("Binding unavailable")
        user = get_user_model().objects.get(
            pk=local, is_active=True, is_staff=False, is_superuser=False
        )
        if str(user.pk) != local:
            raise ValueError("Noncanonical native identity")
        return user
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        get_user_model().DoesNotExist,
    ) as error:
        raise OIDCError("Reviewed identity unavailable") from error


def identity_source():
    configured = settings.APLYD_SOURCE_IDENTITY_SOURCE
    if not configured:
        return "file" if settings.APLYD_SOURCE_BINDINGS_FILE else "portal"
    if configured not in ("file", "portal"):
        raise OIDCError("Unknown identity authority")
    return configured


def mapped_user(subject, expected_local_id=None):
    if identity_source() == "file":
        return file_mapped_user(subject, expected_local_id)
    from .models import PortalIdentity

    if not isinstance(subject, str) or not 0 < len(subject) <= 250:
        raise OIDCError("Subject unavailable")
    rows = list(
        PortalIdentity.objects.filter(
            issuer=settings.APLYD_ACCOUNTS_ISSUER, subject=subject
        ).select_related("user")
    )
    if (
        not rows
        or any(not row.review_reference.strip() for row in rows)
        or len({str(row.user_id) for row in rows}) != 1
        or (expected_local_id is not None and str(rows[0].user_id) != expected_local_id)
    ):
        raise OIDCError("Reviewed mapping unavailable")
    user = rows[0].user
    if not user.is_active or user.is_staff or user.is_superuser:
        raise OIDCError("Native account unavailable")
    return user
