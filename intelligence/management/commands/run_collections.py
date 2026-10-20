import time

from django.core.management.base import BaseCommand

from intelligence.runs import LeaseLost, claim_next, collect_step


class Command(BaseCommand):
    help = "Execute bounded, leased collection jobs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            item = claim_next()
            if item:
                while item.state == "running":
                    try:
                        item = collect_step(item)
                    except LeaseLost:
                        break
            if options["once"]:
                break
            if not item:
                time.sleep(1)
