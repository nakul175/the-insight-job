import time

from django.core.management.base import BaseCommand

from intelligence.content import process_extraction
from intelligence.models import ExtractionJob


class Command(BaseCommand):
    help = "Run explicit, version-pinned, bounded typed extraction jobs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            job = (
                ExtractionJob.objects.filter(state="queued", preview=False)
                .order_by("created_at")
                .first()
            )
            if job:
                process_extraction(job.pk)
            if options["once"]:
                break
            if not job:
                time.sleep(1)
