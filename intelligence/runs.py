import hashlib
import uuid
from datetime import timedelta
from http.client import HTTPException
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from .access import NeedsAccess, connection_for, headers_for, mark_expired
from .collector import fetch_public
from .models import Capture, ContentRecord, ParseResult, Source, WorkItem
from .parsing import parse_document
from .permissions import member, sources_for
from .services import audit, check_work_permission
from .urlsafety import canonical_url, scope_decision


class LeaseLost(Exception):
    pass


DEFAULT_LIMITS = {
    "pages": 10,
    "depth": 2,
    "seconds": 180,
    "bytes": 10_000_000,
    "attempts": 3,
    "provider_cost_microunits": 0,
}


def validated_limits(config):
    supplied = config.get("limits", {})
    limits = {**DEFAULT_LIMITS, **supplied}
    for key, low, high in [
        ("pages", 1, 100),
        ("depth", 0, 5),
        ("seconds", 1, 900),
        ("bytes", 1, 20_000_000),
        ("attempts", 1, 5),
        ("provider_cost_microunits", 0, 100_000_000),
    ]:
        value = limits.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not low <= value <= high
        ):
            raise ValidationError(f"Invalid {key} budget.")
    return limits


@transaction.atomic
def start_run(actor, source, request_key):
    member(actor, source.workspace_id, write=True)
    if not sources_for(actor, source.workspace_id).filter(pk=source.pk).exists():
        raise PermissionDenied()
    source = Source.objects.select_for_update().get(pk=source.pk)
    if source.state != "active":
        raise ValidationError("Only an active, tested source can start collection.")
    if (
        not isinstance(request_key, str)
        or not request_key.strip()
        or len(request_key) > 100
    ):
        raise ValidationError("Provide a request key of up to 100 characters.")
    version = source.versions.get(version=source.config_version)
    headers_for(source, version)
    existing = WorkItem.objects.filter(
        workspace=source.workspace, request_key=request_key
    ).first()
    if existing:
        if (
            existing.actor_id != actor.pk
            or existing.source_id != source.pk
            or existing.source_version_id != version.pk
        ):
            raise ValidationError(
                "Request key already belongs to a different operation."
            )
        return existing, False
    item = WorkItem.objects.create(
        workspace=source.workspace,
        source=source,
        source_version=version,
        actor=actor,
        request_key=request_key,
        limits=validated_limits(version.config),
        checkpoint={
            "pending": [{"url": source.url, "depth": 0}],
            "visited": [],
            "skipped": [],
            "pages": 0,
        },
        usage={"bytes": 0, "provider_cost_microunits": 0},
    )
    audit(
        actor,
        source.workspace_id,
        "run.started",
        item.pk,
        {"source_version": version.version},
    )
    return item, True


@transaction.atomic
def claim_next():
    now = timezone.now()
    candidates = (
        WorkItem.objects.filter(kind="collection")
        .filter(
            Q(state__in=["queued", "retry"], next_attempt__lte=now)
            | Q(state="running", lease_until__lt=now)
        )
        .order_by("created_at")
    )
    for item in candidates[:20]:
        if item.attempts >= item.limits.get("attempts", 3):
            WorkItem.objects.filter(pk=item.pk).update(
                state="action_required", reason="Retry budget exhausted"
            )
            continue
        token = uuid.uuid4()
        updated = WorkItem.objects.filter(
            pk=item.pk, state=item.state, lease_token=item.lease_token
        ).update(
            state="running",
            lease_token=token,
            lease_until=now + timedelta(seconds=60),
            attempts=F("attempts") + 1,
            started_at=item.started_at or now,
        )
        if updated:
            item.refresh_from_db()
            return item
    return None


def stop_requested(item):
    item.refresh_from_db()
    if item.state in {"cancel_requested", "cancelled"}:
        WorkItem.objects.filter(pk=item.pk).update(
            state="cancelled",
            reason="Stopped at a safe checkpoint; retained captures are partial",
            lease_until=None,
        )
        item.refresh_from_db()
        return True
    return False


