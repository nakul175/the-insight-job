from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods

from .lifecycle import execute_deletion, preview_deletion
from .models import (
    DeletionOperation,
    DeliveryIntent,
    RecoveryExercise,
    RecoverySnapshot,
)
from .permissions import member
from .recovery import resume_delivery, run_exercise, take_snapshot
from .views import context


class LifecycleForm(forms.Form):
    action = forms.ChoiceField(
        choices=[
            ("preview", "Preview content deletion impact"),
            ("delete", "Execute previewed deletion"),
            ("snapshot", "Retain scoped recovery snapshot"),
            ("exercise", "Restore into isolated test database"),
            ("resume", "Resume delivery after reconciliation"),
        ]
    )
    payload = forms.JSONField(widget=forms.Textarea)


@login_required
@require_http_methods(["GET", "POST"])
def lifecycle(request, workspace_id):
    member(request.user, workspace_id, admin=True)
    form = LifecycleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            data, action = form.cleaned_data["payload"], form.cleaned_data["action"]
            if not isinstance(data, dict):
                raise ValidationError("Provide an action object.")
            if action == "preview":
                preview_deletion(
                    request.user,
                    workspace_id,
                    data["record_id"],
                    data["backup_scope"],
                    parse_datetime(data["backup_expires_at"])
                    if data.get("backup_expires_at")
                    else None,
                )
            elif action == "delete":
                operation = get_object_or_404(
                    DeletionOperation,
                    pk=data["operation_id"],
                    workspace_id=workspace_id,
                )
                if str(operation.record_id) != data["confirm_record_id"]:
                    raise ValidationError(
                        "Confirm the exact record ID shown in the impact preview."
                    )
                execute_deletion(request.user, workspace_id, operation.pk)
            elif action == "snapshot":
                take_snapshot(request.user, workspace_id, data["retention_minutes"])
            elif action == "exercise":
                run_exercise(
                    request.user, workspace_id, data["snapshot_id"], data["policy"]
                )
            else:
                resume_delivery(request.user, workspace_id, data["exercise_id"])
            return redirect("lifecycle", workspace_id)
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            form.add_error(
                None,
                error
                if isinstance(error, ValidationError)
                else "Check action fields and declared policy.",
            )
    return render(
        request,
        "intelligence/lifecycle.html",
        {
            **context(request, workspace_id),
            "form": form,
            "withdrawals": DeliveryIntent.objects.filter(
                workspace_id=workspace_id, operation="withdraw"
            ).order_by("-created_at")[:100],
            "deletions": DeletionOperation.objects.filter(
                workspace_id=workspace_id
            ).order_by("-created_at")[:100],
            "snapshots": RecoverySnapshot.objects.filter(workspace_id=workspace_id)
            .defer("payload")
            .order_by("-created_at")[:100],
            "exercises": RecoveryExercise.objects.filter(
                workspace_id=workspace_id
            ).order_by("-created_at")[:100],
        },
    )
