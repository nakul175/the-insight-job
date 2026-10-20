import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from workspaces.models import Workspace

from .models import UserDraft
from .permissions import member, records_for, sources_for, systems_for


@login_required
@require_http_methods(["GET", "POST", "DELETE"])
@transaction.atomic
def draft(request, workspace_id, scope_kind, scope_id, form_key):
    Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
    key = {
        "workspace_id": workspace_id,
        "actor": request.user,
        "scope_kind": scope_kind,
        "scope_id": scope_id,
        "form_key": form_key,
    }
    try:
        membership = member(request.user, workspace_id)
        if scope_kind == "result":
            from .routing import live_access, results_for

            result = get_object_or_404(
                results_for(request.user, workspace_id), pk=scope_id
            )
            live_access(request.user, result, review=True)
        elif scope_kind == "record":
            if membership.role not in {"administrator", "operator", "reviewer"}:
                raise PermissionDenied()
            get_object_or_404(records_for(request.user, workspace_id), pk=scope_id)
        elif scope_kind == "workspace":
            member(request.user, workspace_id, write=True)
            if str(scope_id) != str(workspace_id):
                raise PermissionDenied()
        elif scope_kind in {"source", "system"}:
            member(request.user, workspace_id, write=True)
            get_object_or_404(
                sources_for(request.user, workspace_id)
                if scope_kind == "source"
                else systems_for(request.user, workspace_id),
                pk=scope_id,
            )
        else:
            raise PermissionDenied()
    except (PermissionDenied, Http404):
        UserDraft.objects.filter(**key).delete()
        return JsonResponse(
            {
                "error": "Current permissions do not allow this draft; no inaccessible draft was retained."
            },
            status=403,
        )
    try:
        if request.method == "DELETE":
            UserDraft.objects.filter(**key).delete()
            return JsonResponse({"state": "removed"})
        if request.method == "GET":
            saved = UserDraft.objects.filter(**key).first()
            return JsonResponse(
                {
                    "payload": saved.payload if saved else {},
                    "saved_at": saved.updated_at.isoformat() if saved else None,
                }
            )
        if len(request.body) > 24000:
            raise ValidationError("Draft is too large.")
        payload = json.loads(request.body)
        if (
            not isinstance(payload, dict)
            or len(payload) > 50
            or any(
                not isinstance(name, str)
                or len(name) > 100
                or any(
                    word in name.lower()
                    for word in [
                        "password",
                        "secret",
                        "token",
                        "cookie",
                        "ciphertext",
                        "connection",
                    ]
                )
                or not isinstance(values, list)
                or len(values) > 100
                or any(
                    not isinstance(value, str) or len(value) > 10000 for value in values
                )
                for name, values in payload.items()
            )
        ):
            raise ValidationError("Draft supports bounded non-secret form fields only.")
        saved, _ = UserDraft.objects.update_or_create(
            **key, defaults={"payload": payload, "updated_at": timezone.now()}
        )
        return JsonResponse(
            {"state": "saved", "saved_at": saved.updated_at.isoformat()}
        )
    except (ValidationError, ValueError, TypeError):
        return JsonResponse(
            {"error": "Invalid draft fields; keep your current input and retry."},
            status=400,
        )
    except DatabaseError:
        return JsonResponse(
            {"error": "Draft could not save. Current input remains unsaved; retry."},
            status=503,
        )
