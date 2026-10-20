import csv
import io
import json
from datetime import timedelta

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import F, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .content import (
    correct_types,
    decide_duplicate,
    duplicate_candidates,
    editable_record,
    preview_extraction,
    save_schema,
    schemas_for,
)
from .models import ExtractionJob, SchemaVersion
from .permissions import member, records_for, sources_for, systems_for
from .services import audit
from .views import context


def posted_uuid(request, key):
    try:
        return forms.UUIDField().clean(request.POST.get(key))
    except ValidationError as exc:
        raise Http404() from exc


def posted_integer(request, key):
    try:
        return forms.IntegerField(min_value=1).clean(request.POST.get(key))
    except ValidationError as exc:
        raise Http404() from exc


class SchemaForm(forms.Form):
    name = forms.CharField(max_length=120)
    key = forms.SlugField(max_length=60)
    scope = forms.ChoiceField(
        choices=[
            ("common", "Common envelope"),
            ("type", "Content type"),
            ("system", "System"),
        ]
    )
    system = forms.ModelChoiceField(queryset=None, required=False)
    expected_version = forms.IntegerField(
        min_value=0, widget=forms.HiddenInput, initial=0
    )
    fields = forms.JSONField(
        widget=forms.Textarea(attrs={"rows": 16}),
        help_text="JSON list: key, label, type (text/integer/number/boolean/date/enum), required, evidence_required, allowed_values, unit, instruction. This connector uses instruction as an exact label before a colon, for example Budget matches Budget: 12 USD. Natural-language model extraction is not enabled.",
    )

    def __init__(self, *args, actor, workspace_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["system"].queryset = systems_for(actor, workspace_id)


def filtered_records(actor, workspace_id, params, export=False):
    qs = records_for(actor, workspace_id, export=export)
    if params.get("q"):
        qs = qs.filter(
            Q(title__icontains=params["q"][:300]) | Q(body__icontains=params["q"][:300])
        )
    for key in ["status", "source", "type", "format", "language"]:
        value = params.get(key, "")
        if not value:
            continue
        if key == "source":
            source = get_object_or_404(sources_for(actor, workspace_id), pk=value)
            qs = qs.filter(source=source)
        elif key == "type":
            qs = (
                qs.filter(type_assignments__key=value)
                if value != "unknown"
                else qs.filter(type_assignments__isnull=True)
            )
        elif key in {"format", "language"}:
            qs = qs.filter(
                **{
                    "revisions__" + ("media_type" if key == "format" else key): value,
                    "revisions__number": F("version"),
                }
            )
        else:
            qs = qs.filter(status=value)
    if params.get("window") == "7d":
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=7))
    for name, lookup in [
        ("after", "created_at__date__gte"),
        ("before", "created_at__date__lte"),
    ]:
        if params.get(name):
            from datetime import date

            try:
                day = date.fromisoformat(params[name])
            except ValueError as exc:
                raise ValidationError("Use ISO dates for date filters.") from exc
            qs = qs.filter(**{lookup: day})
    sort = {"newest": "-created_at", "oldest": "created_at", "title": "title"}.get(
        params.get("sort"), "-created_at"
    )
    return qs.distinct().order_by(sort, "pk")


@login_required
@require_GET
def record_list(request, workspace_id):
    ctx = context(request, workspace_id)
    try:
        qs = (
            filtered_records(request.user, workspace_id, request.GET)
            .select_related("source")
            .prefetch_related("type_assignments")
        )
        paginator = Paginator(qs, 25)
        page = paginator.get_page(request.GET.get("page"))
        back = request.GET.urlencode()[:3000]
        query = request.GET.copy()
        query.pop("page", None)
        ctx.update(
            records=list(page.object_list),
            has_visible_records=records_for(request.user, workspace_id).exists(),
            has_filters=any(
                value
                for key, value in request.GET.items()
                if key not in {"page", "sort"}
            ),
            page=page,
            count=paginator.count,
            back=back,
            query=query.urlencode(),
            sources=list(sources_for(request.user, workspace_id)),
            types=list(schemas_for(request.user, workspace_id).filter(scope="type")),
        )
    except ValidationError as exc:
        return render(
            request,
            "intelligence/action_error.html",
            {**ctx, "error": str(exc)},
            status=400,
        )
    except DatabaseError:
        return render(
            request,
            "intelligence/records.html",
            {**ctx, "load_error": True},
            status=503,
        )
    return render(request, "intelligence/records.html", ctx)


