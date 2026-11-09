from django.conf import settings


def context(request):
    return {"aplyd_source_sso_enabled": settings.APLYD_SOURCE_OIDC_ENABLED}
