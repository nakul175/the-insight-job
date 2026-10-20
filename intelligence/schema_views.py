from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .content import schemas_for
from .models import EvaluationSet, SchemaCheck
from .schema_lifecycle import (
    check_schema,
    permitted_samples,
    publish_schema,
    retire_schema,
    save_samples,
)
from .views import context


class SamplesForm(forms.Form):
    name = forms.CharField(max_length=120)
    sample_set = forms.UUIDField(required=False)
    expected_version = forms.IntegerField(min_value=0, initial=0)
    samples = forms.JSONField(
        widget=forms.Textarea,
        help_text='List: [{"content_version":"UUID","fields":{"budget":12},"routing":"include","qualification":"pass"}]. Values and outcomes are labels, not acceptance evidence.',
    )


class CheckForm(forms.Form):
    draft_number = forms.IntegerField(min_value=1)
    evaluation_version = forms.IntegerField(min_value=1)


class PublishForm(forms.Form):
    draft_number = forms.IntegerField(min_value=1)
    check_id = forms.UUIDField()


@login_required
@require_http_methods(["GET", "POST"])
def lifecycle(request, workspace_id, schema_id):
    schema = get_object_or_404(schemas_for(request.user, workspace_id), pk=schema_id)
    action = request.POST.get("action") if request.method == "POST" else None
    form_type = {
        "samples": SamplesForm,
        "check": CheckForm,
        "publish": PublishForm,
    }.get(action)
    active_form = form_type(request.POST) if form_type else None
    error = None
    if request.method == "POST":
        try:
            if action == "retire":
                retire_schema(request.user, workspace_id, schema.pk)
            elif not active_form or not active_form.is_valid():
                raise ValidationError("Correct the action form fields.")
            elif action == "samples":
                data = active_form.cleaned_data
                save_samples(
                    request.user,
                    workspace_id,
                    schema.pk,
                    data["name"],
                    data["samples"],
                    data["sample_set"],
                    data["expected_version"],
                )
            elif action == "check":
                data = active_form.cleaned_data
                check_schema(
                    request.user,
                    workspace_id,
                    schema.pk,
                    data["draft_number"],
                    data["evaluation_version"],
                )
            elif action == "publish":
                data = active_form.cleaned_data
                publish_schema(
                    request.user,
                    workspace_id,
                    schema.pk,
                    data["draft_number"],
                    data["check_id"],
                )
            return redirect("schema-lifecycle", workspace_id, schema.pk)
        except ValidationError as exc:
            error = "; ".join(exc.messages)
    sets = EvaluationSet.objects.filter(schema=schema).prefetch_related(
        "versions__samples__content_version__record__source",
        "versions__samples__analysis",
    )
    visible_sets = []
    for sample_set in sets:
        versions = []
        for version in sample_set.versions.order_by("-number"):
            try:
                samples = permitted_samples(request.user, version)
            except PermissionDenied:
                continue
            versions.append({"version": version, "samples": samples})
        if versions:
            visible_sets.append({"set": sample_set, "versions": versions})
    checks = []
    for check in SchemaCheck.objects.filter(
        schema_version__schema=schema
    ).select_related("evaluation_version__sample_set__schema"):
        try:
            permitted_samples(request.user, check.evaluation_version)
        except PermissionDenied:
            continue
        checks.append(check)
    return render(
        request,
        "intelligence/schema_lifecycle.html",
        {
            **context(request, workspace_id),
            "schema": schema,
            "sets": visible_sets,
            "checks": checks,
            "error": error,
            "samples_form": active_form if action == "samples" else SamplesForm(),
            "check_form": active_form if action == "check" else CheckForm(),
            "publish_form": active_form if action == "publish" else PublishForm(),
        },
        status=400 if error else 200,
    )