def available_jobs(actor, workspace_id, record):
    schemas = (
        schemas_for(actor, workspace_id)
        .filter(
            Q(system__isnull=True)
            | Q(
                system__systemsourcegrant__source=record.source,
                system__systemsourcegrant__active=True,
            )
        )
        .exclude(system__state__in=["paused", "retired"])
    )
    return (
        ExtractionJob.objects.filter(
            workspace_id=workspace_id,
            record=record,
        )
        .filter(Q(schema_version__isnull=True) | Q(schema_version__schema__in=schemas))
        .filter(Q(content_version__isnull=True) | Q(content_version__record=record))
        .filter(Q(analysis__isnull=True) | Q(analysis__content_version__record=record))
        .select_related(
            "schema_version__schema", "content_version", "analysis", "actor"
        )
        .order_by("-created_at")
    )


@login_required
@require_GET
def record_detail(request, workspace_id, record_id):
    record = get_object_or_404(records_for(request.user, workspace_id), pk=record_id)
    versions = record.revisions.order_by("-number")
    try:
        version_number = (
            forms.IntegerField(min_value=1).clean(request.GET["version"])
            if request.GET.get("version")
            else None
        )
    except ValidationError:
        return HttpResponse("Use a valid capture version number.", status=400)
    revision = (
        get_object_or_404(versions, number=version_number)
        if version_number
        else versions.first()
    )
    back = request.GET.get("back", "")[:3000]
    ctx = {
        **context(request, workspace_id),
        "record": record,
        "revision": revision,
        "analysis": (revision.analyses.first() or revision) if revision else None,
        "versions": versions,
        "back_url": reverse("records", args=[workspace_id])
        + ("?" + back if back else "")
        + "#record-"
        + str(record.pk),
        "types": record.type_assignments.all(),
        "type_options": schemas_for(request.user, workspace_id)
        .filter(scope="type")
        .exclude(state="retired"),
        "schemas": SchemaVersion.objects.filter(
            schema__in=schemas_for(request.user, workspace_id)
        )
        .select_related("schema")
        .order_by("schema__name", "-number"),
        "jobs": available_jobs(request.user, workspace_id, record)[:50],
        "derivatives": record.derivative_set.all(),
        "changes": record.classificationchange_set.select_related("actor").order_by(
            "-created_at"
        )[:20],
    }
    return render(request, "intelligence/record.html", ctx)


@login_required
@require_POST
def type_correction(request, workspace_id, record_id):
    record = editable_record(request.user, workspace_id, record_id)
    try:
        correct_types(
            request.user,
            record,
            request.POST.getlist("types"),
            request.POST.get("reason", ""),
            int(request.POST.get("expected_version", "0")),
            expected_content_version=int(
                request.POST.get("expected_content_version", "0")
            ),
            expected_analysis_id=forms.UUIDField(required=False).clean(
                request.POST.get("expected_analysis_id")
            ),
        )
    except (ValidationError, ValueError) as exc:
        return render(
            request,
            "intelligence/action_error.html",
            {**context(request, workspace_id), "error": str(exc)},
            status=409,
        )
    return redirect("record-detail", workspace_id=workspace_id, record_id=record_id)


@login_required
@require_POST
def extract_preview(request, workspace_id, record_id):
    record = editable_record(request.user, workspace_id, record_id)
    schema_version = get_object_or_404(
        SchemaVersion,
        schema__in=schemas_for(request.user, workspace_id),
        pk=posted_integer(request, "schema_version"),
    )
    try:
        preview_extraction(request.user, record, schema_version)
    except ValidationError as exc:
        return render(
            request,
            "intelligence/action_error.html",
            {**context(request, workspace_id), "error": str(exc)},
            status=400,
        )
    return redirect("record-detail", workspace_id=workspace_id, record_id=record_id)


@login_required
@require_GET
def field_evidence(request, workspace_id, record_id, job_id, field_key):
    record = get_object_or_404(records_for(request.user, workspace_id), pk=record_id)
    job = get_object_or_404(
        available_jobs(request.user, workspace_id, record), pk=job_id
    )
    field = job.result.get(field_key)
    if not field:
        return HttpResponse("Field is unavailable.", status=404)
    evidence = job.analysis or job.content_version
    passages = []
    for candidate in field.get("candidates", []):
        index = candidate.get("segment")
        if isinstance(index, int) and 0 <= index < len(evidence.segments):
            passages.append(
                {
                    "candidate": candidate,
                    "segment": evidence.segments[index],
                    "index": index,
                }
            )
    return render(
        request,
        "intelligence/evidence.html",
        {
            **context(request, workspace_id),
            "record": record,
            "job": job,
            "field_key": field_key,
            "field": field,
            "passages": passages,
            "stale": record.version != job.content_version.number
            or record.classification_version != job.classification_version
            or record.current_analysis_id != job.analysis_id,
        },
    )


