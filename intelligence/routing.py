"""Independent, idempotent per-system prototype processing and scoped review."""

from copy import deepcopy

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Exists, F, OuterRef
from django.http import Http404
from django.shortcuts import get_object_or_404

from workspaces.models import Workspace

from .content import extract_fields
from .models import (
    ContentRecord,
    ContentSchema,
    Destination,
    ReviewChange,
    SchemaVersion,
    SystemResult,
    WorkItem,
)
from .permissions import member, records_for, sources_for, system_can_read, systems_for
from .qualification import evaluate_rules, valid_value, validate_rules
from .services import audit
from .systems import (
    current_config,
    owned_system,
    relevance_decision,
    save_configuration,
)


@transaction.atomic
def configure_qualification(
    actor,
    workspace_id,
    system_id,
    expected_version,
    schema_id,
    rules,
    destination_id=None,
):
    system = owned_system(actor, workspace_id, system_id)
    schema = get_object_or_404(
        ContentSchema,
        pk=schema_id,
        workspace_id=workspace_id,
        system=system,
        scope="system",
        state="published",
        published_version__isnull=False,
    )
    definition = schema.versions.get(number=schema.published_version)
    validate_rules(rules, definition.fields)
    config = current_config(system)
    config.update(
        schema_id=str(schema.pk), schema_version=definition.number, qualification=rules
    )
    if destination_id:
        destination = get_object_or_404(
            Destination,
            pk=destination_id,
            workspace_id=workspace_id,
            system=system,
            state="test_passed",
        )
        config["destination_id"] = str(destination.pk)
    return save_configuration(actor, workspace_id, system.pk, expected_version, config)


def live_access(actor, result, review=False, allow_inactive=False):
    membership = member(actor, result.workspace_id)
    if review and membership.role not in {"administrator", "operator", "reviewer"}:
        raise PermissionDenied("Scoped review authority required.")
    if (
        not systems_for(actor, result.workspace_id).filter(pk=result.system_id).exists()
        or not records_for(actor, result.workspace_id)
        .filter(pk=result.record_id)
        .exists()
        or not (
            result.record.source.systemsourcegrant_set.filter(
                system=result.system, active=True
            ).exists()
            if allow_inactive
            else system_can_read(result.system, result.record.source)
        )
    ):
        raise PermissionDenied(
            "Current system/source access does not permit this result."
        )
    from .models import Capture

    capture_ids = {
        candidate["capture"]
        for field in result.fields.values()
        for candidate in field.get("candidates", [])
        if candidate.get("capture")
    }
    for capture_id in capture_ids:
        capture = (
            Capture.objects.filter(
                pk=capture_id,
                workspace_id=result.workspace_id,
                source__in=sources_for(actor, result.workspace_id),
            )
            .select_related("source")
            .first()
        )
        if (
            not capture
            or not capture.source.systemsourcegrant_set.filter(
                system=result.system, active=True
            ).exists()
        ):
            raise PermissionDenied("Enriched evidence access is no longer permitted.")
    if (
        result.content_version.record_id != result.record_id
        or result.configuration.system_id != result.system_id
        or result.analysis
        and result.analysis.content_version_id != result.content_version_id
    ):
        raise PermissionDenied("Result lineage is inconsistent.")


def result_is_current(result):
    record = ContentRecord.objects.get(pk=result.record_id)
    return (
        record.version == result.content_version.number
        and record.current_analysis_id == result.analysis_id
        and record.classification_version == result.classification_version
    )


def qualify_result(result):
    if (result.analysis or result.content_version).status not in {"parsed", "partial"}:
        return {
            "decision": "blocked",
            "reason": "Immutable base source security/parsing gate failed",
            "rules": [],
        }
    config = result.configuration.config
    if not result.schema_version or not config.get("qualification"):
        return {
            "decision": "review",
            "reason": "Missing published schema or qualification configuration",
            "rules": [],
        }
    outcome = evaluate_rules(
        result.fields,
        result.schema_version.fields,
        config["qualification"],
        result.configuration.number,
    )
    missing = [
        field["key"]
        for field in result.schema_version.fields
        if field["required"]
        and result.fields.get(field["key"], {}).get("status") != "known"
    ]
    if missing and outcome["decision"] != "fail":
        outcome.update(
            decision=config["qualification"]["unknown"],
            reason="Required facts incomplete: " + ", ".join(missing),
        )
    return outcome


