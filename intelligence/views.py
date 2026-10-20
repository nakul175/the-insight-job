from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from workspaces.models import Membership

from .forms import InviteForm, ScopeForm, SourceForm
from .models import Schedule, SourceTest, WorkItem
from .permissions import member, records_for, sources_for, systems_for
from .services import (
    accept_invitation,
    archive_source,
    audit,
    change_role,
    invite,
    register_source,
    revoke_membership,
    revoke_source,
    update_source_config,
)
from .source_testing import activate, test_source
from .urlsafety import scope_decision


def context(request, workspace_id):
    membership = member(request.user, workspace_id)
    return {"workspace": membership.workspace, "membership": membership}


def source_for(request, workspace_id, source_id):
    return get_object_or_404(sources_for(request.user, workspace_id), pk=source_id)


@login_required
@require_GET
def overview(request, workspace_id):
    ctx = context(request, workspace_id)
    now = timezone.now()
    start = now - timedelta(days=7)
    try:
        sources = sources_for(request.user, workspace_id)
        records = records_for(request.user, workspace_id)
        recent = records.filter(created_at__gte=start)
        ctx.update(
            source_count=sources.count(),
            record_count=recent.count(),
            issue_count=recent.filter(status="review").count(),
            start=start,
            as_of=now,
        )
    except DatabaseError:
        ctx.update(load_error=True, as_of=now)
        return render(request, "intelligence/overview.html", ctx, status=503)
    return render(request, "intelligence/overview.html", ctx)


@login_required
@require_GET
def source_list(request, workspace_id):
    ctx = context(request, workspace_id)
    ctx["sources"] = sources_for(request.user, workspace_id)
    return render(request, "intelligence/sources.html", ctx)


@login_required
def source_create(request, workspace_id):
    ctx = context(request, workspace_id)
    member(request.user, workspace_id, write=True)
    form = SourceForm(request.POST or None, workspace=ctx["workspace"])
    if request.method == "POST" and form.is_valid():
        try:
            source, _created = register_source(
                request.user,
                workspace_id,
                form.cleaned_data["name"],
                form.cleaned_data["url"],
                form.cleaned_data["owner"].user,
            )
            if (
                not sources_for(request.user, workspace_id)
                .filter(pk=source.pk)
                .exists()
            ):
                raise PermissionDenied()
            return redirect(
                "source-detail", workspace_id=workspace_id, source_id=source.pk
            )
        except ValidationError as exc:
            form.add_error(None, exc)
    ctx.update(
        form=form,
        title="Register a source",
        help="Sources start in Draft. Configure access and scope, then run a bounded sample before activation.",
    )
    return render(request, "intelligence/form.html", ctx)


@login_required
def source_detail(request, workspace_id, source_id):
    ctx = context(request, workspace_id)
    source = source_for(request, workspace_id, source_id)
    version = source.versions.get(version=source.config_version)
    initial = {
        **version.config,
        "page_limit": version.config.get("limits", {}).get("pages", 10),
        "depth_limit": version.config.get("limits", {}).get("depth", 2),
        "time_limit_seconds": version.config.get("limits", {}).get("seconds", 180),
        "byte_limit": version.config.get("limits", {}).get("bytes", 10000000),
        "include": "\n".join(version.config.get("include", [])),
        "exclude": "\n".join(version.config.get("exclude", [])),
        "connection": version.config.get("connection_id"),
    }
    form = ScopeForm(
        request.POST or None, initial=initial, workspace=ctx["workspace"], source=source
    )
    if request.method == "POST":
        member(request.user, workspace_id, write=True)
        if form.is_valid():
            d = form.cleaned_data
            try:
                update_source_config(
                    request.user,
                    source,
                    {
                        "include": d["include"].splitlines(),
                        "exclude": d["exclude"].splitlines(),
                        "access": d["access"],
                        "connection_id": str(d["connection"].pk)
                        if d["connection"]
                        else None,
                        "partial_policy": d["partial_policy"],
                        "partial_reason": d["partial_reason"],
                        "limits": {
                            "pages": d["page_limit"],
                            "depth": d["depth_limit"],
                            "seconds": d["time_limit_seconds"],
                            "bytes": d["byte_limit"],
                            "attempts": 3,
                            "provider_cost_microunits": 0,
                        },
                    },
                )
                return redirect(
                    "source-detail", workspace_id=workspace_id, source_id=source_id
                )
            except ValidationError as exc:
                form.add_error(None, exc)
    ctx.update(
        source=source,
        form=form,
        config=version.config,
        tests=source.tests.filter(source_version=version).order_by("-created_at"),
        active_runs=WorkItem.objects.filter(
            source=source, state__in=["queued", "running"]
        ).count(),
        schedules=Schedule.objects.filter(source=source, enabled=True).count(),
        dependent_systems=source.systemsourcegrant_set.filter(
            active=True, system__in=systems_for(request.user, workspace_id)
        ).select_related("system"),
    )
    return render(request, "intelligence/source.html", ctx)


