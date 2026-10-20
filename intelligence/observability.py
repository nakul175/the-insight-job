"""Scoped, content-free operational evidence and deduplicated in-app incidents."""

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from workspaces.models import Membership, Workspace

from .models import (
    AlertRule,
    AuditEvent,
    DeliveryIntent,
    Incident,
    RejectSampling,
)
from .permissions import member, records_for, runs_for, sources_for, systems_for
from .routing import live_access, permitted_result_ids, results_for
from .schema_lifecycle import save_samples
from .services import audit
from .systems import owned_system


def checked_interval(starts_at, ends_at):
    if (
        starts_at is None
        or ends_at is None
        or timezone.is_naive(starts_at)
        or timezone.is_naive(ends_at)
        or starts_at >= ends_at
    ):
        raise ValidationError("Declare an ordered timezone-aware interval.")


@transaction.atomic
def sample_rejects(
    actor, workspace_id, system_id, starts_at, ends_at, limit, threshold
):
    owned_system(actor, workspace_id, system_id)
    checked_interval(starts_at, ends_at)
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or type(threshold) is not int
        or not 0 <= threshold <= 100
    ):
        raise ValidationError(
            "Sampling limit must be 1–100; allowed false-exclusion count must be 0–100."
        )
    ids = []
    for result in (
        results_for(actor, workspace_id)
        .filter(
            system_id=system_id,
            decision__in=["excluded", "fail"],
            created_at__gte=starts_at,
            created_at__lt=ends_at,
        )
        .order_by("created_at", "pk")
        .iterator(chunk_size=100)
    ):
        try:
            live_access(actor, result)
        except PermissionDenied:
            continue
        ids.append(str(result.pk))
        if len(ids) == limit:
            break
    selection = RejectSampling.objects.create(
        workspace_id=workspace_id,
        system_id=system_id,
        actor=actor,
        starts_at=starts_at,
        ends_at=ends_at,
        threshold=threshold,
        result_ids=ids,
    )
    audit(
        actor,
        workspace_id,
        "rejects.sampled",
        selection.pk,
        {"count": len(ids), "system": str(system_id)},
    )
    return selection


@transaction.atomic
def relabel_reject(
    actor,
    workspace_id,
    sampling_id,
    result_id,
    fields,
    routing,
    qualification,
    sample_set_id=None,
    expected_version=0,
):
    selection = get_object_or_404(
        RejectSampling, pk=sampling_id, workspace_id=workspace_id
    )
    owned_system(actor, workspace_id, selection.system_id)
    selection = RejectSampling.objects.select_for_update().get(pk=selection.pk)
    if str(result_id) not in selection.result_ids or str(result_id) in selection.labels:
        raise ValidationError(
            "Choose an unrelabelled result in this pinned sampling selection."
        )
    result = get_object_or_404(
        results_for(actor, workspace_id), pk=result_id, system=selection.system
    )
    live_access(actor, result, review=True)
    if result.record.current_analysis_id != result.analysis_id:
        raise ValidationError(
            "Sample processing analysis changed; review the current result before relabelling."
        )
    if not result.schema_version:
        raise ValidationError("A system schema is required for labelled evaluation.")
    labels = []
    if sample_set_id:
        from .models import EvaluationSet
        from .schema_lifecycle import permitted_samples

        existing = get_object_or_404(
            EvaluationSet,
            pk=sample_set_id,
            workspace_id=workspace_id,
            schema_id=result.schema_version.schema_id,
        )
        if existing.version != expected_version:
            raise ValidationError(
                "Evaluation membership changed; reload before adding a label."
            )
        previous = existing.versions.get(number=expected_version)
        for sample in permitted_samples(actor, previous):
            if sample.content_version_id != result.content_version_id:
                labels.append(
                    {
                        "content_version": str(sample.content_version_id),
                        "analysis": str(sample.analysis_id)
                        if sample.analysis_id
                        else None,
                        "fields": sample.expected_fields,
                        "routing": sample.expected_routing,
                        "qualification": sample.expected_qualification,
                    }
                )
    labels.append(
        {
            "content_version": str(result.content_version_id),
            "analysis": str(result.analysis_id) if result.analysis_id else None,
            "fields": fields,
            "routing": routing,
            "qualification": qualification,
        }
    )
    version = save_samples(
        actor,
        workspace_id,
        result.schema_version.schema_id,
        "Reviewed rejects",
        labels,
        sample_set_id,
        expected_version,
    )
    false_exclusion = (
        result.relevance.get("decision") == "exclude" and routing == "include"
    )
    selection.labels[str(result_id)] = {
        "evaluation_version": version.pk,
        "false_exclusion": false_exclusion,
    }
    selection.save(update_fields=["labels"])
    count = sum(bool(label["false_exclusion"]) for label in selection.labels.values())
    if count > selection.threshold:
        Incident.objects.get_or_create(
            fingerprint=f"rejects:{selection.pk}",
            defaults={
                "workspace_id": workspace_id,
                "sampling": selection,
                "recipient": selection.system.owner or actor,
                "category": "false_exclusion",
                "count": count,
            },
        )
    audit(
        actor,
        workspace_id,
        "rejects.relabelled",
        selection.pk,
        {"version": version.number, "count": count, "rules_published": False},
    )
    return version


