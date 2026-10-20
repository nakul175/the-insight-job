from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from . import external_delivery as service
from .models import (
    ConsumerSystem,
    DeliveryIntent,
    Destination,
    ExternalDestination,
    ExternalOperation,
)
from .permissions import member
from .views import context


class EndpointForm(forms.Form):
    destination = forms.ModelChoiceField(queryset=Destination.objects.none())
    endpoint = forms.URLField(max_length=2000)
    secret_ref = forms.RegexField(
        r"^TIJ_SECRET_[A-Z0-9_]{1,100}$",
        max_length=120,
        help_text="Protected environment reference; never enter the credential here.",
    )
    approval_ref = forms.CharField(max_length=300)
    receipt_contract_ref = forms.CharField(
        max_length=300,
        required=False,
        help_text="Evidence of receiver deduplication/receipt lookup support.",
    )
    supports_notices = forms.BooleanField(required=False)


class PackageForm(forms.Form):
    intent = forms.ModelChoiceField(queryset=DeliveryIntent.objects.none())
    destination = forms.ModelChoiceField(queryset=ExternalDestination.objects.none())
    previous = forms.ModelChoiceField(
        queryset=ExternalOperation.objects.none(), required=False
    )
    withdrawal = forms.BooleanField(required=False)


@login_required
@require_http_methods(["GET", "POST"])
def dashboard(request, workspace_id, system_id):
    member(request.user, workspace_id, admin=True)
    system = get_object_or_404(ConsumerSystem, pk=system_id, workspace_id=workspace_id)
    endpoint = EndpointForm(
        request.POST if request.POST.get("action") == "configure" else None
    )
    package = PackageForm(
        request.POST if request.POST.get("action") == "queue" else None
    )
    targets = ExternalDestination.objects.filter(
        workspace_id=workspace_id, destination__system=system
    )
    operations = ExternalOperation.objects.filter(
        workspace_id=workspace_id, destination__in=targets
    ).order_by("-created_at")
    endpoint.fields["destination"].queryset = Destination.objects.filter(
        workspace_id=workspace_id, system=system, state="test_passed"
    )
    package.fields["destination"].queryset = targets
    package.fields["intent"].queryset = DeliveryIntent.objects.filter(
        workspace_id=workspace_id, result__system=system
    ).exclude(operation="withdraw")
    package.fields["previous"].queryset = operations
    error = None
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "configure" and endpoint.is_valid():
                values = endpoint.cleaned_data.copy()
                destination = values.pop("destination")
                service.configure(request.user, workspace_id, destination.pk, **values)
            elif action == "queue" and package.is_valid():
                v = package.cleaned_data
                service.queue(
                    request.user,
                    workspace_id,
                    v["intent"].pk,
                    v["destination"].pk,
                    v["previous"].pk if v["previous"] else None,
                    v["withdrawal"],
                )
            elif action in {"activate", "deactivate"}:
                target = get_object_or_404(targets, pk=request.POST.get("target"))
                service.activate(
                    request.user, workspace_id, target.pk, action == "activate"
                )
            elif action in {"send", "lookup", "recover"}:
                operation = get_object_or_404(operations, pk=request.POST.get("target"))
                if action == "recover":
                    service.recover(
                        request.user,
                        workspace_id,
                        operation.pk,
                        request.POST.get("reason", ""),
                    )
                else:
                    service.send(
                        request.user,
                        workspace_id,
                        operation.pk,
                        lookup=action == "lookup",
                    )
            else:
                raise ValidationError(
                    "Correct the form and choose an available action."
                )
            return redirect("external-delivery", workspace_id, system_id)
        except (ValidationError, ValueError) as exc:
            error = (
                "; ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else "Choose a valid identifier."
            )
    return render(
        request,
        "intelligence/external_delivery.html",
        {
            **context(request, workspace_id),
            "system": system,
            "endpoint_form": endpoint,
            "package_form": package,
            "targets": targets,
            "operations": operations[:100],
            "error": error,
        },
    )