def budget_reason(item):
    usage = item.usage
    cp = item.checkpoint
    if cp.get("pages", 0) >= item.limits["pages"]:
        return "page limit"
    if usage.get("bytes", 0) >= item.limits["bytes"]:
        return "byte limit"
    if (
        item.started_at
        and (timezone.now() - item.started_at).total_seconds() >= item.limits["seconds"]
    ):
        return "time limit"
    if (
        usage.get("provider_cost_microunits", 0)
        > item.limits["provider_cost_microunits"]
    ):
        return "provider spend limit"
    return None


@transaction.atomic
def cancel_run(actor, item):
    member(actor, item.workspace_id, write=True)
    if not sources_for(actor, item.workspace_id).filter(pk=item.source_id).exists():
        raise PermissionDenied()
    item = WorkItem.objects.select_for_update().get(pk=item.pk)
    if item.state in {"completed", "partial", "cancelled"}:
        return item
    item.state = "cancel_requested" if item.state == "running" else "cancelled"
    item.reason = "Cancellation requested"
    item.save(update_fields=["state", "reason"])
    audit(actor, item.workspace_id, "run.cancel_requested", item.pk)
    return item


@transaction.atomic
def update_owned(item, token, **values):
    current = WorkItem.objects.select_for_update().get(pk=item.pk)
    if current.lease_token != token:
        raise LeaseLost()
    if current.state != "running":
        stop_requested(current)
        return current
    for key, value in values.items():
        setattr(current, key, value)
    current.save(update_fields=list(values))
    return current


@transaction.atomic
def schedule_retry(item, token):
    current = WorkItem.objects.select_for_update().get(pk=item.pk)
    if current.lease_token != token:
        raise LeaseLost()
    if stop_requested(current) or current.state != "running":
        return current
    if current.attempts >= current.limits.get("attempts", 3) or budget_reason(current):
        return update_owned(
            current,
            token,
            state="action_required",
            reason="Retry/time budget exhausted; inspect the failed capture stage",
            lease_until=None,
        )
    return update_owned(
        current,
        token,
        state="retry",
        reason="Transient capture failure; bounded retry scheduled",
        next_attempt=timezone.now() + timedelta(seconds=min(60, 2**current.attempts)),
        lease_until=None,
    )


@transaction.atomic
def account_received(item, token, amount):
    current = WorkItem.objects.select_for_update().get(pk=item.pk)
    if current.lease_token != token:
        raise LeaseLost()
    current.usage = {
        **current.usage,
        "bytes": current.usage.get("bytes", 0) + amount,
        "seconds": round((timezone.now() - current.started_at).total_seconds(), 3),
    }
    current.save(update_fields=["usage"])
    return current