@login_required
@require_GET
def schema_list(request, workspace_id):
    ctx = context(request, workspace_id)
    ctx["schemas"] = (
        schemas_for(request.user, workspace_id)
        .select_related("owner", "system")
        .prefetch_related("versions")
        .order_by("name")
    )
    return render(request, "intelligence/schemas.html", ctx)


@login_required
def schema_edit(request, workspace_id, schema_id=None):
    member(request.user, workspace_id, write=True)
    schema = (
        get_object_or_404(schemas_for(request.user, workspace_id), pk=schema_id)
        if schema_id
        else None
    )
    latest = schema.versions.order_by("-number").first() if schema else None
    initial = (
        {
            "name": schema.name,
            "key": schema.key,
            "scope": schema.scope,
            "system": schema.system_id,
            "expected_version": latest.number if latest else 0,
            "fields": json.dumps(latest.fields, indent=2) if latest else "[]",
        }
        if schema
        else {"expected_version": 0, "scope": "type"}
    )
    form = SchemaForm(
        request.POST or None,
        initial=initial,
        actor=request.user,
        workspace_id=workspace_id,
    )
    if request.method == "POST" and form.is_valid():
        values = form.cleaned_data
        try:
            schema, _ = save_schema(
                request.user,
                workspace_id,
                values["name"],
                values["key"],
                values["scope"],
                values["fields"],
                schema_id=schema_id,
                expected_version=values["expected_version"],
                system_id=values["system"].pk if values["system"] else None,
            )
            return redirect(
                "schema-detail", workspace_id=workspace_id, schema_id=schema.pk
            )
        except ValidationError as exc:
            form.add_error(None, str(exc))
    return render(
        request,
        "intelligence/schema_form.html",
        {
            **context(request, workspace_id),
            "form": form,
            "schema": schema,
            "latest": latest,
        },
    )


@login_required
@require_GET
def schema_detail(request, workspace_id, schema_id):
    schema = get_object_or_404(
        schemas_for(request.user, workspace_id).select_related("owner", "system"),
        pk=schema_id,
    )
    return render(
        request,
        "intelligence/schema.html",
        {
            **context(request, workspace_id),
            "schema": schema,
            "versions": schema.versions.order_by("-number"),
            "dependent_system": schema.system if schema.system_id else None,
        },
    )


@login_required
@require_GET
def duplicates(request, workspace_id, record_id):
    record = get_object_or_404(records_for(request.user, workspace_id), pk=record_id)
    candidates = duplicate_candidates(request.user, record)
    visible = records_for(request.user, workspace_id)
    decisions = record.duplicate_left.filter(
        right__in=visible
    ) | record.duplicate_right.filter(left__in=visible)
    return render(
        request,
        "intelligence/duplicates.html",
        {
            **context(request, workspace_id),
            "record": record,
            **candidates,
            "decisions": decisions.select_related("left", "right", "actor"),
        },
    )


@login_required
@require_POST
def duplicate_action(request, workspace_id, record_id):
    left = get_object_or_404(records_for(request.user, workspace_id), pk=record_id)
    right = get_object_or_404(
        records_for(request.user, workspace_id), pk=posted_uuid(request, "other")
    )
    try:
        decide_duplicate(
            request.user,
            left,
            right,
            request.POST.get("decision"),
            request.POST.get("reason", ""),
        )
    except ValidationError as exc:
        return render(
            request,
            "intelligence/action_error.html",
            {**context(request, workspace_id), "error": str(exc)},
            status=400,
        )
    return redirect("duplicates", workspace_id=workspace_id, record_id=record_id)


EXPORT_FIELDS = {
    "id",
    "title",
    "body",
    "version",
    "source",
    "format",
    "language",
    "metadata",
    "summary",
    "types",
    "originals",
}


