"""Management command to ingest MTGO tournament JSON files."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.formats import FORMAT_NAMES
from core.formats import is_valid_format
from core.pipeline.ingest import IngestionPipeline
from core.pipeline.ingest import parse_date


class Command(BaseCommand):
    help = "Ingest MTGO tournament decklists from local MTG_decklistcache"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            type=str,
            default=str(settings.DEFAULT_DECKLIST_DIR),
            help="Directory containing MTGO tournament files",
        )
        parser.add_argument(
            "--format",
            type=str,
            default=None,
            help="Filter by specific format (e.g. legacy, modern, vintage, pioneer, standard, premodern, pauper)",
        )
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            help="Only process tournaments on or after this date (YYYY-MM-DD). Existing tournaments in this date range will be refreshed.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit number of tournament files to process",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-ingest tournaments even if they already exist in the database",
        )
        parser.add_argument(
            "--show-unmatched",
            action="store_true",
            help="Show unrecognized cards as they are encountered and print a summary",
        )
        parser.add_argument(
            "--skip-knn",
            action="store_true",
            help="Skip rebuilding the kNN deck similarity index after ingestion",
        )

    def handle(self, *args, **options):
        base_dir = Path(options["dir"])
        fmt_filter = options["format"]
        limit = options["limit"]
        force = options["force"]
        show_unmatched = options["show_unmatched"]
        since_str = options["since"]

        if fmt_filter:
            fmt_filter = fmt_filter.lower().strip()
            if not is_valid_format(fmt_filter):
                raise CommandError(
                    f"Unsupported format '{fmt_filter}'. Supported formats: {', '.join(sorted(FORMAT_NAMES))}"
                )

        since_date = None
        if since_str:
            since_date = parse_date(since_str)
            if not since_date:
                raise CommandError(
                    f"Invalid date format for --since: '{since_str}'. Expected YYYY-MM-DD (e.g. 2026-08-01)."
                )

        if not base_dir.exists():
            self.stderr.write(f"Directory {base_dir} does not exist!")
            return

        pipeline = IngestionPipeline()
        if since_date:
            self.stdout.write(
                f"Scanning for tournament files in {base_dir} since {since_date} ..."
            )
        else:
            self.stdout.write(f"Scanning for tournament files in {base_dir} ...")

        files = pipeline.find_tournament_files(
            base_dir, format_filter=fmt_filter, since_date=since_date
        )
        self.stdout.write(f"Found {len(files)} tournament files.")

        replace_existing = bool(since_date or force)
        tourn_count, deck_count = pipeline.ingest_files(
            files,
            limit=limit,
            force=force or bool(since_date),
            show_unmatched=show_unmatched,
            replace_existing=replace_existing,
            since_date=since_date,
        )

        if pipeline.rejected_unsupported_count:
            self.stdout.write(
                self.style.WARNING(
                    f"Rejected {pipeline.rejected_unsupported_count} tournaments in unsupported formats."
                )
            )
        if tourn_count == 0 and deck_count == 0 and not force and not since_date:
            self.stdout.write(
                self.style.SUCCESS(
                    "Database is up to date! All tournament files are already ingested."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully ingested {tourn_count} tournaments and {deck_count} decks!"
                )
            )

        if show_unmatched:
            unmatched = pipeline.unmatched_cards
            if unmatched:
                self.stdout.write(
                    self.style.WARNING(
                        f"\nUnmatched Cards Summary ({len(unmatched)} unique cards not found in Scryfall):"
                    )
                )
                for c_name in unmatched:
                    self.stdout.write(self.style.NOTICE(f"  - {c_name}"))
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        "\nAll cards matched successfully! No unknown cards found."
                    )
                )

        if not options.get("skip_knn"):
            from django.core.management import call_command

            self.stdout.write("\nBuilding kNN similarity index for all tournaments...")
            call_command("build_knn", stdout=self.stdout, stderr=self.stderr)
