"""Bounded evidence-link traversal using existing permission-checked capture jobs.

One page is scheduled at a time; aggregate attempt budgets constrain each capture.
Only the existing HTML/PDF connector is used; media/provider budgets remain zero.
"""

from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from workspaces.models import Workspace

from .access import NeedsAccess
from .models import EnrichmentAttempt, EnrichmentPolicy, WorkItem
from .permissions import records_for, sources_for, system_can_read
from .runs import validated_limits
from .services import audit
from .systems import owned_system
from .urlsafety import canonical_url, scope_decision


@transaction.atomic
def save_policy(actor, workspace_id, system_id, expected_number, config):
    system = owned_system(actor, workspace_id, system_id)
    latest = system.enrichment_policies.order_by("-number").first()
    if (latest.number if latest else 0) != expected_number:
        raise ValidationError("Enrichment policy changed; reload before saving.")
    if not isinstance(config, dict) or set(config) != {
        "domains",
        "depth",
        "pages",
        "seconds",
        "bytes",
        "media_seconds",
        "provider_cost_microunits",
    }:
        raise ValidationError(
            "Explicit domains, depth, pages, seconds, bytes, media_seconds and provider_cost_microunits are required."
        )
    domains = config["domains"]
    if (
        not isinstance(domains, list)
        or not 1 <= len(domains) <= 20
        or any(
            not isinstance(d, str)
            or urlsplit(canonical_url("https://" + d, resolve=False)).hostname != d
            for d in domains
        )
    ):
        raise ValidationError("Provide 1–20 exact public domain names.")
    for key, low, high in [
        ("depth", 0, 5),
        ("pages", 1, 100),
        ("seconds", 1, 900),
        ("bytes", 1, 20_000_000),
        ("media_seconds", 0, 0),
        ("provider_cost_microunits", 0, 0),
    ]:
        value = config[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not low <= value <= high
        ):
            raise ValidationError(f"Invalid {key} cap; supported range {low}–{high}.")
    permitted_domains = {
        urlsplit(s.url).hostname
        for s in sources_for(actor, workspace_id)
        if system_can_read(system, s)
    }
    if not set(domains) <= permitted_domains:
        raise ValidationError(
            "Every domain requires a permitted registered source and system grant."
        )
    policy = EnrichmentPolicy.objects.create(
        system=system, number=expected_number + 1, config=config, actor=actor
    )
    audit(
        actor,
        workspace_id,
        "enrichment.policy_saved",
        system.pk,
        {"version": policy.number},
    )
    return policy


@transaction.atomic
def start_enrichment(
    actor, workspace_id, system_id, record_id, trigger, links, unresolved_fields
):
    system = owned_system(actor, workspace_id, system_id)
    record = get_object_or_404(records_for(actor, workspace_id), pk=record_id)
    if not system_can_read(system, record.source):
        raise PermissionDenied("System source access required.")
    policy = system.enrichment_policies.order_by("-number").first()
    if not policy:
        raise ValidationError("Configure enrichment limits first.")
    if (
        not isinstance(trigger, str)
        or not trigger.strip()
        or len(trigger) > 500
        or not isinstance(links, list)
        or len(links) > 100
        or not isinstance(unresolved_fields, list)
        or len(unresolved_fields) > 50
        or any(not isinstance(k, str) or len(k) > 60 for k in unresolved_fields)
    ):
        raise ValidationError(
            "Provide an approved trigger, up to 100 links and 50 unresolved field keys."
        )
    unique_links = list(
        dict.fromkeys(canonical_url(link, resolve=False) for link in links)
    )
    pending = [
        {"url": link, "depth": 0, "parent": str(record.pk)} for link in unique_links
    ]
    attempt = EnrichmentAttempt.objects.create(
        workspace_id=workspace_id,
        policy=policy,
        record=record,
        actor=actor,
        trigger=trigger,
        pending=pending,
        unresolved_fields=unresolved_fields,
        usage={
            "pages": 0,
            "seconds": 0,
            "bytes": 0,
            "media_seconds": 0,
            "provider_cost_microunits": 0,
        },
        state="queued" if pending else "completed",
        reason="" if pending else "No enrichment needed; proceed to final validation",
    )
    audit(
        actor,
        workspace_id,
        "enrichment.started",
        attempt.pk,
        {"trigger": trigger, "links": len(links)},
    )
    return attempt


