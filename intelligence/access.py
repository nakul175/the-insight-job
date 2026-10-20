import os
import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import BrowserAction, BrowserHandoff, Connection, WorkItem
from .permissions import member, sources_for
from .services import audit, check_work_permission
from .urlsafety import canonical_url
from .vault import seal, unseal, vault


class NeedsAccess(Exception):
    pass


def own_connection(actor, connection):
    member(actor, connection.workspace_id, write=True)
    if connection.owner_id != actor.pk:
        raise PermissionDenied(
            "Only this connection's owner can manage its account material."
        )


def connection_for(source, version=None):
    version = version or source.versions.get(version=source.config_version)
    if version.config.get("access") == "public":
        return None
    conn = Connection.objects.filter(
        pk=version.config.get("connection_id"),
        workspace=source.workspace,
        bound_source=source,
        domain=urlsplit(source.url).hostname,
        method=version.config.get("access"),
    ).first()
    if not conn or conn.state != "connected":
        raise NeedsAccess("Reconnect the source account before continuing.")
    from django.http import Http404

    try:
        member(conn.owner, source.workspace_id, write=True)
        if (
            not sources_for(conn.owner, source.workspace_id)
            .filter(pk=source.pk)
            .exists()
        ):
            raise PermissionDenied()
    except (PermissionDenied, Http404) as exc:
        raise NeedsAccess("The account owner no longer has source access.") from exc
    return conn


def headers_for(source, version):
    conn = connection_for(source, version)
    if conn is None:
        return {}
    if conn.method == "api":
        if not re.fullmatch(r"TIJ_SECRET_[A-Z0-9_]{1,100}", conn.secret_ref):
            raise NeedsAccess("Configure a scoped API secret reference.")
        secret = os.environ.get(conn.secret_ref)
        if not secret or len(secret) > 16384 or "\r" in secret or "\n" in secret:
            raise NeedsAccess("The configured API secret is unavailable.")
        return {"Authorization": "Bearer " + secret}
    try:
        material = unseal(conn.session_ciphertext)
    except ValidationError as exc:
        raise NeedsAccess("Reconnect the unavailable source session.") from exc
    cookies = []
    host = urlsplit(source.url).hostname
    path = urlsplit(source.url).path or "/"
    for c in material.get("cookies", []):
        domain = c.get("domain", "").lstrip(".")
        if (host != domain and not host.endswith("." + domain)) or not path.startswith(
            c.get("path", "/")
        ):
            continue
        if c.get("expires", -1) > 0 and c["expires"] <= timezone.now().timestamp():
            continue
        if any(x in c.get("name", "") + c.get("value", "") for x in ["\r", "\n", ";"]):
            continue
        cookies.append(c["name"] + "=" + c["value"])
    if not cookies:
        raise NeedsAccess("No usable source session remains; reconnect.")
    return {"Cookie": "; ".join(cookies)}


@transaction.atomic
def begin_login(actor, source, login_url, identity_selector, expected_identity):
    member(actor, source.workspace_id, write=True)
    if not sources_for(actor, source.workspace_id).filter(pk=source.pk).exists():
        raise PermissionDenied()
    vault()
    login_url = canonical_url(login_url)
    if (
        urlsplit(source.url).scheme != "https"
        or urlsplit(login_url).scheme != "https"
        or urlsplit(login_url).hostname != urlsplit(source.url).hostname
    ):
        raise ValidationError("Login must use HTTPS on the approved source domain.")
    if (
        not identity_selector.strip()
        or not expected_identity.strip()
        or len(identity_selector) > 300
        or len(expected_identity) > 200
    ):
        raise ValidationError(
            "Specify the website's account-identity selector and expected account identity."
        )
    conn = Connection.objects.filter(
        workspace=source.workspace,
        owner=actor,
        bound_source=source,
        domain=urlsplit(source.url).hostname,
        method="session",
    ).first()
    if conn is None:
        conn = Connection.objects.create(
            workspace=source.workspace,
            owner=actor,
            bound_source=source,
            domain=urlsplit(source.url).hostname,
            method="session",
            name=source.name + " session",
        )
    conn.login_url = login_url
    conn.identity_selector = identity_selector
    conn.expected_identity = expected_identity
    conn.allowed_login_hosts = [conn.domain]
    conn.state = "connecting"
    conn.save()
    BrowserAction.objects.filter(
        handoff__connection=conn, handoff__state__in=["pending", "active"]
    ).delete()
    BrowserHandoff.objects.filter(
        connection=conn, state__in=["pending", "active"]
    ).update(state="cancelled", frame_ciphertext="")
    handoff = BrowserHandoff.objects.create(
        workspace=source.workspace,
        source=source,
        connection=conn,
        owner=actor,
        expires_at=timezone.now() + timedelta(minutes=20),
    )
    audit(
        actor,
        source.workspace_id,
        "connection.login_started",
        conn.pk,
        {"handoff_id": str(handoff.pk), "domain": conn.domain},
    )
    return handoff


def handoff_permission(actor, handoff):
    own_connection(actor, handoff.connection)
    if (
        handoff.owner_id != actor.pk
        or not sources_for(actor, handoff.workspace_id)
        .filter(pk=handoff.source_id)
        .exists()
    ):
        raise PermissionDenied()
    if handoff.expires_at <= timezone.now() or handoff.state not in {
        "pending",
        "active",
    }:
        raise ValidationError("This login session is no longer active.")