@login_required
@require_POST
def source_action(request, workspace_id, source_id, action):
    source = source_for(request, workspace_id, source_id)
    member(request.user, workspace_id, write=True)
    try:
        if action == "test":
            test_source(request.user, source)
        elif action == "activate":
            activate(request.user, source, request.POST.get("test_id"))
        elif action == "archive":
            archive_source(request.user, source, request.POST.get("policy"))
        elif action == "restore":
            source.state = "draft"
            source.archive_policy = ""
            source.save(update_fields=["state", "archive_policy"])
            audit(request.user, workspace_id, "source.restored", source.pk)
        elif action == "revoke":
            revoke_source(
                request.user, workspace_id, source_id, request.POST.get("system_id")
            )
        else:
            raise Http404()
    except (ValidationError, SourceTest.DoesNotExist) as exc:
        return render(
            request,
            "intelligence/action_error.html",
            {**context(request, workspace_id), "error": str(exc)},
            status=400,
        )
    return redirect("source-detail", workspace_id=workspace_id, source_id=source_id)


@login_required
@require_POST
def scope_preview(request, workspace_id, source_id):
    source = source_for(request, workspace_id, source_id)
    config = source.versions.get(version=source.config_version).config
    urls = request.POST.get("urls", "").splitlines()
    if len(urls) > 20:
        return JsonResponse({"error": "Preview at most 20 URLs."}, status=400)
    results = []
    for url in urls:
        try:
            allowed, reason = scope_decision(source.url, url, config)
        except ValidationError as exc:
            allowed, reason = False, str(exc)
        results.append({"url": url, "included": allowed, "rule": reason})
    return JsonResponse({"version": source.config_version, "results": results})


@login_required
def members(request, workspace_id):
    ctx = context(request, workspace_id)
    member(request.user, workspace_id, admin=True)
    form = InviteForm(
        request.POST or None,
        sources=sources_for(request.user, workspace_id),
        systems=systems_for(request.user, workspace_id),
    )
    token = None
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        _invitation, token = invite(
            request.user,
            workspace_id,
            d["email"],
            d["role"],
            d["sources"],
            d["systems"],
            d["can_export"],
        )
    ctx.update(
        form=form,
        invite_token=token,
        members=Membership.objects.filter(workspace_id=workspace_id).select_related(
            "user"
        ),
        invite_path=reverse("accept-invitation") if token else None,
    )
    return render(request, "intelligence/members.html", ctx)


@login_required
@require_POST
def membership_action(request, workspace_id, membership_id, action):
    member(request.user, workspace_id, admin=True)
    get_object_or_404(Membership, pk=membership_id, workspace_id=workspace_id)
    try:
        if action == "role":
            change_role(
                request.user, workspace_id, membership_id, request.POST.get("role")
            )
        elif action == "revoke":
            revoke_membership(request.user, workspace_id, membership_id)
        else:
            raise Http404()
    except ValidationError as exc:
        return render(
            request,
            "intelligence/action_error.html",
            {**context(request, workspace_id), "error": str(exc)},
            status=400,
        )
    return redirect("members", workspace_id=workspace_id)


@login_required
def accept(request):
    if request.method == "POST":
        try:
            m = accept_invitation(request.user, request.POST.get("token", ""))
            return redirect("workspace", workspace_id=m.workspace_id)
        except ValidationError as exc:
            return render(
                request,
                "intelligence/action_error.html",
                {"error": str(exc)},
                status=400,
            )
    return render(request, "intelligence/accept.html")


@login_required
@require_GET
def system_detail(request, workspace_id, system_id):
    system = get_object_or_404(systems_for(request.user, workspace_id), pk=system_id)
    return render(
        request,
        "intelligence/system.html",
        {**context(request, workspace_id), "system": system},
    )


@login_required
@require_GET
def system_api(request, workspace_id, system_id):
    system = get_object_or_404(systems_for(request.user, workspace_id), pk=system_id)
    return JsonResponse(
        {"id": str(system.pk), "name": system.name, "state": system.state}
    )


@login_required
@require_GET
def record_list(request, workspace_id):
    ctx = context(request, workspace_id)
    qs = records_for(request.user, workspace_id)
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET["status"])
    if request.GET.get("q"):
        qs = qs.filter(title__icontains=request.GET["q"])
    if request.GET.get("window") == "7d":
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=7))
    ctx["records"] = qs.order_by("-created_at")[:100]
    return render(request, "intelligence/records.html", ctx)


@login_required
@require_GET
def record_detail(request, workspace_id, record_id):
    record = get_object_or_404(records_for(request.user, workspace_id), pk=record_id)
    return render(
        request,
        "intelligence/record.html",
        {
            **context(request, workspace_id),
            "record": record,
            "derivatives": record.derivative_set.all(),
        },
    )


@login_required
@require_GET
def record_file(request, workspace_id, record_id):
    record = get_object_or_404(
        records_for(request.user, workspace_id, export=True), pk=record_id
    )
    response = HttpResponse(record.body, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="capture.txt"'
    return response


@login_required
@require_GET
def record_export(request, workspace_id):
    qs = records_for(request.user, workspace_id, export=True)
    if request.GET.get("q"):
        qs = qs.filter(title__icontains=request.GET["q"])
    rows = list(qs.values("id", "title", "body", "version"))
    audit(
        request.user,
        workspace_id,
        "records.exported",
        workspace_id,
        {"count": len(rows)},
    )
    response = JsonResponse({"records": rows})
    response["Content-Disposition"] = 'attachment; filename="records.json"'
    return response


@login_required
@require_GET
def system_records(request, workspace_id, system_id):
    system = get_object_or_404(systems_for(request.user, workspace_id), pk=system_id)
    grants = system.systemsourcegrant_set.filter(
        active=True, source__workspace_id=workspace_id
    )
    records = records_for(request.user, workspace_id).filter(
        source_id__in=grants.values("source_id")
    )
    return JsonResponse({"records": list(records.values("id", "title", "version"))})
