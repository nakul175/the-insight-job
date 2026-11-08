class PrivateResponseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Cache-Control"] = "no-store, private"
        response["Pragma"] = "no-cache"
        response["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; img-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return response


class WorkspaceSessionGuard:
    """Recheck native membership on every workspace request; no cached role."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        from django.http import HttpResponse

        from .models import Membership

        workspace_id = view_kwargs.get("workspace_id")
        if not workspace_id or not request.user.is_authenticated:
            return None
        membership = (
            Membership.objects.select_related("workspace")
            .filter(workspace_id=workspace_id, user=request.user, active=True)
            .first()
        )
        if membership is None:
            return HttpResponse("Workspace access unavailable.", status=404)
        key = "workspace_generation_" + str(workspace_id)
        seen = request.session.get(key)
        if seen is not None and seen != membership.generation:
            return HttpResponse("Sign in again to use this workspace.", status=401)
        if seen is None:
            request.session[key] = membership.generation
        request.workspace_policy_url = f"/workspaces/{workspace_id}/permission-status/"
        request.workspace_policy_stamp = (
            f"{membership.workspace.permission_revision}:{membership.role}"
        )
        return None
