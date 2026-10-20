from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from intelligence.observability import evaluate_alerts
from intelligence.permissions import member
from intelligence.recovery import expire_snapshots


class Command(BaseCommand):
    help = "Expire declared local snapshots and evaluate in-app alerts for one authorised workspace."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", required=True)
        parser.add_argument("--actor", required=True, type=int)

    def handle(self, *args, **options):
        actor = get_user_model().objects.filter(pk=options["actor"]).first()
        if not actor:
            raise CommandError("Administrator actor unavailable.")
        member(actor, options["workspace"], admin=True)
        expired = expire_snapshots(options["workspace"])
        incidents = evaluate_alerts(actor, options["workspace"])
        self.stdout.write(
            f"Expired snapshots: {expired}; new in-app incidents: {len(incidents)}"
        )
