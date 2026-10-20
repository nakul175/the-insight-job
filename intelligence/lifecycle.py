"""Explicit prototype content deletion with tombstones and truthful residuals."""

import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from workspaces.models import Workspace

from . import models as m
from .delivery import digest
from .permissions import member
from .services import audit


def url_digest(url):
    return hashlib.sha256(url.encode()).hexdigest()


def ensure_not_deleted(source_id, url, final_url=None):
    hashes = [url_digest(value) for value in [url, final_url] if value]
    if m.ContentTombstone.objects.filter(
        source_id=source_id, url_hash__in=hashes
    ).exists():
        raise ValidationError(
            "Deleted content is tombstoned; collection cannot recreate it."
        )


def inventory(record):
    captures = list(
        m.Capture.objects.filter(
            Q(record=record) | Q(content_version__record=record)
        ).values_list("pk", flat=True)
    )
    versions = list(record.revisions.values_list("pk", flat=True))
    capture_ids, version_ids = set(map(str, captures)), set(map(str, versions))
    results = []
    for result in m.SystemResult.objects.filter(workspace_id=record.workspace_id):
        affected = result.record_id == record.pk or any(
            str(c.get("capture")) in capture_ids
            or str(c.get("content_version")) in version_ids
            or str(c.get("evidence_content_version")) in version_ids
            for fact in result.fields.values()
            for c in fact.get("candidates", [])
        )
        if affected:
            results.append(str(result.pk))
    for change in m.ReviewChange.objects.filter(workspace_id=record.workspace_id):
        if str(change.result_id) not in results and any(
            identifier
            in json.dumps([change.original, change.replacement, change.evidence])
            for identifier in capture_ids | version_ids
        ):
            results.append(str(change.result_id))
    for event in m.EnrichedFieldEvent.objects.filter(workspace_id=record.workspace_id):
        if str(event.result_id) not in results and any(
            identifier in json.dumps([event.original, event.merged])
            for identifier in capture_ids | version_ids
        ):
            results.append(str(event.result_id))
    # Snapshots retain provenance even if a subsequent correction removed it from live fields.
    intents = []
    for intent in m.DeliveryIntent.objects.filter(workspace_id=record.workspace_id):
        affected = str(intent.result_id) in results or any(
            str(c.get("capture")) in capture_ids
            or str(c.get("content_version")) in version_ids
            or str(c.get("evidence_content_version")) in version_ids
            for fact in intent.snapshot.get("fields", {}).values()
            if isinstance(fact, dict)
            for c in fact.get("candidates", [])
        )
        if affected:
            intents.append(str(intent.pk))
            if str(intent.result_id) not in results:
                results.append(str(intent.result_id))
    runs = set(
        m.Capture.objects.filter(pk__in=captures).values_list("run_id", flat=True)
    )
    attempts = []
    for attempt in m.EnrichmentAttempt.objects.filter(workspace_id=record.workspace_id):
        if attempt.record_id == record.pk or any(
            str(pk) in capture_ids
            for row in attempt.coverage
            for pk in row.get("captures", [])
        ):
            attempts.append(str(attempt.pk))
            runs.update(
                m.WorkItem.objects.filter(
                    workspace_id=record.workspace_id,
                    request_key__startswith=f"enrichment:{attempt.pk}:",
                ).values_list("pk", flat=True)
            )
    # Pending page checkpoints may hold this URL even before a capture exists.
    for run in m.WorkItem.objects.filter(
        workspace_id=record.workspace_id, source_id=record.source_id
    ):
        if record.canonical_url and record.canonical_url in json.dumps(run.checkpoint):
            runs.add(run.pk)
    affected_record_ids = {
        str(pk)
        for pk in m.SystemResult.objects.filter(pk__in=results).values_list(
            "record_id", flat=True
        )
    } | {str(record.pk)}
    identifiers = capture_ids | version_ids | set(results) | affected_record_ids
    drafts = []
    for draft in m.UserDraft.objects.filter(workspace_id=record.workspace_id):
        if (
            draft.scope_kind == "record"
            and str(draft.scope_id) in affected_record_ids
            or draft.scope_kind == "result"
            and str(draft.scope_id) in results
            or any(
                identifier in json.dumps(draft.payload) for identifier in identifiers
            )
        ):
            drafts.append(
                {
                    "id": str(draft.pk),
                    "version": draft.updated_at.isoformat(),
                    "digest": hashlib.sha256(
                        json.dumps(draft.payload, sort_keys=True).encode()
                    ).hexdigest(),
                }
            )
    samples = m.EvaluationSample.objects.filter(content_version_id__in=versions)
    return {
        "record": str(record.pk),
        "record_version": record.version,
        "local_recovery_snapshots": [
            (str(snapshot.pk), snapshot.expires_at.isoformat(), snapshot.digest)
            for snapshot in m.RecoverySnapshot.objects.filter(
                workspace_id=record.workspace_id, state="retained"
            ).order_by("pk")
        ],
        "unmapped_legacy_external_receipts": [
            str(pk)
            for pk in m.DeliveryReceipt.objects.filter(
                workspace_id=record.workspace_id, source=record.source
            )
            .order_by("pk")
            .values_list("pk", flat=True)
        ],
        "analysis": str(record.current_analysis_id),
        "drafts": sorted(drafts, key=lambda row: row["id"]),
        "intent_states": sorted(
            [
                (str(intent.pk), intent.state, intent.attempts)
                for intent in m.DeliveryIntent.objects.filter(pk__in=intents)
            ]
        ),
        "alias_hashes": sorted(
            {
                url_digest(url)
                for pair in m.Capture.objects.filter(pk__in=captures).values_list(
                    "url", "final_url"
                )
                for url in pair
                if url
            }
            | ({url_digest(record.canonical_url)} if record.canonical_url else set())
        ),
        "classification_version": record.classification_version,
        "captures": sorted(capture_ids),
        "versions": sorted(version_ids),
        "results": sorted(results),
        "intents": sorted(intents),
        "runs": sorted(map(str, runs)),
        "enrichment_attempts": sorted(attempts),
        "evaluation_versions": sorted(
            set(samples.values_list("version_id", flat=True))
        ),
        "derivatives": record.derivative_set.count(),
        "extraction_jobs": m.ExtractionJob.objects.filter(record=record).count(),
        "indexes": "Database content/title/type/identity indexes and retained URL lookup",
        "external_copies": "Local sandbox intents include acknowledged and uncertain writes; unsupported withdrawal needs manual follow-up",
        "exports": "Previously downloaded files are outside application control; residual copies unknown",
    }