@transaction.atomic
def submit_browser_action(actor, handoff, kind, payload):
    handoff_permission(actor, handoff)
    if not isinstance(payload, dict):
        raise ValidationError("Invalid browser action payload.")
    if kind not in {"click", "type", "key", "scroll", "verify", "cancel"}:
        raise ValidationError("Unsupported browser action.")
    if kind == "type" and (
        not isinstance(payload.get("text"), str) or len(payload["text"]) > 2000
    ):
        raise ValidationError("Input is too long.")
    if kind == "click" and not (
        0 <= int(payload.get("x", -1)) <= 1280 and 0 <= int(payload.get("y", -1)) <= 800
    ):
        raise ValidationError("Click outside the browser viewport.")
    if kind == "key" and payload.get("key") not in {
        "Tab",
        "Enter",
        "Backspace",
        "Escape",
        "ArrowDown",
        "ArrowUp",
    }:
        raise ValidationError("Unsupported key.")
    if BrowserAction.objects.filter(handoff=handoff).count() >= 20:
        raise ValidationError("Wait for pending browser actions to complete.")
    return BrowserAction.objects.create(
        handoff=handoff, kind=kind, payload_ciphertext=seal(payload)
    )


@transaction.atomic
def verify_identity(connection, identity, storage_state):
    own_connection(connection.owner, connection)
    if (
        not connection.bound_source_id
        or not sources_for(connection.owner, connection.workspace_id)
        .filter(pk=connection.bound_source_id)
        .exists()
    ):
        raise PermissionDenied()
    if identity.strip() != connection.expected_identity.strip():
        raise ValidationError(
            "The expected account identity is not present on this page."
        )
    if connection.verified_identity and connection.verified_identity != identity:
        connection.pending_ciphertext = seal(storage_state)
        connection.state = "identity_changed"
    else:
        connection.session_ciphertext = seal(storage_state)
        connection.verified_identity = identity
        connection.state = "connected"
        connection.checked_at = timezone.now()
    connection.save()
    audit(
        connection.owner,
        connection.workspace_id,
        "connection.identity_checked",
        connection.pk,
        {"state": connection.state, "domain": connection.domain},
    )
    if connection.state == "connected":
        resume_waiting(connection)


@transaction.atomic
def confirm_changed_identity(actor, connection):
    own_connection(actor, connection)
    if (
        not sources_for(actor, connection.workspace_id)
        .filter(pk=connection.bound_source_id)
        .exists()
    ):
        raise PermissionDenied()
    if connection.state != "identity_changed" or not connection.pending_ciphertext:
        raise ValidationError("No account change is awaiting confirmation.")
    connection.session_ciphertext = connection.pending_ciphertext
    connection.pending_ciphertext = ""
    connection.verified_identity = connection.expected_identity
    connection.checked_at = timezone.now()
    connection.state = "connected"
    connection.save()
    audit(
        actor,
        connection.workspace_id,
        "connection.account_change_confirmed",
        connection.pk,
    )
    resume_waiting(connection)


def resume_waiting(connection):
    for item in WorkItem.objects.filter(
        workspace=connection.workspace,
        state="needs_access",
        source_version__config__connection_id=str(connection.pk),
    ):
        if check_work_permission(item):
            item.state = "queued"
            item.next_attempt = timezone.now()
            item.reason = ""
            item.save(update_fields=["state", "next_attempt", "reason"])


def mark_expired(connection):
    if connection:
        connection.state = "needs_access"
        connection.save(update_fields=["state"])


@transaction.atomic
def disconnect(actor, connection):
    own_connection(actor, connection)
    connection.state = "disconnected"
    connection.session_ciphertext = ""
    connection.pending_ciphertext = ""
    connection.secret_ref = ""
    connection.save()
    handoffs = BrowserHandoff.objects.filter(connection=connection)
    BrowserAction.objects.filter(handoff__in=handoffs).delete()
    handoffs.update(state="cancelled", frame_ciphertext="")
    WorkItem.objects.filter(
        workspace=connection.workspace,
        source_version__config__connection_id=str(connection.pk),
        state__in=["queued", "running", "needs_access", "retry"],
    ).update(state="blocked", reason="Connection disconnected")
    audit(actor, connection.workspace_id, "connection.disconnected", connection.pk)


@transaction.atomic
def rotate_api(actor, connection, new_ref, source, fetch):
    own_connection(actor, connection)
    if not sources_for(actor, source.workspace_id).filter(pk=source.pk).exists():
        raise PermissionDenied()
    if (
        connection.method != "api"
        or connection.domain != urlsplit(source.url).hostname
        or connection.workspace_id != source.workspace_id
        or connection.bound_source_id != source.pk
    ):
        raise ValidationError("Select a matching source/API connection.")
    if not re.fullmatch(r"TIJ_SECRET_[A-Z0-9_]{1,100}", new_ref):
        raise ValidationError(
            "Enter an environment secret reference, never the token value."
        )
    if urlsplit(source.url).scheme != "https":
        raise ValidationError("API credentials require an HTTPS source.")
    value = os.environ.get(new_ref)
    if not value:
        raise ValidationError("That secret reference is not configured.")
    report = fetch(
        source.url,
        source.url,
        source.versions.get(version=source.config_version).config,
        headers={"Authorization": "Bearer " + value},
    )
    if report.get("state") != "captured":
        raise ValidationError(
            "The replacement secret did not pass its source check; the old reference remains unchanged."
        )
    connection.secret_ref = new_ref
    connection.state = "connected"
    connection.checked_at = timezone.now()
    connection.save()
    audit(actor, connection.workspace_id, "connection.secret_rotated", connection.pk)
