import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-not-a-production-secret")
os.environ.setdefault("DJANGO_DEBUG", "true")
from .settings import *  # noqa: F403 — environment must be set before settings import

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