def export_preview(actor, workspace_id, params):
    requested = params.getlist("fields") or ["id", "title", "body", "version"]
    selected = sorted(set(requested) & EXPORT_FIELDS)
    excluded = [
        {"field": f, "reason": "Unsupported or restricted field"}
        for f in requested
        if f not in EXPORT_FIELDS
    ]
    readable = filtered_records(actor, workspace_id, params)
    if params.getlist("ids"):
        readable = readable.filter(pk__in=params.getlist("ids")[:100])
    permitted = records_for(actor, workspace_id, export=True)
    candidates = list(readable[:101])
    if len(candidates) > 100:
        raise ValidationError(
            "Export at most 100 records; narrow the filters or select IDs."
        )
    allowed_ids = set(
        permitted.filter(pk__in=[r.pk for r in candidates]).values_list("pk", flat=True)
    )
    rows = []
    for record in candidates:
        if len(rows) >= 100:
            raise ValidationError(
                "Export at most 100 records; narrow the filters or select IDs."
            )
        if record.pk not in allowed_ids:
            rows.append(
                {
                    "record": record,
                    "allowed": False,
                    "reason": "Source does not grant export permission.",
                }
            )
            continue
        revision = record.revisions.filter(number=record.version).first()
        analysis = record.current_analysis or revision
        values = {
            "id": str(record.pk),
            "title": record.title,
            "body": record.body,
            "version": record.version,
            "source": str(record.source_id),
            "format": revision.media_type if revision else "unknown",
            "language": revision.language if revision else "unknown",
            "metadata": revision.metadata if revision else {},
            "summary": analysis.summary if analysis else {},
            "types": list(record.type_assignments.values_list("key", flat=True))
            or ["unknown"],
        }
        originals = []
        original_exclusions = []
        if "originals" in selected:
            for capture in record.captures.filter(content_version=revision).order_by(
                "-created_at"
            )[:10]:
                if capture.status == "quarantined":
                    original_exclusions.append(
                        "Quarantined original cannot be exported."
                    )
                else:
                    originals.append(
                        reverse("original-file", args=[workspace_id, capture.pk])
                    )
            values["originals"] = originals
        rows.append(
            {
                "record": record,
                "allowed": True,
                "values": {k: values[k] for k in selected},
                "reason": "; ".join(original_exclusions),
            }
        )
    return {"rows": rows, "selected": selected, "excluded": excluded}


@login_required
@require_GET
def record_export(request, workspace_id):
    ctx = context(request, workspace_id)
    try:
        preview = export_preview(request.user, workspace_id, request.GET)
    except (ValidationError, ValueError) as exc:
        return render(
            request,
            "intelligence/action_error.html",
            {**ctx, "error": str(exc)},
            status=400,
        )
    if request.GET.get("preview") == "1" and request.GET.get("download") != "1":
        return render(
            request,
            "intelligence/export.html",
            {
                **ctx,
                **preview,
                "download_query": request.GET.urlencode() + "&download=1",
                "fields": sorted(EXPORT_FIELDS),
                "filter_params": [
                    (k, v)
                    for k in request.GET
                    for v in request.GET.getlist(k)
                    if k not in {"fields", "preview", "download", "export_format"}
                ],
            },
        )
    kind = request.GET.get("export_format", "json")
    if kind not in {"json", "csv"}:
        return JsonResponse({"error": "Choose json or csv."}, status=400)
    rows = [row["values"] for row in preview["rows"] if row["allowed"]]
    audit(
        request.user,
        workspace_id,
        "records.exported",
        workspace_id,
        {
            "format": kind,
            "filters": {
                k: request.GET.getlist(k)
                for k in request.GET
                if k not in {"download", "format"}
            },
            "fields": preview["selected"],
            "versions": [
                {"id": str(r["record"].pk), "version": r["record"].version}
                for r in preview["rows"]
                if r["allowed"]
            ],
        },
    )
    if kind == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=preview["selected"])
        writer.writeheader()
        for row in rows:
            safe = {}
            for k, v in row.items():
                value = (
                    json.dumps(v, ensure_ascii=False)
                    if isinstance(v, (dict, list))
                    else str(v)
                )
                safe[k] = (
                    "'" + value
                    if value.lstrip().startswith(("=", "+", "-", "@"))
                    else value
                )
            writer.writerow(safe)
        response = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
    else:
        response = JsonResponse(
            {
                "records": rows,
                "excluded_fields": preview["excluded"],
                "excluded_records": sum(not r["allowed"] for r in preview["rows"]),
            }
        )
    response["Content-Disposition"] = f'attachment; filename="records.{kind}"'
    return response


@login_required
@require_POST
def classify(request, workspace_id, record_id):
    from .content import classify_record

    record = editable_record(request.user, workspace_id, record_id)
    try:
        classify_record(request.user, record)
    except ValidationError as exc:
        return render(
            request,
            "intelligence/action_error.html",
            {**context(request, workspace_id), "error": str(exc)},
            status=409,
        )
    return redirect("record-detail", workspace_id=workspace_id, record_id=record_id)
