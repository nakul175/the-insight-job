from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .delivery import reconcile_delivery, retry_delivery
from .models import DeliveryIntent
from .permissions import member, systems_for
from .routing import live_access
from .views import context


class RecoveryForm(forms.Form):
    action = forms.ChoiceField(
        choices=[
            ("reconcile", "Reconcile existing local receipt"),
            ("retry", "Authorise bounded retry"),
        ]
    )
    reason = forms.CharField(max_length=1000, widget=forms.Textarea)
    bundle = forms.UUIDField(
        required=False,
        label="Repaired bundle ID (optional, same configuration and destination)",
    )
    attempt_budget = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=10,
        label="Raised total attempt budget (optional)",
    )


@login_required
@require_http_methods(["GET", "POST"])
def detail(request, workspace_id, intent_id):
    intent = get_object_or_404(
        DeliveryIntent,
        pk=intent_id,
        workspace_id=workspace_id,
        result__system__in=systems_for(request.user, workspace_id),
    )
    membership = member(request.user, workspace_id)
    try:
        live_access(request.user, intent.result, allow_inactive=True)
    except PermissionDenied:
        if membership.role != "administrator":
            raise
    can_manage = (
        membership.role == "administrator"
        or membership.role == "operator"
        and intent.result.system.owner_id == request.user.pk
    )
    form = RecoveryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not can_manage:
            raise PermissionDenied(
                "Only the scoped system owner or administrator can recover delivery."
            )
        try:
            values = form.cleaned_data
            if values["action"] == "reconcile":
                reconcile_delivery(
                    request.user, workspace_id, intent.pk, values["reason"]
                )
            else:
                retry_delivery(
                    request.user,
                    workspace_id,
                    intent.pk,
                    values["reason"],
                    values["bundle"],
                    values["attempt_budget"],
                )
            return redirect("delivery-detail", workspace_id, intent.pk)
        except ValidationError as error:
            form.add_error(None, error)
    return render(
        request,
        "intelligence/delivery_detail.html",
        {
            **context(request, workspace_id),
            "intent": intent,
            "form": form,
            "can_manage": can_manage,
            "may_retry": intent.state in {"retry", "action_required"},
            "may_reconcile": intent.state in {"ambiguous", "retry", "action_required"},
        },
    )
