import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY before starting the application.")
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "workspaces",
    "axes",
    "intelligence",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "workspaces.middleware.PrivateResponseMiddleware",
    "workspaces.middleware.WorkspaceSessionGuard",
    "axes.middleware.AxesMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "workspaces.suite_context.context",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
APLYD_SOURCE_OIDC_ENABLED = os.environ.get("APLYD_SOURCE_OIDC_ENABLED") == "1"
APLYD_ACCOUNTS_ISSUER = os.environ.get(
    "APLYD_ACCOUNTS_ISSUER", "https://accounts.aplyd.org"
).rstrip("/")
APLYD_SOURCE_ORIGIN = os.environ.get("APLYD_SOURCE_ORIGIN", "").rstrip("/")
APLYD_SOURCE_CLIENT_ID = os.environ.get("APLYD_SOURCE_CLIENT_ID", "sourcealmighty")
APLYD_SOURCE_CLIENT_SECRET = os.environ.get("APLYD_SOURCE_CLIENT_SECRET", "")
APLYD_SOURCE_BINDINGS_FILE = os.environ.get("APLYD_SOURCE_BINDINGS_FILE", "")
MIDDLEWARE.insert(
    MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware") + 1,
    "workspaces.suite_session.SuiteSessionGuard",
)
db_url = os.environ.get("DATABASE_URL")
if not db_url and not DEBUG:
    raise ImproperlyConfigured("DATABASE_URL is required outside local development.")
DATABASES = {
    "default": dj_database_url.parse(
        db_url or f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=60
    )
}
if not DEBUG and DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured("Production requires PostgreSQL, not ephemeral SQLite.")
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"django.contrib.auth.password_validation.{v}"}
    for v in [
        "UserAttributeSimilarityValidator",
        "MinimumLengthValidator",
        "CommonPasswordValidator",
        "NumericPasswordValidator",
    ]
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/sign-in/"
LOGIN_REDIRECT_URL = "/workspaces/"
LOGOUT_REDIRECT_URL = "/sign-in/"
SESSION_COOKIE_AGE = 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# Enable only behind a proxy which strips and rewrites this header.
if os.environ.get("TRUST_PROXY_HTTPS") == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = [
    x for x in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if x
]
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = False

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
AXES_RESET_ON_SUCCESS = True
AXES_CLIENT_IP_CALLABLE = "workspaces.security.direct_client_ip"
AXES_LOCKOUT_TEMPLATE = "registration/locked.html"
AXES_SENSITIVE_PARAMETERS = ["username", "password", "ip_address"]

# Separate explicit deployment gates; all disabled unless provisioned.
APLYD_SOURCE_WRITER_ENABLED = os.environ.get("APLYD_SOURCE_WRITER_ENABLED") == "1"

APLYD_SOURCE_SCOPES_ENABLED = os.environ.get("APLYD_SOURCE_SCOPES_ENABLED") == "1"

# Staging-first, explicit per-installation central authentication and creation gates.
APLYD_SOURCE_PROVISIONING_ENABLED = (
    os.environ.get("APLYD_SOURCE_PROVISIONING_ENABLED") == "1"
)

APLYD_SOURCE_ENROLLMENT_ENABLED = (
    os.environ.get("APLYD_SOURCE_ENROLLMENT_ENABLED") == "1"
)

# Choose one reviewed authority; never fall back between mapping stores.
APLYD_SOURCE_IDENTITY_SOURCE = os.environ.get("APLYD_SOURCE_IDENTITY_SOURCE", "")