def fingerprint(impact):
    return hashlib.sha256(json.dumps(impact, sort_keys=True).encode()).hexdigest()


@transaction.atomic
def preview_deletion(
    actor, workspace_id, record_id, backup_scope="unknown", backup_expires_at=None
):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    member(actor, workspace_id, admin=True)
    record = get_object_or_404(
        m.ContentRecord.objects.select_for_update(),
        pk=record_id,
        workspace_id=workspace_id,
    )
    if record.status == "deleted":
        raise ValidationError(
            "Content was already deleted; inspect its residual status."
        )
    if (
        backup_scope not in {"unknown", "none", "until"}
        or backup_scope == "until"
        and (
            backup_expires_at is None
            or timezone.is_naive(backup_expires_at)
            or backup_expires_at <= timezone.now()
        )
    ):
        raise ValidationError(
            "Declare unknown/none/until backup scope; until needs a future timezone-aware expiry."
        )
    impact = inventory(record)
    operation = m.DeletionOperation.objects.create(
        workspace_id=workspace_id,
        actor=actor,
        record=record,
        fingerprint=fingerprint(impact),
        impact=impact,
        backup_scope=backup_scope,
        backup_expires_at=backup_expires_at,
    )
    audit(
        actor,
        workspace_id,
        "content.deletion_previewed",
        operation.pk,
        {"count": len(impact["captures"])},
    )
    return operation


def queue_deleted_withdrawal(actor, original):
    if original.operation == "withdraw" or original.attempts == 0:
        return None
    supported = original.bundle.mapping.contract.supports_withdrawal
    intent, _ = m.DeliveryIntent.objects.get_or_create(
        idempotency_key=digest(original.idempotency_key, "withdraw"),
        defaults={
            "workspace_id": original.workspace_id,
            "result": original.result,
            "result_version": original.result_version,
            "bundle": original.bundle,
            "destination": original.destination,
            "actor": actor,
            "payload": {},
            "snapshot": {"withdraws": str(original.pk), "mode": "local_sandbox"},
            "external_key": original.external_key,
            "operation": "withdraw",
            "state": "pending" if supported else "manual_follow_up",
            "reason": "Content deleted: withdraw local copy"
            if supported
            else "No withdrawal support; residual local copy needs manual follow-up",
        },
    )
    return intent


