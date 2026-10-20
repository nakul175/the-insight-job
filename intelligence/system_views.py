import json

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import ContentSchema
from .permissions import sources_for, systems_for
from .systems import (
    activation_gaps,
    create_system,
    current_config,
    duplicate_system,
    request_activation,
    save_configuration,
)
from .views import context


class SystemForm(forms.Form):
    name = forms.CharField(max_length=120)
    purpose = forms.CharField(max_length=500, widget=forms.Textarea)


class ConfigurationForm(forms.Form):
    expected_version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    sources = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple
    )
    types = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple
    )
    unknown_type = forms.ChoiceField(
        choices=[(x, x.title()) for x in ["review", "include", "exclude"]]
    )
    relevance = forms.JSONField(
        required=False,
        widget=forms.Textarea,
        help_text='Optional draft: {"all":["tender"],"none":[],"unknown":"review","examples":[{"text":"tender","expected":"include"}]} . Literal terms only; no semantic/model calls.',
    )

    def __init__(self, *args, actor, workspace_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sources"].choices = [
            (str(s.pk), s.name)
            for s in sources_for(actor, workspace_id).exclude(state="archived")
        ]
        self.fields["types"].choices = [
            (s.key, s.name)
            for s in ContentSchema.objects.filter(
                workspace_id=workspace_id,
                scope="type",
                state="published",
                published_version__isnull=False,
            )
        ]


@login_required
@require_http_methods(["GET", "POST"])
def systems(request, workspace_id):
    form = SystemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            system = create_system(request.user, workspace_id, **form.cleaned_data)
            return redirect("system-detail", workspace_id, system.pk)
        except ValidationError as error:
            form.add_error(None, error)
    return render(
        request,
        "intelligence/systems.html",
        {
            **context(request, workspace_id),
            "systems": systems_for(request.user, workspace_id),
            "form": form,
        },
    )


@login_required
@require_GET
def detail(request, workspace_id, system_id):
    system = get_object_or_404(systems_for(request.user, workspace_id), pk=system_id)
    return render(
        request,
        "intelligence/system.html",
        {
            **context(request, workspace_id),
            "system": system,
            "gaps": activation_gaps(system),
            "configuration": json.dumps(current_config(system), indent=2),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def configure(request, workspace_id, system_id):
    system = get_object_or_404(systems_for(request.user, workspace_id), pk=system_id)
    config = current_config(system)
    form = ConfigurationForm(
        request.POST or None,
        actor=request.user,
        workspace_id=workspace_id,
        initial={
            "expected_version": system.config_version,
            "sources": config.get("source_ids", []),
            "types": config.get("type_keys", []),
            "unknown_type": config.get("unknown_type", "review"),
            "relevance": config.get("relevance"),
        },
    )
    if request.method == "POST" and form.is_valid():
        values = form.cleaned_data
        draft = {
            **config,
            "source_ids": values["sources"],
            "type_keys": values["types"],
            "unknown_type": values["unknown_type"],
        }
        if values["relevance"]:
            draft["relevance"] = values["relevance"]
        else:
            draft.pop("relevance", None)
        try:
            save_configuration(
                request.user, workspace_id, system.pk, values["expected_version"], draft
            )
            return redirect("system-detail", workspace_id, system.pk)
        except ValidationError as error:
            form.add_error(None, error)
    return render(
        request,
        "intelligence/system_configure.html",
        {**context(request, workspace_id), "system": system, "form": form},
    )


@login_required
@require_POST
def duplicate(request, workspace_id, system_id):
    system = duplicate_system(request.user, workspace_id, system_id)
    return redirect("system-detail", workspace_id, system.pk)


@login_required
@require_POST
def activate(request, workspace_id, system_id):
    try:
        request_activation(request.user, workspace_id, system_id)
    except ValidationError as error:
        system = get_object_or_404(
            systems_for(request.user, workspace_id), pk=system_id
        )
        return render(
            request,
            "intelligence/system.html",
            {
                **context(request, workspace_id),
                "system": system,
                "gaps": error.messages,
            },
            status=409,
        )
    return redirect("system-detail", workspace_id, system_id)
