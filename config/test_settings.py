import os
import tempfile
from pathlib import Path

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-not-a-production-secret")
os.environ.setdefault("DJANGO_DEBUG", "true")
from .settings import *  # noqa: F403 — environment must be set before settings import

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# LiveServer's shared in-memory SQLite wrapper can interleave transactions between
# HTTP/autosave threads. A disposable file gives browser requests independent
# connections, while retaining the same database and all existing assertions.
if os.environ.get("TIJ_BROWSER_TESTS") == "true":
    _browser_database = tempfile.TemporaryDirectory(prefix="insight-job-ui-tests-")
    DATABASES["default"]["TEST"] = {
        "NAME": str(Path(_browser_database.name) / "browser.sqlite3")
    }