@transaction.atomic
def route_one(actor, workspace_id, record_id, system_id, replay_run=None):
    system = owned_system(actor, workspace_id, system_id)
    record = get_object_or_404(
        records_for(actor, workspace_id).select_for_update(), pk=record_id
    )
    if system.state in {"paused", "retired"} or not system_can_read(
        system, record.source
    ):
        raise PermissionDenied(
            "System routing is paused, retired or lacks source access."
        )
    if replay_run is not None:
        from .models import ReplayRun

        replay_run = get_object_or_404(
            ReplayRun,
            pk=replay_run.pk,
            workspace_id=workspace_id,
            system=system,
            state="running",
        )
        if not any(pin["record"] == str(record.pk) for pin in replay_run.pins):
            raise PermissionDenied("Record is outside the pinned replay.")
    bundle = (
        replay_run.bundle
        if replay_run
        else system.active_bundle
        if system.state == "active"
        else None
    )
    config_row = (
        bundle.configuration
        if bundle
        else system.configurations.filter(number=system.config_version).first()
    )
    if not config_row:
        raise ValidationError("Save a system configuration first.")
    config = config_row.config
    if str(record.source_id) not in config.get("source_ids", []):
        raise PermissionDenied("Source is not selected in this system configuration.")
    revision = record.revisions.order_by("-number").first()
    if not revision:
        raise ValidationError("A source content version is required.")
    analysis = record.current_analysis
    processing_number = analysis.number if analysis else 0
    existing = SystemResult.objects.filter(
        system=system,
        content_version=revision,
        configuration=config_row,
        processing_number=processing_number,
        classification_version=record.classification_version,
        bundle_number=bundle.number if bundle else 0,
        replay_number=replay_run.number if replay_run else 0,
    ).first()
    if existing:
        return existing, False
    analysis = record.current_analysis
    types = set(record.type_assignments.values_list("key", flat=True))
    text = (analysis or revision).body
    relevance = (
        relevance_decision(text, config["relevance"])
        if config.get("relevance")
        else {"decision": "review", "method": "missing_relevance", "evidence": []}
    )
    if types and not types.intersection(config.get("type_keys", [])):
        relevance.update(decision="exclude", reason="No selected content type")
    elif not types and config.get("unknown_type", "review") != "include":
        relevance.update(
            decision=config.get("unknown_type", "review"),
            reason="Explicit Unknown type policy",
        )
    relevance.update(
        content_version=str(revision.pk),
        analysis=str(analysis.pk) if analysis else None,
        configuration_version=config_row.number,
        classification_version=record.classification_version,
        criteria=deepcopy(config.get("relevance", {})),
        selected_type_keys=config.get("type_keys", []),
        unknown_type_policy=config.get("unknown_type", "review"),
        segment_ids=list(range(len((analysis or revision).segments))),
    )
    schema_version = None
    if config.get("schema_id"):
        schema_version = get_object_or_404(
            SchemaVersion,
            schema_id=config["schema_id"],
            number=config["schema_version"],
            schema__system=system,
            schema__workspace_id=workspace_id,
        )
    extracted = (
        extract_fields(analysis or revision, schema_version.fields)
        if schema_version
        else {}
    )
    if schema_version:
        from .models import ExtractionJob

        definitions = {f["key"]: f for f in schema_version.fields}
        shared_jobs = (
            ExtractionJob.objects.filter(
                workspace_id=workspace_id,
                record=record,
                content_version=revision,
                analysis=analysis,
                classification_version=record.classification_version,
                schema_version__schema__scope__in=["common", "type"],
                schema_version__schema__workspace_id=workspace_id,
                state="completed",
                preview=False,
            )
            .select_related("schema_version")
            .order_by("-created_at")[:100]
        )
        reused = set()
        for shared in shared_jobs:
            shared_definitions = {f["key"]: f for f in shared.schema_version.fields}
            for key, definition in definitions.items():
                original = shared_definitions.get(key)
                fact = shared.result.get(key, {})
                if (
                    key in reused
                    or not original
                    or any(
                        original.get(k) != definition.get(k)
                        for k in ["type", "unit", "instruction"]
                    )
                    or fact.get("status") != "known"
                    or not valid_value(fact.get("value"), definition)
                    or fact.get("unit", "") != definition.get("unit", "")
                ):
                    continue
                extracted[key] = {
                    **deepcopy(fact),
                    "reused_from": {
                        "job": str(shared.pk),
                        "schema_version": shared.schema_version_id,
                        "content_version": str(revision.pk),
                        "analysis": str(analysis.pk) if analysis else None,
                    },
                }
                reused.add(key)
    result = SystemResult.objects.create(
        workspace_id=workspace_id,
        system=system,
        record=record,
        content_version=revision,
        analysis=analysis,
        configuration=config_row,
        bundle=bundle,
        bundle_number=bundle.number if bundle else 0,
        preview=bundle is None or replay_run is not None,
        replay_run=replay_run,
        replay_number=replay_run.number if replay_run else 0,
        schema_version=schema_version,
        classification_version=record.classification_version,
        processing_number=processing_number,
        relevance=relevance,
        fields=extracted,
    )
    if (analysis or revision).status not in {"parsed", "partial"}:
        result.decision, result.reason = (
            "blocked",
            "Source parsing/security validation unavailable",
        )
    elif relevance["decision"] == "exclude":
        result.decision, result.reason = (
            "excluded",
            "Relevance excluded; authorised review may overturn",
        )
    elif relevance["decision"] == "review":
        result.decision, result.reason = "review", "Relevance uncertain"
    else:
        result.qualification = qualify_result(result)
        result.decision = result.qualification["decision"]
        result.reason = result.qualification["reason"]
    result.save()
    audit(
        actor,
        workspace_id,
        "system.preview_processed",
        result.pk,
        {
            "system": str(system.pk),
            "configuration": config_row.number,
            "decision": result.decision,
        },
    )
    return result, True


