import base64
import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .access import (
    NeedsAccess,
    begin_login,
    confirm_changed_identity,
    disconnect,
    handoff_permission,
    rotate_api,
    submit_browser_action,
)
from .collector import fetch_public
from .models import BrowserHandoff, Capture, Connection
from .permissions import member, runs_for, sources_for
from .runs import cancel_run, start_run
from .services import update_source_config
from .vault import unseal
from .views import context, source_for


@login_required
def connection_setup(request, workspace_id, source_id):
    ctx = context(request, workspace_id)
    member(request.user, workspace_id, write=True)
    source = source_for(request, workspace_id, source_id)
    error = None
    if request.method == "POST":
        try:
            if request.POST.get("method") == "api":
                from urllib.parse import urlsplit

                connection = Connection.objects.create(
                    workspace=source.workspace,
                    owner=request.user,
                    bound_source=source,
                    name=source.name + " API",
                    domain=urlsplit(source.url).hostname,
                    method="api",
                )
                rotate_api(
                    request.user,
                    connection,
                    request.POST.get("secret_ref", ""),
                    source,
                    fetch_public,
                )
                update_source_config(
                    request.user,
                    source,
                    {"access": "api", "connection_id": str(connection.pk)},
                )
                return redirect(
                    "source-detail", workspace_id=workspace_id, source_id=source_id
                )
            handoff = begin_login(
                request.user,
                source,
                request.POST.get("login_url", ""),
                request.POST.get("identity_selector", ""),
                request.POST.get("expected_identity", ""),
            )
            update_source_config(
                request.user,
                source,
                {"access": "session", "connection_id": str(handoff.connection_id)},
            )
            return redirect(
                "browser-handoff", workspace_id=workspace_id, handoff_id=handoff.pk
            )
        except (ValidationError, ImproperlyConfigured) as exc:
            error = str(exc)
    ctx.update(
        source=source,
        error=error,
        connections=Connection.objects.filter(
            workspace_id=workspace_id, owner=request.user, bound_source=source
        ),
    )
    return render(request, "intelligence/connections.html", ctx)


@login_required
@require_POST
def connection_action(request, workspace_id, connection_id, action):
    connection = get_object_or_404(
        Connection, pk=connection_id, workspace_id=workspace_id, owner=request.user
    )
    member(request.user, workspace_id, write=True)
    try:
        if action == "disconnect":
            disconnect(request.user, connection)
        elif action == "confirm":
            confirm_changed_identity(request.user, connection)
        elif action == "rotate":
            source = source_for(request, workspace_id, request.POST.get("source_id"))
            rotate_api(
                request.user,
                connection,
                request.POST.get("secret_ref", ""),
                source,
                fetch_public,
            )
        else:
            return JsonResponse({"error": "Unknown action"}, status=400)
    except ValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return redirect("overview", workspace_id=workspace_id)


@login_required
@require_GET
def handoff_view(request, workspace_id, handoff_id):
    handoff = get_object_or_404(
        BrowserHandoff, pk=handoff_id, workspace_id=workspace_id, owner=request.user
    )
    member(request.user, workspace_id, write=True)
    return render(
        request,
        "intelligence/browser.html",
        {**context(request, workspace_id), "handoff": handoff},
    )


@login_required
@require_GET
def handoff_state(request, workspace_id, handoff_id):
    handoff = get_object_or_404(
        BrowserHandoff, pk=handoff_id, workspace_id=workspace_id, owner=request.user
    )
    member(request.user, workspace_id, write=True)
    return JsonResponse(
        {
            "state": "expired"
            if handoff.expires_at <= timezone.now()
            else handoff.state,
            "connection_state": handoff.connection.state,
            "domain": handoff.connection.domain,
            "reason": handoff.reason,
            "last_check": handoff.connection.checked_at,
        }
    )


@login_required
@require_GET
def handoff_frame(request, workspace_id, handoff_id):
    handoff = get_object_or_404(
        BrowserHandoff, pk=handoff_id, workspace_id=workspace_id, owner=request.user
    )
    try:
        handoff_permission(request.user, handoff)
    except ValidationError:
        return HttpResponse(status=410)
    if not handoff.frame_ciphertext:
        return HttpResponse(status=204)
    return HttpResponse(
        base64.b64decode(unseal(handoff.frame_ciphertext)["frame"]),
        content_type="image/jpeg",
    )


@login_required
@require_POST
def handoff_action(request, workspace_id, handoff_id):
    handoff = get_object_or_404(
        BrowserHandoff, pk=handoff_id, workspace_id=workspace_id, owner=request.user
    )
    try:
        if len(request.body) > 10000:
            raise ValidationError("Browser action is too large.")
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValidationError("Invalid browser action.")
        submit_browser_action(
            request.user, handoff, payload.get("kind"), payload.get("payload", {})
        )
    except (ValidationError, ValueError, TypeError, KeyError):
        return JsonResponse(
            {"error": "This browser action is unavailable or invalid."}, status=400
        )
    return JsonResponse({"queued": True})


@login_required
@require_POST
def run_start(request, workspace_id, source_id):
    source = source_for(request, workspace_id, source_id)
    try:
        item, _created = start_run(
            request.user, source, request.POST.get("request_key", "")
        )
    except (ValidationError, NeedsAccess) as exc:
        return render(
            request,
            "intelligence/action_error.html",
            {**context(request, workspace_id), "error": str(exc)},
            status=400,
        )
    return redirect("run-detail", workspace_id=workspace_id, run_id=item.pk)


@login_required
@require_GET
def run_detail(request, workspace_id, run_id):
    item = get_object_or_404(
        runs_for(request.user, workspace_id),
        pk=run_id,
    )
    return render(
        request,
        "intelligence/run.html",
        {
            **context(request, workspace_id),
            "run": item,
            "captures": Capture.objects.filter(run=item),
        },
    )


@login_required
@require_POST
def run_cancel(request, workspace_id, run_id):
    item = get_object_or_404(
        runs_for(request.user, workspace_id),
        pk=run_id,
    )
    cancel_run(request.user, item)
    return redirect("run-detail", workspace_id=workspace_id, run_id=run_id)


@login_required
@require_GET
def original_file(request, workspace_id, capture_id):
    capture = get_object_or_404(
        Capture,
        pk=capture_id,
        workspace_id=workspace_id,
        source__in=sources_for(request.user, workspace_id, export=True),
    )
    if capture.status == "quarantined":
        return HttpResponse(
            "This original is quarantined and cannot be downloaded through this viewer.",
            status=403,
        )
    response = HttpResponse(
        bytes(capture.original), content_type="application/octet-stream"
    )
    response["Content-Disposition"] = 'attachment; filename="original.bin"'
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return response
