"""Audited review follow-up and final requalification from permitted captures."""

import json
from copy import deepcopy
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from workspaces.models import Workspace

from .content import extract_fields
from .models import (
    Capture,
    EnrichedFieldEvent,
    EnrichmentAttempt,
    ParseResult,
    ReviewChange,
    ReviewTask,
)
from .permissions import sources_for, system_can_read
from .routing import live_access, qualify_result, result_is_current, results_for
from .services import audit


def current_review(
    actor, workspace_id, result_id, expected_version, allow_security_review=False
):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    result = get_object_or_404(
        results_for(actor, workspace_id).select_for_update(), pk=result_id
    )
    live_access(actor, result, review=True)
    if (
        result.version != expected_version
        or result.assigned_to_id != actor.pk
        or not result_is_current(result)
        or result.review_state == "resolved"
    ):
        raise ValidationError(
            "Review changed/resolved; compare the current revision and reload before saving."
        )
    if not allow_security_review and (
        result.analysis or result.content_version
    ).status not in {"parsed", "partial"}:
        raise ValidationError("Immutable base source security/parsing gate failed.")
    return result


@transaction.atomic
def follow_up(
    actor,
    workspace_id,
    result_id,
    expected_version,
    action,
    owner,
    next_review_at,
    reason,
):
    result = current_review(
        actor, workspace_id, result_id, expected_version, allow_security_review=True
    )
    if (
        action not in ("defer", "escalate")
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 1000
        or next_review_at is None
        or timezone.is_naive(next_review_at)
        or next_review_at <= timezone.now()
    ):
        raise ValidationError(
            "Defer/escalate requires a reason, permitted owner and future timezone-aware next-review date."
        )
    live_access(owner, result, review=True)
    before = {
        "review_state": result.review_state,
        "owner": str(result.assigned_to_id),
        "next_review": result.next_review_at.isoformat()
        if result.next_review_at
        else None,
    }
    result.review_state = "deferred" if action == "defer" else "escalated"
    result.assigned_to, result.next_review_at = owner, next_review_at
    result.version += 1
    result.save(
        update_fields=["review_state", "assigned_to", "next_review_at", "version"]
    )
    ReviewTask.objects.filter(result=result, state="open").update(state="superseded")
    ReviewTask.objects.create(
        workspace_id=workspace_id, result=result, recipient=owner, due_at=next_review_at
    )
    ReviewChange.objects.create(
        workspace_id=workspace_id,
        result=result,
        actor=actor,
        kind=action,
        original=before,
        replacement={
            "review_state": result.review_state,
            "owner": str(owner.pk),
            "next_review": next_review_at.isoformat(),
        },
        reason=reason,
        result_version=result.version,
    )
    audit(
        actor,
        workspace_id,
        "review.follow_up",
        result.pk,
        {"action": action, "owner": str(owner.pk), "version": result.version},
    )
    return result


@transaction.atomic
def reopen_review(actor, workspace_id, result_id, expected_version, reason):
    result = current_review(actor, workspace_id, result_id, expected_version)
    if (
        result.review_state not in {"deferred", "escalated"}
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 1000
    ):
        raise ValidationError(
            "Reopen a deferred/escalated review with an information-arrival reason."
        )
    result.qualification = qualify_result(result)
    result.decision = (
        result.qualification["decision"]
        if result.relevance.get("decision") == "include"
        else "review"
    )
    result.review_state, result.next_review_at = "open", None
    result.version += 1
    result.save()
    ReviewTask.objects.filter(result=result, state="open").update(
        state="returned_to_review"
    )
    ReviewChange.objects.create(
        workspace_id=workspace_id,
        result=result,
        actor=actor,
        kind="reopened",
        replacement={
            "decision": result.decision,
            "qualification": result.qualification,
        },
        reason=reason,
        result_version=result.version,
    )
    audit(
        actor, workspace_id, "review.reopened", result.pk, {"version": result.version}
    )
    return result


@transaction.atomic
def complete_review(actor, workspace_id, result_id, expected_version, reason):
    result = current_review(actor, workspace_id, result_id, expected_version)
    if (
        result.review_state in {"deferred", "escalated"}
        or result.decision not in {"pass", "fail", "excluded"}
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 1000
    ):
        raise ValidationError(
            "Resolve current completed qualification only, with a reason; unknown/security cases remain reviewable."
        )
    result.review_state = "resolved"
    result.version += 1
    result.save(update_fields=["review_state", "version"])
    ReviewTask.objects.filter(result=result, state="open").update(state="completed")
    ReviewChange.objects.create(
        workspace_id=workspace_id,
        result=result,
        actor=actor,
        kind="completed",
        replacement={"decision": result.decision, "review_state": "resolved"},
        reason=reason,
        result_version=result.version,
    )
    audit(
        actor,
        workspace_id,
        "review.completed",
        result.pk,
        {"version": result.version, "reason": reason},
    )
    return result