def results_for(actor, workspace_id):
    from .models import SystemSourceGrant

    granted = SystemSourceGrant.objects.filter(
        system_id=OuterRef("system_id"),
        source_id=OuterRef("record__source_id"),
        active=True,
    )
    return SystemResult.objects.filter(
        workspace_id=workspace_id,
        system__in=systems_for(actor, workspace_id),
        record__in=records_for(actor, workspace_id),
    ).filter(Exists(granted))


def route_independently(actor, workspace_id, record_id, system_ids):
    if not isinstance(system_ids, list) or len(system_ids) > 50:
        raise ValidationError("Choose at most50 systems.")
    outcomes = []
    for system_id in dict.fromkeys(system_ids):
        try:
            result, created = route_one(actor, workspace_id, record_id, system_id)
            outcomes.append(
                {
                    "system": str(system_id),
                    "result": str(result.pk),
                    "decision": result.decision,
                    "created": created,
                }
            )
        except (PermissionDenied, ValidationError, Http404, DatabaseError):
            outcomes.append(
                {
                    "system": str(system_id),
                    "error": "This system could not process the item; inspect current access/configuration.",
                }
            )
    return outcomes


@transaction.atomic
def claim_review(actor, workspace_id, result_id, expected_version):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    result = get_object_or_404(
        results_for(actor, workspace_id).select_for_update(), pk=result_id
    )
    live_access(actor, result, review=True)
    if result.review_state == "resolved":
        raise ValidationError(
            "Review is already resolved; inspect its immutable completion event."
        )
    if result.version != expected_version or result.assigned_to_id not in {
        None,
        actor.pk,
    }:
        raise ValidationError(
            "Review changed or is claimed by another reviewer; reload current assignment."
        )
    if not result_is_current(result):
        raise ValidationError(
            "Source content superseded this review; process the current version."
        )
    result.assigned_to = actor
    result.version += 1
    result.save(update_fields=["assigned_to", "version"])
    audit(actor, workspace_id, "review.claimed", result.pk, {"version": result.version})
    return result


@transaction.atomic
def correct_field(
    actor, workspace_id, result_id, expected_version, key, value, segment, reason
):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    result = get_object_or_404(
        results_for(actor, workspace_id).select_for_update(), pk=result_id
    )
    live_access(actor, result, review=True)
    if (
        result.review_state != "open"
        or result.version != expected_version
        or result.assigned_to_id != actor.pk
        or not result_is_current(result)
    ):
        raise ValidationError(
            "Claim the current, unchanged review before correcting a field."
        )
    if (
        result.decision == "blocked"
        or (result.analysis or result.content_version).status
        not in {"parsed", "partial"}
        or not result.schema_version
    ):
        raise ValidationError(
            "Security/schema failures cannot be corrected as business fields."
        )
    definition = next(
        (f for f in result.schema_version.fields if f["key"] == key), None
    )
    evidence_source = result.analysis or result.content_version
    if (
        not definition
        or not valid_value(value, definition)
        or type(segment) is not int
        or not 0 <= segment < len(evidence_source.segments)
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 1000
    ):
        raise ValidationError(
            "Valid typed value, original evidence segment and reason are required."
        )
    old = deepcopy(result.fields.get(key, {}))
    evidence = {
        "content_version": str(result.content_version_id),
        "analysis": str(result.analysis_id) if result.analysis_id else None,
        "segment": segment,
    }
    result.fields[key] = {
        "status": "unknown" if value is None else "known",
        "value": value,
        "unit": definition.get("unit", ""),
        "candidates": [
            {
                "value": value,
                **evidence,
                "method": "human_correction",
                "actor": str(actor.pk),
            }
        ],
    }
    result.qualification = qualify_result(result)
    result.decision = (
        result.qualification["decision"]
        if result.relevance["decision"] == "include"
        else "review"
    )
    result.reason = result.qualification["reason"]
    result.version += 1
    result.save()
    ReviewChange.objects.create(
        workspace_id=workspace_id,
        result=result,
        actor=actor,
        kind="field",
        original={key: old},
        replacement={key: result.fields[key]},
        evidence=evidence,
        reason=reason,
        result_version=result.version,
    )
    audit(
        actor,
        workspace_id,
        "review.field_corrected",
        result.pk,
        {"field": key, "version": result.version},
    )
    return result


