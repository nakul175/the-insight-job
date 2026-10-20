from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from workspaces.models import Workspace

from .access import NeedsAccess, connection_for
from .models import Source, SourceActivation
from .permissions import member, sources_for
from .source_testing import activate
from .views import context


class ActivationForm(forms.Form):
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    test_id = forms.UUIDField(widget=forms.HiddenInput)
    confirm_scope = forms.BooleanField(
        label="I confirm the exact source, access and tested scope shown above."
    )


@login_required
@require_http_methods(["GET", "POST"])
def source_setup(request, workspace_id, source_id):
    source = get_object_or_404(sources_for(request.user, workspace_id), pk=source_id)
    version = source.versions.get(version=source.config_version)
    config = version.config
    access_error = None
    try:
        connection_for(source, version)
    except (NeedsAccess, ValidationError):
        access_error = "Complete or reconnect the source account access configuration."
    test = (
        source.tests.filter(source_version=version, state__in=["passed", "partial"])
        .order_by("-created_at")
        .first()
    )
    form = ActivationForm(
        request.POST or None,
        initial={
            "expected_version": source.config_version,
            "test_id": test.pk if test else None,
        },
    )
    scope = {
        "source_url": source.url,
        "name": source.name,
        "owner_id": source.owner_id,
        "source_version": source.config_version,
        "access": config.get("access"),
        "include": config.get("include", []),
        "exclude": config.get("exclude", []),
        "limits": config.get("limits", {}),
        "partial_policy": config.get("partial_policy"),
        "partial_reason": config.get("partial_reason", ""),
    }
    if request.method == "POST" and form.is_valid():
        member(request.user, workspace_id, write=True)
        try:
            with transaction.atomic():
                Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
                current = Source.objects.select_for_update().get(pk=source.pk)
                if (
                    form.cleaned_data["expected_version"] != current.config_version
                    or not test
                    or str(test.pk) != str(form.cleaned_data["test_id"])
                ):
                    raise ValidationError(
                        "Tested scope changed or sample is missing; review and retest the current version."
                    )
                activate(request.user, current, test.pk)
                SourceActivation.objects.create(
                    workspace_id=workspace_id,
                    source=current,
                    source_version=version,
                    test=test,
                    actor=request.user,
                    scope=scope,
                )
            return redirect("source-setup", workspace_id, source.pk)
        except ValidationError as error:
            form.add_error(None, error)
    return render(
        request,
        "intelligence/setup.html",
        {
            **context(request, workspace_id),
            "source": source,
            "scope": scope,
            "test": test,
            "access_error": access_error,
            "form": form,
            "details_complete": bool(source.name and source.url and source.owner_id),
            "scope_complete": bool(config.get("include") and config.get("limits")),
            "activations": SourceActivation.objects.filter(source=source).order_by(
                "-created_at"
            )[:10],
        },
    )
