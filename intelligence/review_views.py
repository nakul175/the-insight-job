import json
from datetime import timedelta

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import ContentSchema, Destination, UserDraft
from .permissions import records_for, systems_for
from .routing import (
    backlog_preview,
    claim_review,
    configure_qualification,
    correct_field,
    live_access,
    override_decision,
    result_is_current,
    results_for,
    route_independently,
    set_system_state,
)
from .views import context


class QualificationForm(forms.Form):
    expected_version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    schema = forms.ModelChoiceField(queryset=ContentSchema.objects.none())
    rules = forms.JSONField(
        widget=forms.Textarea,
        help_text='{"group":"all","unknown":"review","rules":[{"field":"budget","operator":"gte","value":10,"unit":"USD","hard_fail":true}]} . No semantic inference or unit conversion.',
    )
    destination = forms.ModelChoiceField(
        queryset=Destination.objects.none(), required=False
    )


@login_required
@require_http_methods(["GET", "POST"])
def qualification(request, workspace_id, system_id):
    system = get_object_or_404(systems_for(request.user, workspace_id), pk=system_id)
    form = QualificationForm(
        request.POST or None, initial={"expected_version": system.config_version}
    )
    form.fields["schema"].queryset = ContentSchema.objects.filter(
        workspace_id=workspace_id,
        system=system,
        state="published",
        published_version__isnull=False,
    )
    form.fields["destination"].queryset = Destination.objects.filter(
        workspace_id=workspace_id, system=system, state="test_passed"
    )
    if request.method == "POST" and form.is_valid():
        try:
            data = form.cleaned_data
            configure_qualification(
                request.user,
                workspace_id,
                system.pk,
                data["expected_version"],
                data["schema"].pk,
                data["rules"],
                data["destination"].pk if data["destination"] else None,
            )
            return redirect("system-detail", workspace_id, system.pk)
        except ValidationError as error:
            form.add_error(None, error)
    return render(
        request,
        "intelligence/qualification.html",
        {**context(request, workspace_id), "system": system, "form": form},
    )


class ProcessForm(forms.Form):
    record = forms.ModelChoiceField(queryset=None)
    systems = forms.ModelMultipleChoiceField(queryset=None)


@login_required
@require_http_methods(["GET", "POST"])
def process(request, workspace_id):
    form = ProcessForm(request.POST or None)
    form.fields["record"].queryset = records_for(request.user, workspace_id)
    form.fields["systems"].queryset = systems_for(request.user, workspace_id).exclude(
        state__in=["paused", "retired"]
    )
    outcomes = None
    if request.method == "POST" and form.is_valid():
        outcomes = route_independently(
            request.user,
            workspace_id,
            form.cleaned_data["record"].pk,
            [s.pk for s in form.cleaned_data["systems"]],
        )
    return render(
        request,
        "intelligence/process.html",
        {**context(request, workspace_id), "form": form, "outcomes": outcomes},
    )


@login_required
@require_http_methods(["GET", "POST"])
def state(request, workspace_id, system_id):
    system = get_object_or_404(systems_for(request.user, workspace_id), pk=system_id)
    error = None
    if request.method == "POST":
        try:
            system = set_system_state(
                request.user, workspace_id, system.pk, request.POST.get("state")
            )
        except ValidationError as exc:
            error = "; ".join(exc.messages)
    return render(
        request,
        "intelligence/system_state.html",
        {
            **context(request, workspace_id),
            "system": system,
            "backlog": backlog_preview(request.user, system),
            "error": error,
        },
    )


class QueueForm(forms.Form):
    reason = forms.CharField(max_length=100, required=False)
    system = forms.ModelChoiceField(queryset=None, required=False)
    age_hours = forms.IntegerField(min_value=0, max_value=87600, required=False)
    owner = forms.CharField(
        max_length=100, required=False, help_text="Username, me or unassigned"
    )


@login_required
@require_http_methods(["GET"])
def queue(request, workspace_id):
    form = QueueForm(request.GET)
    form.fields["system"].queryset = systems_for(request.user, workspace_id)
    data = {**context(request, workspace_id), "form": form}
    if not form.is_valid():
        return render(request, "intelligence/review_queue.html", data, status=400)
    try:
        rows = results_for(request.user, workspace_id).filter(
            decision__in=["review", "enrich", "excluded", "fail", "incomplete"]
        )
        values = form.cleaned_data
        if values["reason"]:
            rows = rows.filter(reason__icontains=values["reason"])
        if values["system"]:
            rows = rows.filter(system=values["system"])
        if values["age_hours"] is not None:
            rows = rows.filter(
                created_at__lte=timezone.now() - timedelta(hours=values["age_hours"])
            )
        if values["owner"] == "me":
            rows = rows.filter(assigned_to=request.user)
        elif values["owner"] == "unassigned":
            rows = rows.filter(assigned_to__isnull=True)
        elif values["owner"]:
            rows = rows.filter(assigned_to__username=values["owner"])
        visible = []
        for result in (
            rows.select_related("system", "record", "assigned_to")
            .order_by("created_at", "pk")
            .iterator(chunk_size=100)
        ):
            try:
                live_access(request.user, result, allow_inactive=True)
            except PermissionDenied:
                continue
            visible.append(result)
            if len(visible) == 100:
                break
        data.update(rows=visible, load_failed=False)
    except DatabaseError:
        data["load_failed"] = True
        return render(request, "intelligence/review_queue.html", data, status=503)
    return render(request, "intelligence/review_queue.html", data)