def collect_step(item, fetch=fetch_public):
    token = item.lease_token
    if stop_requested(item) or not check_work_permission(item):
        return item
    if item.lease_token != token:
        raise LeaseLost()
    if item.state != "running":
        return item
    reason = budget_reason(item)
    if reason:
        return update_owned(
            item,
            token,
            state="partial",
            reason=reason,
            result={"cap": reason, "usage": item.usage, "complete": False},
            lease_until=None,
        )
    checkpoint = dict(item.checkpoint)
    pending = list(checkpoint.get("pending", []))
    if not pending:
        item.state = "partial" if checkpoint.get("skipped") else "completed"
        item.result = {
            "collected": Capture.objects.filter(run=item).count(),
            "missing": checkpoint.get("skipped", []),
            "complete": not bool(checkpoint.get("skipped")),
            "downstream_delivery": False,
        }
        item.lease_until = None
        return update_owned(
            item, token, state=item.state, result=item.result, lease_until=None
        )
    target = pending[0]
    url = target["url"]
    config = item.source_version.config
    try:
        allowed, rule = scope_decision(item.source.url, url, config)
        if not allowed:
            checkpoint.setdefault("skipped", []).append({"url": url, "reason": rule})
            pending.pop(0)
            checkpoint["pending"] = pending
            return update_owned(item, token, checkpoint=checkpoint)
        access_before = connection_for(item.source, item.source_version)
        proof = (
            (access_before.pk, access_before.checked_at, access_before.secret_ref)
            if access_before
            else None
        )
        headers = headers_for(item.source, item.source_version)
        capture = fetch(url, item.source.url, config, headers=headers)
        if capture["state"] == "needs_access":
            mark_expired(connection_for(item.source, item.source_version))
            raise NeedsAccess()
        access_after = connection_for(item.source, item.source_version)
        if (
            (access_after.pk, access_after.checked_at, access_after.secret_ref)
            if access_after
            else None
        ) != proof:
            raise NeedsAccess(
                "Account verification changed during capture; reconnect before resuming."
            )
        data = capture["bytes"]
        media_type = capture["format"]
        item = account_received(item, token, len(data))
        if item.lease_token != token:
            raise LeaseLost()
        if item.usage.get("bytes", 0) > item.limits["bytes"]:
            item.state = "partial"
            item.reason = "byte limit"
            item.result = {"cap": "byte limit", "complete": False, "usage": item.usage}
            return update_owned(
                item,
                token,
                state=item.state,
                reason=item.reason,
                result=item.result,
                lease_until=None,
            )
        if stop_requested(item) or not check_work_permission(item):
            return item
        reason = budget_reason(item)
        if reason:
            return update_owned(
                item,
                token,
                state="partial",
                reason=reason,
                result={"cap": reason, "complete": False, "usage": item.usage},
                lease_until=None,
            )
        if item.lease_token != token or item.state != "running":
            return item
        missing = list(capture.get("missing", []))
        if media_type in {"text/html", "application/xhtml+xml"}:
            challenge = BeautifulSoup(data, "html.parser")
            if (
                challenge.select_one(".g-recaptcha, .h-captcha, [data-sitekey]")
                or "verify you are human" in challenge.get_text(" ", strip=True).lower()
            ):
                item.state = "action_required"
                item.reason = (
                    "CAPTCHA or human-verification challenge; no bypass attempted"
                )
                item.result = {
                    "missing": [{"url": url, "reason": item.reason}],
                    "complete": False,
                }
                return update_owned(
                    item,
                    token,
                    state=item.state,
                    reason=item.reason,
                    result=item.result,
                    lease_until=None,
                )
        quarantined = data.startswith((b"MZ", b"\x7fELF", b"PK\x03\x04", b"#!"))
        if media_type not in {
            "text/html",
            "application/xhtml+xml",
            "application/pdf",
            "text/plain",
            "text/csv",
        }:
            missing.append(
                {"reason": "Unsupported original format; retained in quarantine"}
            )
            quarantined = True
        known_secrets = []
        if "Authorization" in headers:
            known_secrets.append(headers["Authorization"].removeprefix("Bearer "))
        if "Cookie" in headers:
            known_secrets.extend(
                p.partition("=")[2] for p in headers["Cookie"].split("; ")
            )
        if any(len(value) >= 6 and value.encode() in data for value in known_secrets):
            quarantined = True
            missing.append(
                {
                    "reason": "Authentication material detected in response; export blocked"
                }
            )
        if media_type in {"text/html", "application/xhtml+xml"}:
            html = BeautifulSoup(data, "html.parser")
            for embedded in html.find_all(
                ["img", "video", "audio", "iframe", "object"]
            ):
                missing.append(
                    {
                        "element": embedded.name,
                        "reason": "Embedded resource not captured by the static connector",
                    }
                )
            if any(
                node.get("value")
                and any(
                    part in node.get("name", "").lower()
                    for part in ["password", "csrf", "token", "secret"]
                )
                for node in html.find_all("input")
            ):
                quarantined = True
                missing.append(
                    {
                        "reason": "Sensitive form material detected; original is quarantined"
                    }
                )
        parsed = (
            {
                "status": "quarantined",
                "segments": [],
                "reason": "Unsafe or unsupported download",
            }
            if quarantined
            else parse_document(data, media_type)
        )
        if parsed["status"] != "parsed":
            missing.append({"reason": parsed["reason"]})
        text = "\n\n".join(s["text"] for s in parsed["segments"])
        digest = hashlib.sha256(data).hexdigest()
        with transaction.atomic():
            current = WorkItem.objects.select_for_update().get(pk=item.pk)
            if current.lease_token != token:
                raise LeaseLost()
            if current.state != "running":
                return current
            if not check_work_permission(current):
                return current
            saved, created = Capture.objects.get_or_create(
                run=item,
                url=url,
                defaults={
                    "workspace": item.workspace,
                    "source": item.source,
                    "digest": digest,
                    "media_type": media_type,
                    "original": data,
                    "status": "quarantined"
                    if quarantined
                    else ("partial" if missing else "captured"),
                    "missing": missing,
                },
            )
            if not created and saved.digest != digest:
                raise ValidationError(
                    "Source changed during recovery; retain the original and start an explicit new run."
                )
            if created:
                record = ContentRecord.objects.create(
                    workspace=item.workspace,
                    source=item.source,
                    title=(text.splitlines()[0] if text else url)[:300],
                    body=text,
                    status="captured" if parsed["status"] == "parsed" else "review",
                )
                saved.record = record
                saved.save(update_fields=["record"])
                ParseResult.objects.create(capture=saved, **parsed)
            visited = list(checkpoint.get("visited", []))
            visited.append(url)
            pending.pop(0)
            skipped = list(checkpoint.get("skipped", []))
            skipped.extend(missing)
            if media_type in {"text/html", "application/xhtml+xml"} and not quarantined:
                soup = BeautifulSoup(data, "html.parser")
                links = soup.select(
                    'a[rel~="next"], a.next, a[href$=".pdf"], a[href$=".docx"], a[href$=".mp4"], a[href$=".zip"]'
                )[:100]
                for link in links:
                    discovered = urljoin(url, link.get("href", ""))
                    try:
                        discovered = canonical_url(discovered)
                        included, why = scope_decision(
                            item.source.url, discovered, config
                        )
                    except ValidationError:
                        included, why = (
                            False,
                            "Unsafe or private-network discovered URL",
                        )
                    if not included:
                        skipped.append({"url": discovered[:2000], "reason": why})
                        continue
                    if target["depth"] >= item.limits["depth"]:
                        skipped.append(
                            {"url": discovered[:2000], "reason": "depth limit"}
                        )
                        continue
                    if discovered not in visited and discovered not in [
                        p["url"] for p in pending
                    ]:
                        pending.append(
                            {"url": discovered, "depth": target["depth"] + 1}
                        )
                if soup.find(["img", "video", "audio", "iframe"]) or soup.find(
                    "form", attrs={"action": True}
                ):
                    skipped.append(
                        {
                            "url": url,
                            "reason": "Interactive/media elements were not executed by the static connector",
                        }
                    )
                if "captcha" in soup.get_text(" ", strip=True).lower():
                    skipped.append(
                        {"url": url, "reason": "Possible CAPTCHA; no bypass attempted"}
                    )
            if created and missing:
                saved.missing = missing
                saved.status = "quarantined" if quarantined else "partial"
                saved.save(update_fields=["missing", "status"])
            item.checkpoint = {
                "pending": pending,
                "visited": visited,
                "skipped": skipped,
                "pages": checkpoint.get("pages", 0) + 1,
            }
            item.usage = {
                **item.usage,
                "bytes": item.usage.get("bytes", 0),
                "provider_cost_microunits": 0,
            }
            item.lease_until = timezone.now() + timedelta(seconds=60)
            item.save(update_fields=["checkpoint", "usage", "lease_until"])
            audit(
                item.actor,
                item.workspace_id,
                "capture.saved",
                saved.pk,
                {
                    "run_id": str(item.pk),
                    "source_version": item.source_version.version,
                    "status": saved.status,
                },
            )
    except NeedsAccess:
        return update_owned(
            item,
            token,
            state="needs_access",
            reason="Source access expired or is unavailable; owner must reconnect",
            lease_until=None,
        )
    except ValidationError:
        audit(
            item.actor,
            item.workspace_id,
            "capture.blocked",
            item.pk,
            {"source_version": item.source_version.version},
        )
        return update_owned(
            item,
            token,
            state="action_required",
            reason="Source scope, safety or capture limits rejected this item",
            lease_until=None,
        )
    except (OSError, TimeoutError, HTTPException):
        return schedule_retry(item, token)
    return item
