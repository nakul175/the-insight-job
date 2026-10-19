from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.contrib.sessions.models import Session
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .models import Membership, Workspace, WorkspaceSelection


class SignInView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        # Never replay a protected action or honor an untrusted next URL.
        return "/workspaces/"

    def form_valid(self, form):
        response = super().form_valid(form)
        self.request.session.pop("active_workspace", None)
        return response


@require_GET
def home(request):
    return redirect("workspaces" if request.user.is_authenticated else "login")


@login_required
@require_GET
def workspace_list(request):
    workspaces = Workspace.objects.filter(memberships__user=request.user)
    active = request.session.get("active_workspace")
    if active and not workspaces.filter(pk=active).exists():
        request.session.pop("active_workspace", None)
    return render(request, "workspaces/list.html", {"workspaces": workspaces})


@login_required
@require_POST
def select_workspace(request, workspace_id):
    with transaction.atomic():
        # Scope before resolving existence. Staff status never bypasses membership.
        membership = get_object_or_404(
            Membership.objects.select_for_update(),
            user=request.user,
            workspace_id=workspace_id,
        )
        WorkspaceSelection.objects.create(
            user=request.user, workspace_id=membership.workspace_id
        )
        request.session["active_workspace"] = str(membership.workspace_id)
    return redirect("workspace", workspace_id=workspace_id)


@login_required
@require_GET
def workspace_detail(request, workspace_id):
    workspace = get_object_or_404(
        Workspace.objects.filter(memberships__user=request.user), pk=workspace_id
    )
    return render(request, "workspaces/detail.html", {"workspace": workspace})


@require_GET
def readiness(request):
    try:
        # Probe the required schema, not merely the database connection.
        Membership.objects.exists()
        Session.objects.exists()
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})