class ReviewForm(forms.Form):
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    action = forms.ChoiceField(
        choices=[
            ("claim", "Claim review"),
            ("correct", "Correct field"),
            ("override", "Override business decision"),
        ]
    )
    key = forms.CharField(max_length=60, required=False)
    value = forms.JSONField(required=False, widget=forms.Textarea)
    segment = forms.IntegerField(min_value=0, required=False)
    decision = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Choose decision"),
            *[(x, x.title()) for x in ["include", "exclude", "pass", "fail", "review"]],
        ],
    )
    reason = forms.CharField(max_length=1000, required=False, widget=forms.Textarea)


@login_required
@require_http_methods(["GET", "POST"])
def detail(request, workspace_id, result_id):
    result = get_object_or_404(
        results_for(request.user, workspace_id).select_related(
            "system",
            "record",
            "analysis",
            "content_version",
            "schema_version",
            "configuration",
            "assigned_to",
        ),
        pk=result_id,
    )
    from .review_workflow import evidence_for

    enriched_evidence = evidence_for(request.user, result)
    initial = {"expected_version": result.version, "action": "claim"}
    pending = UserDraft.objects.filter(
        workspace_id=workspace_id,
        actor=request.user,
        scope_kind="result",
        scope_id=result.pk,
        form_key="conflict",
    ).first()
    if request.method == "GET" and pending:
        initial.update(
            {
                key: values[-1]
                for key, values in pending.payload.items()
                if values
                and key
                in {
                    "expected_version",
                    "action",
                    "key",
                    "value",
                    "segment",
                    "decision",
                    "reason",
                }
            }
        )
    form_data = request.POST or None
    if request.method == "GET" and pending:
        from django.http import QueryDict

        form_data = QueryDict(mutable=True)
        for key, value in initial.items():
            form_data[key] = value
    form = ReviewForm(form_data, initial=initial)
    version_conflict = bool(
        pending and str(initial["expected_version"]) != str(result.version)
    )
    if request.method == "POST" and form.is_valid():
        try:
            values = form.cleaned_data
            if values["action"] == "claim":
                claim_review(
                    request.user, workspace_id, result.pk, values["expected_version"]
                )
            elif values["action"] == "correct":
                correct_field(
                    request.user,
                    workspace_id,
                    result.pk,
                    values["expected_version"],
                    values["key"],
                    values["value"],
                    values["segment"],
                    values["reason"],
                )
            else:
                override_decision(
                    request.user,
                    workspace_id,
                    result.pk,
                    values["expected_version"],
                    values["decision"],
                    values["reason"],
                )
            UserDraft.objects.filter(
                workspace_id=workspace_id,
                actor=request.user,
                scope_kind="result",
                scope_id=result.pk,
                form_key="conflict",
            ).delete()
            return redirect("review-detail", workspace_id, result.pk)
        except ValidationError as error:
            result.refresh_from_db()
            live_access(request.user, result, review=True)
            version_conflict = values[
                "expected_version"
            ] != result.version or not result_is_current(result)
            if version_conflict:
                form.add_error(
                    None,
                    "Version conflict: your change was not saved. Compare the current revision; your unsaved reason is retained.",
                )
                from django.db import transaction

                from workspaces.models import Workspace

                with transaction.atomic():
                    Workspace.objects.select_for_update(no_key=True).get(
                        pk=workspace_id
                    )
                    fresh = get_object_or_404(
                        results_for(request.user, workspace_id), pk=result.pk
                    )
                    live_access(request.user, fresh, review=True)
                    UserDraft.objects.update_or_create(
                        workspace_id=workspace_id,
                        actor=request.user,
                        scope_kind="result",
                        scope_id=result.pk,
                        form_key="conflict",
                        defaults={
                            "payload": {
                                key: [request.POST[key]]
                                for key in [
                                    "expected_version",
                                    "action",
                                    "key",
                                    "value",
                                    "segment",
                                    "decision",
                                    "reason",
                                ]
                                if key in request.POST
                            },
                            "updated_at": timezone.now(),
                        },
                    )
            form.add_error(None, error)
            enriched_evidence = evidence_for(request.user, result)
    captures = {str(item["capture"].pk): item["capture"] for item in enriched_evidence}
    field_rows = []
    for key, fact in result.fields.items():
        candidates = []
        for candidate in fact.get("candidates", []):
            capture = captures.get(candidate.get("capture"))
            candidates.append(
                {
                    "value": candidate.get("value"),
                    "method": candidate.get("method", "Unknown"),
                    "segment": candidate.get("segment"),
                    "source": capture.source.name
                    if capture
                    else result.record.source.name,
                    "content_version": capture.content_version.number
                    if capture and capture.content_version_id
                    else result.content_version.number
                    if not capture
                    else None,
                    "source_configuration": capture.run.source_version.version
                    if capture
                    else None,
                    "capture": str(capture.pk) if capture else None,
                }
            )
        field_rows.append(
            {
                "key": key,
                "status": fact.get("status"),
                "value": fact.get("value"),
                "unit": fact.get("unit", ""),
                "method": fact.get("method", "Unknown"),
                "candidates": candidates,
            }
        )
    return render(
        request,
        "intelligence/review_detail.html",
        {
            **context(request, workspace_id),
            "result": result,
            "form": form,
            "version_conflict": version_conflict,
            "field_rows": field_rows,
            "latest_change": result.changes.select_related("actor")
            .order_by("-created_at")
            .first(),
            "current_segments": result.record.current_analysis.segments
            if result.record.current_analysis_id
            else [],
            "current": result_is_current(result),
            "enriched_evidence": enriched_evidence,
            "relevance": json.dumps(result.relevance, indent=2),
            "qualification": json.dumps(result.qualification, indent=2),
            "fields": json.dumps(result.fields, indent=2),
            "segments": (result.analysis or result.content_version).segments,
            "changes": result.changes.select_related("actor").order_by("created_at"),
        },
    )