def scoped_audit(actor, workspace_id, starts_at, ends_at):
    checked_interval(starts_at, ends_at)
    membership = member(actor, workspace_id)
    events = (
        AuditEvent.objects.filter(
            workspace_id=workspace_id, created_at__gte=starts_at, created_at__lt=ends_at
        )
        .select_related("actor")
        .order_by("created_at", "pk")
    )
    if membership.role != "administrator":
        # Only identifiers currently in the reader's scope are eligible; raw details never leave here.
        allowed = {
            str(pk)
            for qs in [
                sources_for(actor, workspace_id),
                systems_for(actor, workspace_id),
                records_for(actor, workspace_id),
                runs_for(actor, workspace_id),
            ]
            for pk in qs.values_list("pk", flat=True)
        }
        allowed.update(permitted_result_ids(actor, workspace_id))
        events = events.filter(object_id__in=allowed)
    safe_keys = {
        "version",
        "count",
        "state",
        "schema_version",
        "configuration_version",
        "bundle",
        "delivery",
        "attempts",
        "processing_number",
    }
    return [
        {
            "id": str(event.pk),
            "actor": event.actor.username if event.actor else None,
            "time": event.created_at.isoformat(),
            "action": event.action,
            "object": event.object_id,
            "versions": {
                key: value
                for key, value in event.details.items()
                if key in safe_keys
                and type(value) in {int, bool}
                or key in {"state", "bundle"}
                and isinstance(value, str)
                and len(value) < 100
            },
        }
        for event in events[:1000]
    ]


def monitor(actor, workspace_id, starts_at, ends_at, source_id=None, system_id=None):
    checked_interval(starts_at, ends_at)
    runs = runs_for(actor, workspace_id).filter(
        created_at__gte=starts_at, created_at__lt=ends_at
    )
    if source_id:
        get_object_or_404(sources_for(actor, workspace_id), pk=source_id)
        runs = runs.filter(source_id=source_id)
    if system_id:
        get_object_or_404(systems_for(actor, workspace_id), pk=system_id)
        runs = runs.filter(system_id=system_id)
    allowed_results = permitted_result_ids(actor, workspace_id)
    rows, counts = [], {}
    for run in runs.order_by("created_at", "pk")[:1000]:
        counts[run.state] = counts.get(run.state, 0) + 1
        record_ids = list(
            run.capture_set.exclude(record_id=None).values_list("record_id", flat=True)
        )
        deliveries = DeliveryIntent.objects.filter(
            workspace_id=workspace_id,
            result__record_id__in=record_ids,
            result_id__in=allowed_results,
        )
        if system_id:
            deliveries = deliveries.filter(result__system_id=system_id)
        rows.append(
            {
                "run": str(run.pk),
                "correlation_id": str(run.pk),
                "source": str(run.source_id),
                "system": str(run.system_id) if run.system_id else None,
                "state": run.state,
                "usage": run.usage,
                "usage_as_of": run.usage.get(
                    "as_of", "Unavailable: legacy usage has no observation timestamp"
                ),
                "record_ids": [str(pk) for pk in record_ids],
                "delivery_ids": [
                    str(pk) for pk in deliveries.values_list("pk", flat=True)
                ],
                "limits": run.limits,
            }
        )
    return {
        "counts": counts,
        "runs": rows,
        "returned_count": len(rows),
        "total_matching": runs.count(),
        "truncated": runs.count() > len(rows),
        "provider_cost": "Unavailable: no model/provider invoked",
        "accounting": "Persisted worker usage; timestamp is last durable run update",
    }