def permitted_source(actor, system, url):
    for source in sources_for(actor, system.workspace_id).filter(state="active"):
        if not system_can_read(system, source):
            continue
        version = source.versions.get(version=source.config_version)
        allowed, _reason = scope_decision(source.url, url, version.config)
        if allowed:
            return source, version
    raise PermissionDenied("No active permitted source scope covers this link.")


@transaction.atomic
def enrichment_step(actor, workspace_id, attempt_id):
    # Global mutation lock order: workspace, then system/attempt/jobs.
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    attempt = get_object_or_404(
        EnrichmentAttempt.objects.select_for_update().select_related(
            "policy__system", "record"
        ),
        pk=attempt_id,
        workspace_id=workspace_id,
    )
    system = owned_system(actor, workspace_id, attempt.policy.system_id)
    if not records_for(actor, workspace_id).filter(
        pk=attempt.record_id
    ).exists() or not system_can_read(system, attempt.record.source):
        raise PermissionDenied("Parent evidence access was revoked.")
    if attempt.actor_id != actor.pk:
        raise PermissionDenied("Attempt owner required.")
    if attempt.state in {"completed", "cap_reached", "superseded"}:
        return attempt
    config = attempt.policy.config
    active = WorkItem.objects.filter(
        request_key=f"enrichment:{attempt.pk}:{len(attempt.visited)}",
        workspace_id=workspace_id,
    ).first()
    if active:
        active.refresh_from_db()
        if active.state in {"queued", "running", "retry", "cancel_requested"}:
            return attempt
        if not sources_for(actor, workspace_id).filter(
            pk=active.source_id
        ).exists() or not system_can_read(system, active.source):
            attempt.state = "blocked"
            attempt.reason = "Evidence source access was revoked; retained captures cannot be consumed."
            attempt.save(update_fields=["state", "reason"])
            return attempt
        entry = attempt.pending.pop(0)
        attempt.visited.append(entry["url"])
        for key in ["bytes", "seconds"]:
            attempt.usage[key] += active.usage.get(key, 0)
        captures = list(active.capture_set.all())
        attempt.usage["pages"] += 1
        if not captures and active.state in {
            "action_required",
            "needs_access",
            "blocked",
            "cancelled",
            "partial",
        }:
            # A failed stream may have received bytes that were not retained. Reserve
            # its full remaining allocation instead of silently treating that as zero.
            charged = max(
                0, active.limits.get("bytes", 0) - active.usage.get("bytes", 0)
            )
            attempt.usage["bytes"] += charged
            attempt.coverage.append(
                {
                    "url": entry["url"],
                    "state": "uncertain_transfer",
                    "reserved_bytes": charged,
                    "reason": "Transfer bytes unavailable; full allocation charged conservatively, not asserted as retained bytes",
                }
            )
        attempt.coverage.append(
            {
                "url": entry["url"],
                "depth": entry["depth"],
                "parent": entry["parent"],
                "run": str(active.pk),
                "state": active.state,
                "reason": active.reason,
                "missing": active.checkpoint.get("skipped", []),
                "captures": [str(c.pk) for c in captures],
            }
        )
        for capture in captures:
            if capture.final_url and capture.final_url not in attempt.visited:
                attempt.visited.append(capture.final_url)
            if (
                capture.media_type in {"text/html", "application/xhtml+xml"}
                and entry["depth"] < config["depth"]
            ):
                soup = BeautifulSoup(bytes(capture.original), "html.parser")
                for anchor in soup.find_all("a", href=True)[:100]:
                    try:
                        target = canonical_url(
                            urljoin(capture.final_url or capture.url, anchor["href"]),
                            resolve=False,
                        )
                    except ValidationError:
                        continue
                    if (
                        target not in attempt.visited
                        and not any(p["url"] == target for p in attempt.pending)
                        and len(attempt.pending) < 100
                    ):
                        attempt.pending.append(
                            {
                                "url": target,
                                "depth": entry["depth"] + 1,
                                "parent": str(capture.pk),
                            }
                        )
    attempt.pending = [
        entry for entry in attempt.pending if entry["url"] not in attempt.visited
    ]
    if not attempt.pending:
        attempt.state = "completed"
        attempt.reason = (
            "Coverage complete; unresolved fields remain explicit for final validation"
        )
    else:
        exceeded = next(
            (
                key
                for key in ["pages", "seconds", "bytes"]
                if attempt.usage[key] >= config[key]
            ),
            None,
        )
        if exceeded:
            attempt.state = "cap_reached"
            attempt.reason = f"{exceeded} cap {config[exceeded]} reached; consumed {attempt.usage[exceeded]}"
        else:
            entry = attempt.pending[0]
            try:
                if urlsplit(entry["url"]).hostname not in config["domains"]:
                    raise PermissionDenied(
                        "Domain is outside pinned enrichment policy."
                    )
                source, version = permitted_source(actor, system, entry["url"])
                from .access import headers_for

                headers_for(source, version)
                limits = validated_limits(
                    {
                        "limits": {
                            "pages": 1,
                            "depth": 0,
                            "seconds": max(
                                1, int(config["seconds"] - attempt.usage["seconds"])
                            ),
                            "bytes": config["bytes"] - attempt.usage["bytes"],
                            "attempts": 1,
                        }
                    }
                )
                WorkItem.objects.create(
                    workspace_id=workspace_id,
                    actor=actor,
                    source=source,
                    source_version=version,
                    system=system,
                    request_key=f"enrichment:{attempt.pk}:{len(attempt.visited)}",
                    limits=limits,
                    checkpoint={
                        "pending": [{"url": entry["url"], "depth": 0}],
                        "visited": [],
                        "skipped": [],
                        "pages": 0,
                    },
                    usage={"bytes": 0, "provider_cost_microunits": 0},
                )
                attempt.state = "running"
            except (PermissionDenied, ValidationError, NeedsAccess) as error:
                attempt.pending.pop(0)
                attempt.visited.append(entry["url"])
                attempt.coverage.append(
                    {**entry, "state": "blocked", "reason": str(error)[:200]}
                )
    attempt.save(
        update_fields=["pending", "visited", "coverage", "usage", "state", "reason"]
    )
    return attempt


