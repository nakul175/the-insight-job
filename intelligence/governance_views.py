import json
from datetime import timedelta

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods

from .models import Incident, RejectSampling
from .observability import (
    evaluate_alerts,
    incident_action,
    monitor,
    relabel_reject,
    sample_rejects,
    save_alert_rule,
    scoped_audit,
)
from .permissions import member, systems_for
from .routing import live_access, results_for
from .views import context


class GovernanceForm(forms.Form):
    action = forms.ChoiceField(
        choices=[
            ("sample", "Select authorised rejects"),
            ("relabel", "Relabel sampled reject"),
            ("alert", "Configure in-app alert"),
            ("evaluate", "Evaluate alert thresholds"),
            ("acknowledge", "Acknowledge incident"),
            ("resolve", "Resolve incident"),
        ]
    )
    payload = forms.JSONField(widget=forms.Textarea, required=False)


@login_required
@require_http_methods(["GET", "POST"])
def governance(request, workspace_id):
    membership = member(request.user, workspace_id)
    form = GovernanceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            data = form.cleaned_data["payload"] or {}
            if not isinstance(data, dict):
                raise ValidationError("Provide an action object.")
            action = form.cleaned_data["action"]
            if action == "sample":
                sample_rejects(
                    request.user,
                    workspace_id,
                    data["system_id"],
                    parse_datetime(data["starts_at"]),
                    parse_datetime(data["ends_at"]),
                    data["limit"],
                    data["threshold"],
                )
            elif action == "relabel":
                relabel_reject(
                    request.user,
                    workspace_id,
                    data["sampling_id"],
                    data["result_id"],
                    data["fields"],
                    data["routing"],
                    data["qualification"],
                    data.get("sample_set_id"),
                    data.get("expected_version", 0),
                )
            elif action == "alert":
                save_alert_rule(
                    request.user,
                    workspace_id,
                    data["category"],
                    data["threshold"],
                    data["interval_minutes"],
                    data["recipient_id"],
                    data.get("source_id"),
                    data.get("system_id"),
                )
            elif action == "evaluate":
                evaluate_alerts(request.user, workspace_id)
            else:
                incident_action(request.user, workspace_id, data["incident_id"], action)
            return redirect("governance", workspace_id)
        except (ValidationError, KeyError, ValueError, TypeError) as error:
            form.add_error(
                None,
                error
                if isinstance(error, ValidationError)
                else "Check action fields and timezone-aware dates.",
            )
    end = timezone.now()
    start = end - timedelta(days=1)
    if request.GET.get("starts_at"):
        start = parse_datetime(request.GET["starts_at"])
    if request.GET.get("ends_at"):
        end = parse_datetime(request.GET["ends_at"])
    try:
        events = scoped_audit(request.user, workspace_id, start, end)
        metrics = monitor(
            request.user,
            workspace_id,
            start,
            end,
            request.GET.get("source_id"),
            request.GET.get("system_id"),
        )
    except (ValidationError, ValueError) as error:
        form.add_error(
            None,
            error if isinstance(error, ValidationError) else "Invalid interval/filter.",
        )
        events, metrics = None, None
    if request.GET.get("export") == "audit" and events is not None:
        # Export contains the same allowlisted metadata as the scoped on-screen view.
        response = JsonResponse({"events": events})
        response["Content-Disposition"] = 'attachment; filename="redacted-audit.json"'
        return response
    incidents = Incident.objects.filter(workspace_id=workspace_id)
    if membership.role != "administrator":
        incidents = incidents.filter(recipient=request.user)
    selections = RejectSampling.objects.filter(
        workspace_id=workspace_id, system__in=systems_for(request.user, workspace_id)
    )
    permitted_ids = set()
    for result in results_for(request.user, workspace_id):
        try:
            live_access(request.user, result, allow_inactive=True)
        except PermissionDenied:
            continue
        permitted_ids.add(str(result.pk))
    selections = [
        selection
        for selection in selections.order_by("-created_at")
        if set(selection.result_ids) <= permitted_ids
    ][:100]
    return render(
        request,
        "intelligence/governance.html",
        {
            **context(request, workspace_id),
            "form": form,
            "events": events,
            "metrics": json.dumps(metrics, indent=2),
            "incidents": incidents.order_by("-created_at")[:100],
            "selections": selections,
        },
    )
