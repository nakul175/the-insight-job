from django.core.management.base import BaseCommand
from django.db import DatabaseError
from django.http import Http404
from django.utils import timezone

from intelligence.delivery import deliver
from intelligence.models import DeliveryIntent


class Command(BaseCommand):
    help = "Run bounded local sandbox intents; no HTTP endpoint is contacted."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        ids = (
            DeliveryIntent.objects.filter(
                state__in=["pending", "retry"], next_attempt__lte=timezone.now()
            )
            .order_by("created_at")
            .values_list("pk", flat=True)[: max(0, min(100, options["limit"]))]
        )
        for pk in list(ids):
            try:
                deliver(pk)
            except (DatabaseError, Http404):
                self.stderr.write(
                    "Local intent unavailable; no successful delivery is asserted."
                )