@transaction.atomic
def override_decision(
    actor, workspace_id, result_id, expected_version, decision, reason
):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    result = get_object_or_404(
        results_for(actor, workspace_id).select_for_update(), pk=result_id
    )
    live_access(actor, result, review=True)
    if (
        result.review_state != "open"
        or result.version != expected_version
        or result.assigned_to_id != actor.pk
        or not result_is_current(result)
    ):
        raise ValidationError("Claim the current review before overriding.")
    if (
        result.decision == "blocked"
        or (result.analysis or result.content_version).status
        not in {"parsed", "partial"}
        or decision not in {"include", "exclude", "pass", "fail", "review"}
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 1000
    ):
        raise ValidationError(
            "Business override requires a valid decision/reason and cannot bypass security."
        )
    original = {
        "decision": result.decision,
        "relevance": deepcopy(result.relevance),
        "qualification": deepcopy(result.qualification),
    }
    if decision in {"include", "exclude"}:
        result.relevance = {
            **result.relevance,
            "decision": decision,
            "override_reason": reason,
        }
        result.qualification = qualify_result(result) if decision == "include" else {}
        result.decision = (
            result.qualification["decision"] if decision == "include" else "excluded"
        )
    else:
        result.decision = decision
    if result.decision == "pass":
        destination_id = result.configuration.config.get("destination_id")
        if (
            not destination_id
            or not Destination.objects.filter(
                pk=destination_id,
                system=result.system,
                workspace_id=workspace_id,
                state="test_passed",
            ).exists()
        ):
            raise ValidationError("Destination validation failure cannot be bypassed.")
        if not result.schema_version or any(
            f["required"] and result.fields.get(f["key"], {}).get("status") != "known"
            for f in result.schema_version.fields
        ):
            raise ValidationError(
                "Required missing facts cannot be bypassed; correct evidence first."
            )
    result.reason = reason
    result.version += 1
    result.save()
    ReviewChange.objects.create(
        workspace_id=workspace_id,
        result=result,
        actor=actor,
        kind="override",
        original=original,
        replacement={
            "decision": result.decision,
            "relevance": result.relevance,
            "qualification": result.qualification,
        },
        reason=reason,
        result_version=result.version,
    )
    audit(
        actor,
        workspace_id,
        "review.decision_overridden",
        result.pk,
        {"system": str(result.system_id), "version": result.version},
    )
    return result


@transaction.atomic
def set_system_state(actor, workspace_id, system_id, state):
    system = owned_system(actor, workspace_id, system_id)
    if state not in {"paused", "retired", "draft"} or system.state == "retired":
        raise ValidationError("Choose pause, retire or resume a non-retired system.")
    if state == "draft" and system.state != "paused":
        raise ValidationError("Only a paused system can resume into a draft preview.")
    system.state = state
    system.save(update_fields=["state"])
    if state in {"paused", "retired"}:
        WorkItem.objects.filter(
            system=system, state__in=["queued", "running", "retry"]
        ).update(state="blocked", reason=f"System {state}")
    audit(actor, workspace_id, "system.state_changed", system.pk, {"state": state})
    return system


def backlog_preview(actor, system):
    config = current_config(system)
    return (
        records_for(actor, system.workspace_id)
        .filter(source_id__in=config.get("source_ids", []))
        .exclude(
            pk__in=SystemResult.objects.filter(
                system=system,
                configuration__number=system.config_version,
                content_version__number=F("record__version"),
                analysis_id=F("record__current_analysis_id"),
                classification_version=F("record__classification_version"),
            ).values("record_id")
        )
        .order_by("pk")[:100]
    )


def permitted_result_ids(actor, workspace_id):
    permitted = set()
    for result in results_for(actor, workspace_id).iterator(chunk_size=100):
        try:
            live_access(actor, result, allow_inactive=True)
        except PermissionDenied:
            continue
        permitted.add(str(result.pk))
    return permitted
