import json
import sys

from django.core.management.base import BaseCommand, CommandError

from workspaces.portal_permissions import dispatch


class Command(BaseCommand):
    help = "Reviewed OS-local Portal permission bridge (default off)"

    def handle(self, *args, **options):
        try:
            payload = json.loads(sys.stdin.read(65537))
            if not isinstance(payload, dict):
                raise TypeError
            result = dispatch(payload)
        except Exception:  # noqa: BLE001 — never expose native request details at the process boundary
            raise CommandError("Native permission request rejected.") from None
        self.stdout.write(json.dumps(result, default=str))
