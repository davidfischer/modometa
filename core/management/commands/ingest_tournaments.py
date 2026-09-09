"""Management command to ingest MTGO tournament JSON files."""

import re
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db.models import Max

from core.formats import FORMAT_NAMES
from core.formats import is_valid_format
from core.models.tournament import Tournament
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
            "--refresh-days",
            type=int,
            default=None,
            help=(
                "Re-ingest/refresh tournaments dated within N days before the newest tournament in the "
                "existing database (to capture updated/late-arriving league decks), while skipping "
                "older already-ingested tournaments."
            ),
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
        refresh_days = options.get("refresh_days")

        if refresh_days is not None and refresh_days < 0:
            raise CommandError("--refresh-days must be a non-negative integer.")

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
        files_to_process = files

        if refresh_days is not None and not force and not since_date:
            tourn_qs = Tournament.objects.all()
            if fmt_filter:
                tourn_qs = tourn_qs.filter(format=fmt_filter)

            latest_db_date = tourn_qs.aggregate(max_date=Max("date"))["max_date"]

            if latest_db_date is not None:
                cutoff_date = latest_db_date - timedelta(days=refresh_days)
                self.stdout.write(
                    f"Latest tournament date in database: {latest_db_date}. "
                    f"Refreshing tournaments since {cutoff_date} ({refresh_days} days before latest DB record)..."
                )
            else:
                cutoff_date = None
                self.stdout.write(
                    "No existing tournaments in database. Ingesting all tournament files..."
                )

            file_date_map: dict[Path, datetime.date] = {}
            for f in files:
                m = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
                if m:
                    try:
                        d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                        file_date_map[f] = d
                    except ValueError:
                        pass

            existing_ids = set(Tournament.objects.values_list("id", flat=True))
            files_to_process = []
            for f in files:
                f_date = file_date_map.get(f)
                if cutoff_date and f_date and f_date >= cutoff_date:
                    # Recent tournament on or after cutoff: re-ingest and replace
                    files_to_process.append(f)
                elif f.stem not in existing_ids:
                    # Older tournament not yet in database: ingest
                    files_to_process.append(f)

            replace_existing = True

        tourn_count, deck_count = pipeline.ingest_files(
            files_to_process,
            limit=limit,
            force=force or bool(since_date) or bool(refresh_days is not None),
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
        if (
            tourn_count == 0
            and deck_count == 0
            and not force
            and not since_date
            and refresh_days is None
        ):
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
