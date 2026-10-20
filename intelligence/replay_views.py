from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .permissions import records_for, systems_for
from .replay import enable_replay_delivery, execute_replay, preview_replay
from .routing import permitted_result_ids
from .views import context


class ReplayForm(forms.Form):
    action = forms.ChoiceField(
        choices=[
            ("preview", "Preview pinned replay"),
            ("execute", "Start previewed replay"),
            ("enable", "Enable reviewed replay result set"),
        ]
    )
    payload = forms.JSONField(widget=forms.Textarea)


@login_required
@require_http_methods(["GET", "POST"])
def replay(request, workspace_id, system_id):
    system = get_object_or_404(systems_for(request.user, workspace_id), pk=system_id)
    form = ReplayForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data["payload"]
        try:
            if not isinstance(data, dict):
                raise ValidationError("Provide a bounded action object.")
            action = form.cleaned_data["action"]
            if action == "preview":
                preview_replay(
                    request.user,
                    workspace_id,
                    system.pk,
                    data["bundle_id"],
                    data["record_ids"],
                )
            else:
                run = get_object_or_404(
                    system.replays, pk=data["run_id"], workspace_id=workspace_id
                )
                if action == "execute":
                    execute_replay(request.user, workspace_id, run.pk)
                else:
                    enable_replay_delivery(
                        request.user,
                        workspace_id,
                        run.pk,
                        data["result_ids"],
                        data["reason"],
                    )
            return redirect("system-replay", workspace_id, system.pk)
        except (ValidationError, KeyError, ValueError, TypeError) as error:
            form.add_error(
                None,
                error
                if isinstance(error, ValidationError)
                else "Check the documented action fields.",
            )
    permitted = {
        str(pk)
        for pk in records_for(request.user, workspace_id).values_list("pk", flat=True)
    }
    permitted_results = permitted_result_ids(request.user, workspace_id)
    visible = [
        run
        for run in system.replays.order_by("-number")
        if all(pin["record"] in permitted for pin in run.pins)
        and all(
            str(pk) in permitted_results
            for pk in run.results.values_list("pk", flat=True)
        )
    ][:100]
    # Historical operational metadata requires current source/record scope.
    return render(
        request,
        "intelligence/replay.html",
        {
            **context(request, workspace_id),
            "system": system,
            "form": form,
            "replays": visible,
            "bundles": system.bundles.order_by("-number"),
        },
    )
