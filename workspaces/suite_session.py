"""OIDC sign-in into an existing native user, with current-session enforcement."""

import time

from django.conf import settings
from django.contrib.auth import login, logout
from django.http import HttpResponse, HttpResponseRedirect
from django.views.decorators.http import require_GET

from . import suite_oidc as oidc
from .suite_identity import mapped_user


def deny():
    response = HttpResponse(
        "Access unavailable. Start Aplyd sign-in again.", status=401
    )
    response["Cache-Control"] = "no-store, private"
    return response


@require_GET
def start(request):
    if not settings.APLYD_SOURCE_OIDC_ENABLED:
        return HttpResponse(status=404)
    try:
        url, pending = oidc.authorization_url()
        request.session.cycle_key()
        request.session["source_oidc_pending"] = pending | {"created": time.time()}
        response = HttpResponseRedirect(url)
        response["Cache-Control"] = "no-store"
        return response
    except oidc.OIDCError:
        return deny()


@require_GET
def callback(request):
    if not settings.APLYD_SOURCE_OIDC_ENABLED:
        return HttpResponse(status=404)
    pending = request.session.pop("source_oidc_pending", None)
    request.session.save()
    try:
        if (
            not isinstance(pending, dict)
            or type(pending.get("created")) not in (int, float)
            or not 0 <= time.time() - pending["created"] <= 300
            or len(request.GET.getlist("code")) != 1
            or len(request.GET.getlist("state")) != 1
            or "error" in request.GET
            or not 1 <= len(request.GET["code"]) <= 4096
            or (
                "iss" in request.GET
                and request.GET.getlist("iss") != [settings.APLYD_ACCOUNTS_ISSUER]
            )
        ):
            raise oidc.OIDCError("Login proof unavailable")
        profile = oidc.exchange_code(
            code=request.GET["code"], state=request.GET["state"], pending=pending
        )
        if profile["issuer"] != settings.APLYD_ACCOUNTS_ISSUER:
            raise oidc.OIDCError("Issuer mismatch")
        user = mapped_user(profile["sub"])
        native = str(user.pk)
        if not oidc.current_session(
            sub=profile["sub"], uid=profile["uid"], local_id=native
        ):
            raise oidc.OIDCError("Current session unavailable")
        identity = {
            "sub": profile["sub"],
            "uid": profile["uid"],
            "local_id": native,
            "issuer": profile["issuer"],
        }
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session.pop("active_workspace", None)
        request.session["source_suite"] = identity
        return HttpResponseRedirect("/workspaces/")
    except (oidc.OIDCError, ValueError, TypeError, KeyError):
        # An unsolicited or invalid callback must neither authenticate a new
        # user nor act as cross-site logout of an existing native session.
        return deny()


class SuiteSessionGuard:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        identity = request.session.get("source_suite")
        # Preserve existing native password sessions. Enforce central proof only
        # for sessions that this receiver established through Accounts.
        if request.user.is_authenticated and identity is not None:
            try:
                if (
                    not settings.APLYD_SOURCE_OIDC_ENABLED
                    or not isinstance(identity, dict)
                    or identity.get("issuer") != settings.APLYD_ACCOUNTS_ISSUER
                    or identity.get("local_id") != str(request.user.pk)
                ):
                    raise oidc.OIDCError("Session binding unavailable")
                mapped_user(identity["sub"], identity["local_id"])
                if not oidc.current_session(
                    sub=identity["sub"],
                    uid=identity["uid"],
                    local_id=identity["local_id"],
                ):
                    raise oidc.OIDCError("Central session ended")
            except (oidc.OIDCError, ValueError, TypeError, KeyError):
                logout(request)
                # A fresh sign-in and local sign-out must remain reachable after
                # expiry; protected requests fail closed without replaying work.
                if request.path not in (
                    "/auth/login/",
                    "/auth/callback/",
                    "/sign-in/",
                    "/sign-out/",
                ):
                    return deny()
        return self.get_response(request)
