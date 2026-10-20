"""Bounded owner-requested replay with pinned versions and no automatic delivery."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import PublishedBundle, ReplayRun
from .permissions import records_for, system_can_read
from .routing import live_access, result_is_current, route_one
from .services import audit
from .systems import owned_system


def record_pin(record):
    return {
        "record": str(record.pk),
        "version": record.version,
        "analysis": str(record.current_analysis_id),
        "classification": record.classification_version,
    }


@transaction.atomic
def preview_replay(actor, workspace_id, system_id, bundle_id, record_ids):
    system = owned_system(actor, workspace_id, system_id)
    bundle = get_object_or_404(
        PublishedBundle, pk=bundle_id, workspace_id=workspace_id, system=system
    )
    if (
        not isinstance(record_ids, list)
        or not 1 <= len(record_ids) <= 100
        or len(set(record_ids)) != len(record_ids)
    ):
        raise ValidationError("Select 1–100 distinct authorised records.")
    pins, stale = [], []
    for record_id in record_ids:
        record = get_object_or_404(records_for(actor, workspace_id), pk=record_id)
        if str(record.source_id) not in bundle.configuration.config.get(
            "source_ids", []
        ) or not system_can_read(system, record.source):
            raise PermissionDenied("Replay source/system access is unavailable.")
        if not record.revisions.exists():
            raise ValidationError("Replay requires retained source content.")
        pins.append(record_pin(record))
        if (
            system.results.filter(record=record)
            .exclude(
                content_version__number=record.version,
                analysis_id=record.current_analysis_id,
                classification_version=record.classification_version,
            )
            .exists()
        ):
            stale.append(str(record.pk))
    latest = system.replays.order_by("number").last()
    run = ReplayRun.objects.create(
        workspace_id=workspace_id,
        system=system,
        bundle=bundle,
        actor=actor,
        number=latest.number + 1 if latest else 1,
        pins=pins,
        report={
            "eligible_count": len(pins),
            "stale_evidence_records": stale,
            "target_bundle": str(bundle.pk),
            "target_configuration": bundle.configuration.number,
            "target_schema": bundle.mapping.schema_version_id,
            "estimated_processing_units": len(pins),
            "estimated_provider_cost": "unavailable; no model/provider invoked",
            "delivery": "off",
        },
    )
    audit(
        actor,
        workspace_id,
        "replay.previewed",
        run.pk,
        {"count": len(pins), "bundle": str(bundle.pk), "delivery": False},
    )
    return run


@transaction.atomic
def execute_replay(actor, workspace_id, run_id):
    run = get_object_or_404(ReplayRun, pk=run_id, workspace_id=workspace_id)
    owned_system(actor, workspace_id, run.system_id)
    run = ReplayRun.objects.select_for_update().get(pk=run.pk)
    if run.state != "previewed":
        raise ValidationError(
            "Replay was already started; inspect its existing results."
        )
    run.state = "running"
    run.save(update_fields=["state"])
    outcomes = []
    for pin in run.pins:
        try:
            with transaction.atomic():
                record = get_object_or_404(
                    records_for(actor, workspace_id).select_for_update(),
                    pk=pin["record"],
                )
                if record_pin(record) != pin:
                    raise ValidationError(
                        "Source/evidence version changed since preview."
                    )
                result, _ = route_one(
                    actor, workspace_id, record.pk, run.system_id, replay_run=run
                )
                outcomes.append(
                    {
                        "record": pin["record"],
                        "result": str(result.pk),
                        "state": result.decision,
                        "delivery": "off",
                    }
                )
        except (PermissionDenied, ValidationError, Http404):
            outcomes.append(
                {
                    "record": pin["record"],
                    "state": "held",
                    "reason": "Current permission or version validation failed",
                    "delivery": "off",
                }
            )
    run.report = {**run.report, "outcomes": outcomes}
    run.state = (
        "completed_with_holds"
        if any(row["state"] == "held" for row in outcomes)
        else "completed"
    )
    run.save(update_fields=["state", "report"])
    audit(
        actor,
        workspace_id,
        "replay.completed",
        run.pk,
        {"count": len(outcomes), "delivery": False},
    )
    return run


@transaction.atomic
def enable_replay_delivery(actor, workspace_id, run_id, result_ids, reason):
    from .delivery import validate_result

    run = get_object_or_404(ReplayRun, pk=run_id, workspace_id=workspace_id)
    owned_system(actor, workspace_id, run.system_id)
    if (
        run.state not in {"completed", "completed_with_holds"}
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 500
        or not isinstance(result_ids, list)
        or not result_ids
        or len(result_ids) > 100
    ):
        raise ValidationError(
            "Select a completed replay's explicitly reviewed result set and reason."
        )
    results = []
    for result_id in dict.fromkeys(result_ids):
        result = get_object_or_404(run.results.select_for_update(), pk=result_id)
        live_access(actor, result)
        if result.review_state != "resolved" or not result_is_current(result):
            raise ValidationError(
                "Every enabled result must have a current completed review."
            )
        result.preview = False
        validate_result(actor, result, result.bundle)
        results.append(result)
    for result in results:
        result.save(update_fields=["preview"])
    run.enabled_results = sorted(
        set(run.enabled_results) | {str(result.pk) for result in results}
    )
    run.reason = reason
    run.save(update_fields=["enabled_results", "reason"])
    audit(
        actor,
        workspace_id,
        "replay.delivery_enabled",
        run.pk,
        {"count": len(results), "reason": reason},
    )
    return run