@transaction.atomic
def apply_enrichment(actor, workspace_id, result_id, expected_version, attempt_id):
    result = current_review(actor, workspace_id, result_id, expected_version)
    attempt = get_object_or_404(
        EnrichmentAttempt,
        pk=attempt_id,
        workspace_id=workspace_id,
        record=result.record,
        policy__system=result.system,
    )
    if (
        attempt.state not in {"completed", "cap_reached", "superseded"}
        or not result.schema_version
    ):
        raise ValidationError(
            "Complete or cap enrichment before final requalification."
        )
    if EnrichedFieldEvent.objects.filter(result=result, attempt=attempt).exists():
        return result, False
    ancestors = set()
    cursor = attempt
    while cursor and cursor.pk not in ancestors and len(ancestors) < 100:
        if (
            cursor.workspace_id != workspace_id
            or cursor.record_id != result.record_id
            or cursor.policy.system_id != result.system_id
        ):
            raise PermissionDenied("Enrichment attempt ancestry is inconsistent.")
        ancestors.add(cursor.pk)
        cursor = cursor.previous
    capture_ids = {pk for row in attempt.coverage for pk in row.get("captures", [])}
    permitted = sources_for(actor, workspace_id)
    captures = []
    for capture_id in capture_ids:
        capture = get_object_or_404(
            Capture.objects.select_related("source", "run"),
            pk=capture_id,
            workspace_id=workspace_id,
            source__workspace_id=workspace_id,
            run__system=result.system,
        )
        if (
            not permitted.filter(pk=capture.source_id).exists()
            or not system_can_read(result.system, capture.source)
            or not any(
                capture.run.request_key.startswith(f"enrichment:{pk}:")
                for pk in ancestors
            )
        ):
            raise PermissionDenied(
                "Enrichment evidence no longer has current source/system permission or provenance."
            )
        captures.append(capture)
    original = deepcopy(result.fields)
    for capture in captures:
        parsed = ParseResult.objects.filter(
            capture=capture, status__in=["parsed", "partial"]
        ).first()
        if not parsed:
            continue
        extracted = extract_fields(
            SimpleNamespace(segments=parsed.segments), result.schema_version.fields
        )
        for definition in result.schema_version.fields:
            key = definition["key"]
            old = result.fields.get(
                key,
                {
                    "status": "unknown",
                    "value": None,
                    "candidates": [],
                    "unit": definition.get("unit", ""),
                },
            )
            old_candidates = [
                {
                    **c,
                    "content_version": str(result.content_version_id),
                    "analysis": str(result.analysis_id) if result.analysis_id else None,
                }
                if not c.get("capture")
                else c
                for c in old.get("candidates", [])
            ]
            new_candidates = [
                {
                    **c,
                    "capture": str(capture.pk),
                    "source": str(capture.source_id),
                    "url": capture.url,
                    "evidence_content_version": str(capture.content_version_id)
                    if capture.content_version_id
                    else None,
                }
                for c in extracted[key]["candidates"]
            ]
            unique = {
                json.dumps(c, sort_keys=True): c
                for c in old_candidates + new_candidates
            }
            limited = (
                len(unique) > 100
                or old.get("candidate_limit_reached", False)
                or extracted[key].get("candidate_limit_reached", False)
            )
            candidates = list(unique.values())[:100]
            valid = [c for c in candidates if "value" in c and c["value"] is not None]
            values = {json.dumps(c["value"], sort_keys=True) for c in valid}
            status = (
                "known"
                if len(values) == 1 and len(valid) == len(candidates)
                else "conflict"
                if valid
                else "unknown"
            )
            if limited:
                status = "unknown"
            result.fields[key] = {
                "candidate_limit_reached": limited,
                "status": status,
                "value": valid[0]["value"] if status == "known" else None,
                "unit": definition.get("unit", ""),
                "candidates": candidates,
                "method": "permitted_enrichment_merge",
            }
    result.qualification = qualify_result(result)
    conflict = any(f.get("status") == "conflict" for f in result.fields.values())
    incomplete = any(
        f["required"] and result.fields.get(f["key"], {}).get("status") != "known"
        for f in result.schema_version.fields
    )
    if conflict or attempt.state in {"cap_reached", "superseded"} and incomplete:
        result.qualification.update(
            decision="review",
            reason="Contradictory evidence"
            if conflict
            else "Enrichment budget ended with mandatory fields incomplete",
        )
    result.decision = (
        result.qualification["decision"]
        if result.relevance.get("decision") == "include"
        else "review"
    )
    result.reason = result.qualification["reason"]
    result.review_state, result.next_review_at = "open", None
    result.version += 1
    result.save()
    EnrichedFieldEvent.objects.create(
        workspace_id=workspace_id,
        result=result,
        attempt=attempt,
        actor=actor,
        original=original,
        merged=deepcopy(result.fields),
        result_version=result.version,
    )
    audit(
        actor,
        workspace_id,
        "enrichment.final_requalification",
        result.pk,
        {
            "attempt": str(attempt.pk),
            "version": result.version,
            "decision": result.decision,
        },
    )
    return result, True


def evidence_for(actor, result):
    live_access(actor, result, allow_inactive=True)
    ids = {
        c["capture"]
        for field in result.fields.values()
        for c in field.get("candidates", [])
        if c.get("capture")
    }
    return [
        {
            "capture": capture,
            "segments": capture.parseresult.segments
            if hasattr(capture, "parseresult")
            else [],
        }
        for capture in Capture.objects.filter(
            pk__in=ids, workspace_id=result.workspace_id
        ).select_related("source", "parseresult")
    ]
