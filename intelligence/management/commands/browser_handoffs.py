import time
from contextlib import contextmanager

from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.http import Http404
from django.utils import timezone

from intelligence.browser_worker import process_handoff
from intelligence.models import BrowserAction, BrowserHandoff


@contextmanager
def singleton_worker():
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(848250302)")
            if not cursor.fetchone()[0]:
                raise CommandError(
                    "An isolated-browser worker already owns this database."
                )
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(848250302)")
    else:
        import fcntl
        from pathlib import Path

        from django.conf import settings

        folder = Path(settings.BASE_DIR) / ".runtime"
        folder.mkdir(exist_ok=True)
        with (folder / "browser-worker.lock").open("w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CommandError(
                    "A local browser worker already holds this workspace."
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


class Command(BaseCommand):
    help = "Run one isolated login-browser worker with database/file ownership, configured encryption keys and Chromium sandbox support."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        with singleton_worker():
            stale = BrowserHandoff.objects.filter(state="active")
            BrowserAction.objects.filter(handoff__in=stale).delete()
            stale.update(
                state="failed",
                frame_ciphertext="",
                reason="Browser worker restarted; reconnect.",
            )
            while True:
                expired = BrowserHandoff.objects.filter(
                    state__in=["pending", "active"], expires_at__lte=timezone.now()
                )
                BrowserAction.objects.filter(handoff__in=expired).delete()
                expired.update(state="expired", frame_ciphertext="")
                handoff = (
                    BrowserHandoff.objects.filter(
                        state="pending", expires_at__gt=timezone.now()
                    )
                    .order_by("created_at")
                    .first()
                )
                if handoff:
                    try:
                        process_handoff(handoff)
                    except (
                        PermissionDenied,
                        ValidationError,
                        Http404,
                        ImproperlyConfigured,
                    ):
                        BrowserAction.objects.filter(handoff=handoff).delete()
                        handoff.state = "failed"
                        handoff.reason = "Access or browser configuration no longer permits this session."
                        handoff.frame_ciphertext = ""
                        handoff.save(
                            update_fields=["state", "reason", "frame_ciphertext"]
                        )
                if options["once"]:
                    break
                time.sleep(1)