@transaction.atomic
def save_alert_rule(
    actor,
    workspace_id,
    category,
    threshold,
    interval_minutes,
    recipient_id,
    source_id=None,
    system_id=None,
):
    member(actor, workspace_id, admin=True)
    if (
        category not in {"login", "failure", "queue_age"}
        or type(threshold) is not int
        or not 1 <= threshold <= 10000
        or type(interval_minutes) is not int
        or not 1 <= interval_minutes <= 10080
    ):
        raise ValidationError(
            "Choose login/failure/queue_age and bounded positive threshold/window minutes."
        )
    recipient = get_object_or_404(
        Membership, workspace_id=workspace_id, user_id=recipient_id
    ).user
    if source_id:
        get_object_or_404(sources_for(actor, workspace_id), pk=source_id)
    if system_id:
        get_object_or_404(systems_for(actor, workspace_id), pk=system_id)
    rule = AlertRule.objects.create(
        workspace_id=workspace_id,
        actor=actor,
        recipient=recipient,
        source_id=source_id,
        system_id=system_id,
        category=category,
        threshold=threshold,
        interval_minutes=interval_minutes,
    )
    audit(actor, workspace_id, "alert.rule_saved", rule.pk, {"count": threshold})
    return rule


@transaction.atomic
def evaluate_alerts(actor, workspace_id):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    member(actor, workspace_id, admin=True)
    now, created = timezone.now(), []
    for rule in AlertRule.objects.filter(workspace_id=workspace_id, enabled=True):
        runs = runs_for(actor, workspace_id)
        if rule.source_id:
            runs = runs.filter(source_id=rule.source_id)
        if rule.system_id:
            runs = runs.filter(system_id=rule.system_id)
        cutoff = now - timedelta(minutes=rule.interval_minutes)
        if rule.category == "queue_age":
            count = runs.filter(
                state__in=["queued", "running"], created_at__lte=cutoff
            ).count()
        else:
            states = (
                ["login_required", "needs_login", "blocked_login", "needs_access"]
                if rule.category == "login"
                else ["failed", "partial", "action_required"]
            )
            count = (
                runs.filter(created_at__gte=cutoff)
                .filter(
                    Q(state__in=states) | Q(reason__icontains="login")
                    if rule.category == "login"
                    else Q(state__in=states)
                )
                .count()
            )
        if count < rule.threshold:
            if rule.condition_active:
                rule.condition_active = False
                rule.save(update_fields=["condition_active"])
            continue
        if rule.condition_active:
            continue
        rule.condition_active = True
        rule.save(update_fields=["condition_active"])
        # One incident per observed threshold crossing, including after acknowledgement/resolution.
        existing = Incident.objects.filter(
            rule=rule, state__in=["open", "acknowledged"]
        ).first()
        if existing:
            continue
        incident = Incident.objects.create(
            workspace_id=workspace_id,
            rule=rule,
            recipient=rule.recipient,
            fingerprint=f"rule:{rule.pk}:{now.isoformat()}",
            category=rule.category,
            count=count,
        )
        created.append(incident)
        audit(
            actor, workspace_id, "alert.incident_created", incident.pk, {"count": count}
        )
    return created


@transaction.atomic
def incident_action(actor, workspace_id, incident_id, action):
    membership = member(actor, workspace_id)
    incident = get_object_or_404(
        Incident.objects.select_for_update(), pk=incident_id, workspace_id=workspace_id
    )
    if membership.role != "administrator" and incident.recipient_id != actor.pk:
        raise PermissionDenied("Only the incident recipient or administrator may act.")
    if action == "acknowledge" and incident.state == "open":
        incident.state, incident.acknowledged_by, incident.acknowledged_at = (
            "acknowledged",
            actor,
            timezone.now(),
        )
    elif action == "resolve" and incident.state in {"open", "acknowledged"}:
        incident.state, incident.resolved_by, incident.resolved_at = (
            "resolved",
            actor,
            timezone.now(),
        )
    else:
        raise ValidationError("Incident state changed; reload before acting.")
    incident.save()
    audit(
        actor, workspace_id, "alert." + action, incident.pk, {"state": incident.state}
    )
    return incident