@transaction.atomic
def execute_deletion(actor, workspace_id, operation_id):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    member(actor, workspace_id, admin=True)
    operation = get_object_or_404(
        m.DeletionOperation.objects.select_for_update(),
        pk=operation_id,
        workspace_id=workspace_id,
    )
    record = m.ContentRecord.objects.select_for_update().get(pk=operation.record_id)
    if (
        operation.state != "previewed"
        or fingerprint(inventory(record)) != operation.fingerprint
    ):
        raise ValidationError(
            "Deletion impact changed/already executed; refresh preview before deleting."
        )
    impact = operation.impact
    for alias_hash in impact["alias_hashes"]:
        m.ContentTombstone.objects.get_or_create(
            source_id=record.source_id,
            url_hash=alias_hash,
            defaults={"workspace_id": workspace_id, "record": record},
        )
    withdrawals = []
    for original in m.DeliveryIntent.objects.filter(pk__in=impact["intents"]):
        withdrawal = queue_deleted_withdrawal(actor, original)
        if withdrawal:
            withdrawals.append(str(withdrawal.pk))
    for receipt_id in impact["unmapped_legacy_external_receipts"]:
        m.Withdrawal.objects.get_or_create(
            receipt_id=receipt_id,
            defaults={
                "workspace_id": workspace_id,
                "state": "manual_follow_up",
                "reason": "Content deleted; legacy receipt lacks record identity, verify residual copy manually",
            },
        )
    # Scrub accessible content, while retaining non-content identifiers needed for withdrawal/audit.
    m.ContentRecord.objects.filter(pk=record.pk).update(
        status="deleted",
        title="",
        body="",
        canonical_url="",
        identity=None,
        current_analysis=None,
    )
    m.Capture.objects.filter(pk__in=impact["captures"]).update(
        original=b"", url="", final_url="", missing=[], status="deleted", digest=""
    )
    m.ParseResult.objects.filter(capture_id__in=impact["captures"]).update(
        segments=[], reason="", status="deleted"
    )
    m.ContentAnalysis.objects.filter(content_version_id__in=impact["versions"]).update(
        body="", segments=[], summary={}, reason="", status="deleted"
    )
    m.ContentVersion.objects.filter(pk__in=impact["versions"]).update(
        body="",
        segments=[],
        metadata={},
        summary={},
        changes={},
        digest="",
        completeness="deleted",
    )
    m.Derivative.objects.filter(record=record).delete()
    m.ContentType.objects.filter(record=record).delete()
    m.ClassificationChange.objects.filter(record=record).update(
        original=[], selected=[], reason=""
    )
    m.DuplicateDecision.objects.filter(Q(left=record) | Q(right=record)).update(
        reason=""
    )
    m.ExtractionJob.objects.filter(record=record).update(
        result={}, reason="Content deleted", state="blocked"
    )
    m.SystemResult.objects.filter(pk__in=impact["results"]).update(
        fields={},
        relevance={},
        qualification={},
        reason="Content deleted or evidence removed",
        decision="blocked",
        preview=True,
    )
    m.ReviewChange.objects.filter(result_id__in=impact["results"]).update(
        original={}, replacement={}, evidence={}, reason=""
    )
    m.EnrichedFieldEvent.objects.filter(result_id__in=impact["results"]).update(
        original={}, merged={}
    )
    m.ReviewTask.objects.filter(result_id__in=impact["results"]).update(
        state="withdrawn"
    )
    m.UserDraft.objects.filter(pk__in=[row["id"] for row in impact["drafts"]]).delete()
    m.EnrichmentAttempt.objects.filter(pk__in=impact["enrichment_attempts"]).update(
        trigger="",
        unresolved_fields=[],
        pending=[],
        visited=[],
        coverage=[],
        reason="Content deleted",
        state="blocked",
    )
    m.WorkItem.objects.filter(pk__in=impact["runs"]).update(
        checkpoint={},
        result={},
        state="blocked",
        reason="Content deleted",
        lease_token=None,
        lease_until=None,
    )
    m.EvaluationSample.objects.filter(content_version_id__in=impact["versions"]).update(
        expected_fields={}, expected_routing="review", expected_qualification="review"
    )
    m.SchemaCheck.objects.filter(
        evaluation_version_id__in=impact["evaluation_versions"]
    ).update(passed=False, results=[])
    m.ConfigurationComparison.objects.filter(
        evaluation_version_id__in=impact["evaluation_versions"]
    ).update(passed=False, state="invalidated", results=[])
    m.DeliveryIntent.objects.filter(pk__in=impact["intents"]).exclude(
        operation="withdraw"
    ).update(
        payload={},
        snapshot={},
        state="blocked",
        reason="Content deleted; delivery blocked",
        response={},
    )
    # Capable receivers are withdrawn asynchronously. Incapable ones retain a declared residual copy.
    affected_objects = {
        impact["record"],
        *impact["captures"],
        *impact["versions"],
        *impact["results"],
        *impact["intents"],
        *impact["runs"],
        *impact["enrichment_attempts"],
    }
    for event in m.AuditEvent.objects.filter(workspace_id=workspace_id):
        if event.object_id in affected_objects or any(
            identifier in json.dumps(event.details) for identifier in affected_objects
        ):
            event.details = {"content_removed": True}
            event.save(update_fields=["details"])
    m.RecoverySnapshot.objects.filter(
        workspace_id=workspace_id, state="retained"
    ).update(payload=[], state="invalidated_by_deletion")
    operation.impact = {
        "application_content": "removed",
        "local_snapshots_invalidated": len(impact["local_recovery_snapshots"]),
        "unmapped_legacy_external_receipts": impact[
            "unmapped_legacy_external_receipts"
        ],
        "withdrawal_ids": withdrawals,
        "backup_scope": operation.backup_scope,
        "backup_expires_at": operation.backup_expires_at.isoformat()
        if operation.backup_expires_at
        else None,
        "downloaded_exports": "Outside application control; residual copies unknown",
    }
    operation.state, operation.executed_at = (
        "application_removed_residuals_pending",
        timezone.now(),
    )
    operation.save(update_fields=["impact", "state", "executed_at"])
    audit(
        actor,
        workspace_id,
        "content.deleted",
        operation.pk,
        {"count": len(impact["captures"]), "state": operation.state},
    )
    return operation
