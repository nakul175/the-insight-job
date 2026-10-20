import json

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .destinations import register_destination, test_destination
from .enrichment import (
    enrichment_step,
    resume_enrichment,
    save_policy,
    start_enrichment,
)
from .models import EnrichmentAttempt, WorkItem
from .permissions import records_for, sources_for, system_can_read, systems_for
from .views import context


class DestinationForm(forms.Form):
    endpoint = forms.URLField(max_length=2000)
    secret_ref = forms.CharField(
        max_length=120,
        required=False,
        help_text="Opaque reference only. Credential-backed tests remain blocked in declared test mode.",
    )


class OperationForm(forms.Form):
    payload = forms.JSONField(widget=forms.Textarea)


@login_required
@require_http_methods(["GET", "POST"])
def destinations(request, workspace_id, system_id):
    system = get_object_or_404(systems_for(request.user, workspace_id), pk=system_id)
    form = DestinationForm(request.POST or None)
    error = None
    if request.method == "POST":
        try:
            if request.POST.get("action") == "test":
                from uuid import UUID

                try:
                    destination_id = UUID(request.POST.get("destination_id", ""))
                except (ValueError, TypeError) as exc:
                    raise ValidationError("Choose a valid destination ID.") from exc
                test_destination(request.user, workspace_id, system.pk, destination_id)
            elif form.is_valid():
                register_destination(
                    request.user, workspace_id, system.pk, **form.cleaned_data
                )
            else:
                raise ValidationError("Correct the destination fields.")
            return redirect("destinations", workspace_id, system.pk)
        except ValidationError as exc:
            error = "; ".join(exc.messages)
    return render(
        request,
        "intelligence/destinations.html",
        {
            **context(request, workspace_id),
            "system": system,
            "destinations": system.destinations.all(),
            "form": form,
            "error": error,
        },
        status=400 if error else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def enrichment(request, workspace_id, system_id):
    system = get_object_or_404(systems_for(request.user, workspace_id), pk=system_id)
    form = OperationForm(request.POST or None)
    error = None
    if request.method == "POST" and form.is_valid():
        try:
            data = form.cleaned_data["payload"]
            if not isinstance(data, dict):
                raise ValidationError("Operation payload must be an object.")
            action = request.POST.get("action")
            if action == "policy":
                save_policy(
                    request.user,
                    workspace_id,
                    system.pk,
                    data["expected_number"],
                    data["config"],
                )
            elif action == "start":
                start_enrichment(
                    request.user,
                    workspace_id,
                    system.pk,
                    data["record_id"],
                    data["trigger"],
                    data["links"],
                    data["unresolved_fields"],
                )
            elif action in {"step", "resume"}:
                attempt = get_object_or_404(
                    EnrichmentAttempt, pk=data["attempt_id"], policy__system=system
                )
                if action == "step":
                    enrichment_step(request.user, workspace_id, attempt.pk)
                else:
                    resume_enrichment(
                        request.user, workspace_id, attempt.pk, data["reason"]
                    )
            else:
                raise ValidationError("Choose a valid action.")
            return redirect("enrichment", workspace_id, system.pk)
        except (ValidationError, KeyError, ValueError, TypeError) as exc:
            error = (
                "; ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else "Invalid operation payload; check required identifiers and fields."
            )
    elif request.method == "POST":
        error = "Correct the JSON operation payload."
    attempts = []
    readable = set(records_for(request.user, workspace_id).values_list("pk", flat=True))
    source_ids = set(
        sources_for(request.user, workspace_id).values_list("pk", flat=True)
    )
    for attempt in EnrichmentAttempt.objects.filter(
        policy__system=system, record_id__in=readable, actor=request.user
    ).select_related("record__source", "policy"):
        jobs = WorkItem.objects.filter(
            workspace_id=workspace_id,
            request_key__startswith=f"enrichment:{attempt.pk}:",
        ).select_related("source")
        if not system_can_read(system, attempt.record.source) or any(
            j.source_id not in source_ids or not system_can_read(system, j.source)
            for j in jobs
        ):
            continue
        attempts.append(attempt)
    policy = system.enrichment_policies.order_by("-number").first()
    return render(
        request,
        "intelligence/enrichment.html",
        {
            **context(request, workspace_id),
            "system": system,
            "policy": policy,
            "policy_json": json.dumps(policy.config, indent=2)
            if policy
            else "No enrichment policy configured.",
            "attempts": attempts,
            "form": form,
            "error": error,
        },
        status=400 if error else 200,
    )
