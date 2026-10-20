from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from workspaces.models import Membership

from .models import EnrichmentAttempt, ReviewTask
from .review_workflow import apply_enrichment, complete_review, follow_up, reopen_review
from .routing import live_access, results_for
from .views import context


class FollowupForm(forms.Form):
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    action = forms.ChoiceField(
        choices=[
            ("defer", "Defer"),
            ("escalate", "Escalate in-app"),
            ("reopen", "Information arrived: return to review"),
            ("complete", "Complete review"),
            ("enrichment", "Apply permitted enrichment and requalify"),
        ]
    )
    owner = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(), required=False
    )
    next_review_at = forms.DateTimeField(
        required=False,
        help_text="Include timezone offset; times without an offset use the configured server timezone.",
    )
    reason = forms.CharField(max_length=1000, required=False, widget=forms.Textarea)
    attempt = forms.ModelChoiceField(
        queryset=EnrichmentAttempt.objects.none(), required=False
    )

    def clean(self):
        values = super().clean()
        action = values.get("action")
        if action in {"defer", "escalate"}:
            for key in ["owner", "next_review_at", "reason"]:
                if not values.get(key):
                    self.add_error(key, "Required for defer/escalation.")
        elif action == "enrichment" and not values.get("attempt"):
            self.add_error("attempt", "Choose a completed or capped attempt.")
        elif action in {"complete", "reopen"} and not values.get("reason"):
            self.add_error("reason", "A reason is required.")
        return values


@login_required
@require_http_methods(["GET", "POST"])
def followup(request, workspace_id, result_id):
    result = get_object_or_404(results_for(request.user, workspace_id), pk=result_id)
    live_access(request.user, result, review=True)
    form = FollowupForm(
        request.POST or None, initial={"expected_version": result.version}
    )
    recipients = []
    for membership in Membership.objects.filter(
        workspace_id=workspace_id, role__in=["administrator", "operator", "reviewer"]
    ).select_related("user")[:250]:
        try:
            live_access(membership.user, result, review=True)
        except PermissionDenied:
            continue
        recipients.append(membership.user_id)
    form.fields["owner"].queryset = get_user_model().objects.filter(pk__in=recipients)
    form.fields["attempt"].queryset = EnrichmentAttempt.objects.filter(
        workspace_id=workspace_id,
        record=result.record,
        policy__system=result.system,
        state__in=["completed", "cap_reached", "superseded"],
    )
    if request.method == "POST" and form.is_valid():
        values = form.cleaned_data
        try:
            action = values["action"]
            if action in {"defer", "escalate"}:
                follow_up(
                    request.user,
                    workspace_id,
                    result.pk,
                    values["expected_version"],
                    action,
                    values["owner"],
                    values["next_review_at"],
                    values["reason"],
                )
            elif action == "reopen":
                reopen_review(
                    request.user,
                    workspace_id,
                    result.pk,
                    values["expected_version"],
                    values["reason"],
                )
            elif action == "complete":
                complete_review(
                    request.user,
                    workspace_id,
                    result.pk,
                    values["expected_version"],
                    values["reason"],
                )
            else:
                apply_enrichment(
                    request.user,
                    workspace_id,
                    result.pk,
                    values["expected_version"],
                    values["attempt"].pk,
                )
            return redirect("review-detail", workspace_id, result.pk)
        except ValidationError as error:
            form.add_error(None, error)
    return render(
        request,
        "intelligence/followup.html",
        {**context(request, workspace_id), "result": result, "form": form},
    )


@login_required
@require_GET
def tasks(request, workspace_id):
    visible = []
    for task in (
        ReviewTask.objects.filter(workspace_id=workspace_id, recipient=request.user)
        .select_related(
            "result__system",
            "result__record__source",
            "result__content_version",
            "result__configuration",
            "result__analysis",
        )
        .order_by("due_at", "pk")
        .iterator(chunk_size=100)
    ):
        try:
            live_access(request.user, task.result, allow_inactive=True)
        except PermissionDenied:
            continue
        visible.append(task)
        if len(visible) == 100:
            break
    return render(
        request,
        "intelligence/followup_tasks.html",
        {**context(request, workspace_id), "tasks": visible},
    )