@transaction.atomic
def resume_enrichment(actor, workspace_id, attempt_id, reason):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    previous = get_object_or_404(
        EnrichmentAttempt.objects.select_for_update(),
        pk=attempt_id,
        workspace_id=workspace_id,
    )
    system = owned_system(actor, workspace_id, previous.policy.system_id)
    if (
        previous.state != "cap_reached"
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 500
    ):
        raise ValidationError(
            "Only a capped attempt can resume with a documented reason."
        )
    policy = system.enrichment_policies.order_by("-number").first()
    if (
        not policy
        or any(
            policy.config[k] < previous.usage[k] for k in ["pages", "seconds", "bytes"]
        )
        or not any(
            policy.config[k] > previous.policy.config[k]
            for k in ["pages", "seconds", "bytes"]
        )
    ):
        raise ValidationError("Publish an increased permitted cap before resuming.")
    attempt = start_enrichment(
        actor,
        workspace_id,
        system.pk,
        previous.record_id,
        reason,
        [p["url"] for p in previous.pending],
        previous.unresolved_fields,
    )
    attempt.policy = policy
    attempt.previous = previous
    attempt.pending = previous.pending
    attempt.visited = previous.visited
    attempt.coverage = previous.coverage
    attempt.usage = previous.usage
    attempt.save()
    previous.state = "superseded"
    previous.save(update_fields=["state"])
    audit(
        actor,
        workspace_id,
        "enrichment.resumed",
        attempt.pk,
        {"previous": str(previous.pk), "reason": reason},
    )
    return attempt
