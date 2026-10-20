"""Explicit owner actions for publication and workspace-local sandbox delivery."""

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .decision_support import comparison_summary, setup_progress
from .delivery import deliver, retry_delivery, save_contract, save_mapping, store_result
from .models import (
    ConfigurationComparison,
    DeliveryIntent,
    DestinationMapping,
    PublishedBundle,
    SystemResult,
)
from .permissions import member, systems_for
from .publication import compare_configurations, publish_bundle, rollback_bundle
from .routing import live_access
from .schema_lifecycle import permitted_samples
from .views import context


class ReleaseForm(forms.Form):
    action = forms.ChoiceField(
        choices=[
            (value, label)
            for value, label in [
                ("contract", "Define local sandbox contract"),
                ("mapping", "Validate field mapping"),
                ("compare", "Compare pinned configurations"),
                ("publish", "Publish tested local bundle"),
                ("rollback", "Roll back for new jobs"),
                ("store", "Validate result and commit outbox intent"),
                ("deliver", "Execute local sandbox intent"),
                ("retry", "Authorize bounded retry"),
            ]
        ]
    )
    payload = forms.JSONField(widget=forms.Textarea)


def integer(data, key, minimum=0):
    value = data[key]
    if type(value) is not int or value < minimum:
        raise ValidationError(f"{key}: provide an integer at least {minimum}.")
    return value


@login_required
@require_http_methods(["GET", "POST"])
def release(request, workspace_id, system_id):
    system = get_object_or_404(systems_for(request.user, workspace_id), pk=system_id)
    form = ReleaseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            data, action = form.cleaned_data["payload"], form.cleaned_data["action"]
            if not isinstance(data, dict):
                raise ValidationError("Action payload must be an object.")
            if action == "contract":
                save_contract(
                    request.user,
                    workspace_id,
                    system.pk,
                    data["destination_id"],
                    integer(data, "expected_number"),
                    data["fields"],
                    data["safe_upsert"],
                    data["supports_withdrawal"],
                    data["update_policy"],
                )
            elif action == "mapping":
                save_mapping(
                    request.user,
                    workspace_id,
                    system.pk,
                    integer(data, "schema_version_id", 1),
                    integer(data, "contract_id", 1),
                    data["entries"],
                )
            elif action == "compare":
                compare_configurations(
                    request.user,
                    workspace_id,
                    system.pk,
                    integer(data, "left", 1),
                    integer(data, "right", 1),
                    integer(data, "evaluation_version_id", 1),
                )
            elif action == "publish":
                blockers = [
                    step
                    for step in setup_progress(request.user, workspace_id, system)
                    if not step["complete"] and step["key"] != "publish"
                ]
                if blockers:
                    raise ValidationError(
                        "Publication held: "
                        + "; ".join(
                            step["name"] + ": " + step["reason"] for step in blockers
                        )
                    )
                publish_bundle(
                    request.user,
                    workspace_id,
                    system.pk,
                    integer(data, "expected_configuration", 1),
                    data["mapping_id"],
                    data["comparison_id"],
                )
            elif action == "rollback":
                rollback_bundle(
                    request.user,
                    workspace_id,
                    system.pk,
                    data["bundle_id"],
                    data["reason"],
                )
            elif action == "store":
                result = get_object_or_404(
                    SystemResult,
                    pk=data["result_id"],
                    workspace_id=workspace_id,
                    system=system,
                )
                store_result(
                    request.user,
                    workspace_id,
                    result.pk,
                    integer(data, "expected_version", 1),
                )
            else:
                intent = get_object_or_404(
                    DeliveryIntent,
                    pk=data["intent_id"],
                    workspace_id=workspace_id,
                    result__system=system,
                )
                from django.db import transaction

                from .systems import owned_system

                with transaction.atomic():
                    owned_system(request.user, workspace_id, system.pk)
                    if action == "deliver":
                        deliver(intent.pk)
                    else:
                        retry_delivery(
                            request.user,
                            workspace_id,
                            intent.pk,
                            data["reason"],
                            data.get("bundle_id"),
                            data.get("attempt_budget"),
                        )
            return redirect("system-release", workspace_id, system.pk)
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            form.add_error(
                None,
                "; ".join(error.messages)
                if isinstance(error, ValidationError)
                else "Invalid action fields; check the documented IDs and types.",
            )
    comparisons = []
    for comparison in ConfigurationComparison.objects.filter(
        system=system
    ).select_related("evaluation_version__sample_set__schema"):
        try:
            permitted_samples(request.user, comparison.evaluation_version)
        except PermissionDenied:
            continue
        comparison.summary = comparison_summary(comparison)
        comparisons.append(comparison)
    intents = []
    for intent in DeliveryIntent.objects.filter(
        workspace_id=workspace_id, result__system=system
    ).select_related(
        "result__system",
        "result__record__source",
        "result__content_version",
        "result__configuration",
        "result__analysis",
    ):
        try:
            live_access(request.user, intent.result, allow_inactive=True)
        except PermissionDenied:
            if (
                intent.operation != "withdraw"
                or member(request.user, workspace_id).role != "administrator"
            ):
                continue
        intents.append(intent)
    return render(
        request,
        "intelligence/release.html",
        {
            **context(request, workspace_id),
            "system": system,
            "form": form,
            "setup_steps": setup_progress(request.user, workspace_id, system),
            "destinations": system.destinations.prefetch_related("contracts"),
            "schemas": system.contentschema_set.prefetch_related("versions"),
            "configurations": system.configurations.order_by("-number"),
            "mappings": DestinationMapping.objects.filter(system=system),
            "comparisons": comparisons,
            "bundles": PublishedBundle.objects.filter(system=system).order_by(
                "-number"
            ),
            "intents": intents,
        },
    )
