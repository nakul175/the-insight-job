"""Exact Accounts identity and current-session enforcement for an opted-in site."""

import time

from django.conf import settings
from django.contrib.auth import login, logout
from django.http import HttpResponse, HttpResponseRedirect
from django.views.decorators.http import require_GET, require_POST

from . import suite_oidc as oidc
from .models import PortalIdentity


def deny():
    return HttpResponse("Access unavailable. Sign in through Aplyd again.", status=401)


def mapped_user(subject, local_id):
    rows = list(
        PortalIdentity.objects.filter(
            issuer=settings.APLYD_ACCOUNTS_ISSUER, subject=subject
        ).select_related("user")
    )
    if (
        not rows
        or any(not row.review_reference.strip() for row in rows)
        or {str(row.user_id) for row in rows} != {local_id}
    ):
        raise oidc.OIDCError("Reviewed mapping unavailable")
    user = rows[0].user
    if not user.is_active or user.is_staff or user.is_superuser:
        raise oidc.OIDCError("Native account unavailable")
    return user


@require_GET
def start(request):
    if not settings.APLYD_SOURCE_OIDC_ENABLED:
        return HttpResponse(status=404)
    try:
        url, pending = oidc.authorization_url()
        request.session.cycle_key()
        request.session["source_oidc_pending"] = pending | {"created": time.time()}
        return HttpResponseRedirect(url)
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
        ):
            raise oidc.OIDCError("Login proof unavailable")
        profile = oidc.exchange_code(
            code=request.GET["code"], state=request.GET["state"], pending=pending
        )
        rows = list(
            PortalIdentity.objects.filter(
                issuer=profile["issuer"], subject=profile["sub"]
            ).values_list("user_id", flat=True)
        )
        if not rows or len(set(rows)) != 1:
            raise oidc.OIDCError("Mapping unavailable")
        native_id = str(rows[0])
        user = mapped_user(profile["sub"], native_id)
        if not oidc.current_session(
            sub=profile["sub"], uid=profile["uid"], local_id=native_id
        ):
            raise oidc.OIDCError("Session unavailable")
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session["source_suite"] = {
            "sub": profile["sub"],
            "uid": profile["uid"],
            "local_id": native_id,
            "issuer": profile["issuer"],
            "id_token": profile["id_token"],
        }
        return HttpResponseRedirect("/workspaces/")
    except (oidc.OIDCError, ValueError, TypeError, KeyError):
        logout(request)
        return deny()


@require_POST
def end(request):
    identity = request.session.get("source_suite", {})
    logout(request)
    if settings.APLYD_SOURCE_OIDC_ENABLED:
        try:
            return HttpResponseRedirect(
                oidc.logout_url(id_token=identity.get("id_token", ""))
            )
        except (oidc.OIDCError, AttributeError):
            return deny()
    return HttpResponseRedirect("/sign-in/")


class SuiteSessionGuard:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            settings.APLYD_SOURCE_OIDC_ENABLED
            and request.user.is_authenticated
            and request.path
            not in ("/auth/callback/", "/auth/login/", "/sign-in/", "/sign-out/")
        ):
            identity = request.session.get("source_suite")
            try:
                if (
                    not isinstance(identity, dict)
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
                    raise oidc.OIDCError("Session ended")
            except (oidc.OIDCError, KeyError, TypeError, ValueError):
                logout(request)
                return deny()
        return self.get_response(request)
