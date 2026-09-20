"""Management command to ingest Legacy Data Collection Project (LDCP) community match data."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.pipeline.community import CommunityIngestionPipeline


class Command(BaseCommand):
    help = "Ingest Legacy Data Collection Project (LDCP) community match data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            type=str,
            default=str(settings.DEFAULT_COMMUNITY_DATA_DIR),
            help="Directory containing LDCP community data JSON files",
        )
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            help="Only process tournaments on or after this date (YYYY-MM-DD)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit number of tournament files to process",
        )
        parser.add_argument(
            "--slug",
            type=str,
            default=None,
            help="Process a single tournament slug/ID",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Force re-ingestion and overwrite existing LDCP community matches",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            default=False,
            help="Raise an error if any Top 8 matchup disagreements are found",
        )

    def handle(self, *args, **options):
        data_dir = Path(options["dir"]).resolve()
        if not data_dir.exists():
            raise CommandError(f"Community data directory does not exist: {data_dir}")

        pipeline = CommunityIngestionPipeline()

        if options["slug"]:
            slug = options["slug"].strip()
            # Look for matching file directly or under directory
            candidates = pipeline.find_files(data_dir)
            files = [f for f in candidates if slug in f.stem]
            if not files:
                raise CommandError(
                    f"No JSON file found matching slug '{slug}' in {data_dir}"
                )
        else:
            files = pipeline.find_files(data_dir)

        if not files:
            self.stdout.write(
                self.style.WARNING(f"No JSON files found to process in {data_dir}")
            )
            return

        self.stdout.write(
            f"Found {len(files)} community tournament file(s). Ingesting..."
        )

        result = pipeline.ingest_files(
            files=files,
            force=options["force"],
            limit=options["limit"],
            since=options["since"],
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Ingestion complete: {result.matches_saved} LDCP match(es) saved "
                f"across {result.tournaments_ingested} tournament(s) "
                f"({result.matches_skipped_no_deck} matchups skipped without full decklists, "
                f"{result.tournaments_scanned} scanned)."
            )
        )

        if result.warnings:
            self.stdout.write(
                self.style.WARNING(f"\nWarnings ({len(result.warnings)}):")
            )
            for msg in result.warnings:
                self.stdout.write(self.style.WARNING(f"  - {msg}"))

        if result.disagreements:
            self.stdout.write(
                self.style.WARNING(
                    f"\nDisagreements found in Top 8 matches ({len(result.disagreements)}):"
                )
            )
            for msg in result.disagreements:
                self.stdout.write(self.style.WARNING(f"  - {msg}"))

            if options["strict"]:
                raise CommandError(
                    f"Aborted due to {len(result.disagreements)} Top 8 matchup disagreement(s) (--strict enabled)."
                )
