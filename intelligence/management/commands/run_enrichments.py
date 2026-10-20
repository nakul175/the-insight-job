from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand
from django.http import Http404

from intelligence.access import NeedsAccess
from intelligence.enrichment import enrichment_step
from intelligence.models import EnrichmentAttempt


class Command(BaseCommand):
    help = "Advance bounded enrichment coverage and enqueue next permitted capture; run_collection handles captures."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        for attempt in EnrichmentAttempt.objects.filter(
            state__in=["queued", "running"]
        ).order_by("created_at")[: max(0, min(100, options["limit"]))]:
            try:
                enrichment_step(attempt.actor, attempt.workspace_id, attempt.pk)
            except (PermissionDenied, ValidationError, Http404, NeedsAccess):
                EnrichmentAttempt.objects.filter(pk=attempt.pk).update(
                    state="blocked",
                    reason="Current permissions or policy no longer allow enrichment; review access.",
                )
