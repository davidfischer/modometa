"""Management command to download and ingest Scryfall Default Cards bulk data."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.pipeline.scryfall import download_default_cards
from core.pipeline.scryfall import ingest_scryfall_cards


class Command(BaseCommand):
    help = (
        "Download and ingest Scryfall Default Cards bulk data (capturing all prints, "
        "original printing art, and alternate names like Through the Omenpaths)"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            help="Path to an existing default-cards.jsonl or .gz file instead of downloading",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force re-download even if local cache exists",
        )

    def handle(self, *args, **options):
        cache_path = (
            Path(settings.BASE_DIR) / "data" / "scryfall_default_cards.jsonl.gz"
        )
        if options["file"]:
            json_file = Path(options["file"])
        else:
            self.stdout.write(
                "Checking/downloading Scryfall Default Cards bulk data..."
            )
            json_file = download_default_cards(cache_path, force=options["force"])

        self.stdout.write(f"Ingesting cards and aliases from {json_file}...")
        cards_count, lookups_count = ingest_scryfall_cards(json_file)
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully synced Scryfall data: {cards_count} unique cards (with original art), {lookups_count} lookups."
            )
        )
