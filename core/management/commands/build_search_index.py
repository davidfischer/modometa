"""Management command to build and save the static search index JSON file."""

import json
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.engine.search_index import build_search_index_data


class Command(BaseCommand):
    help = "Build and save the precomputed search index JSON file across formats for fast static serving."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Path to save the JSON index file (default: settings.SEARCH_INDEX_PATH)",
        )
        parser.add_argument(
            "--active-only",
            action="store_true",
            help="Restrict index to settings.ACTIVE_FORMAT_SLUGS instead of all formats in the database.",
        )

    def handle(self, *args, **options):
        output_path = options.get("output")
        if output_path:
            dest_path = Path(output_path)
        else:
            dest_path = getattr(
                settings,
                "SEARCH_INDEX_PATH",
                Path(settings.BASE_DIR) / "data" / "search_index.json",
            )

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        formats = None
        if options.get("active_only"):
            formats = getattr(
                settings, "ACTIVE_FORMAT_SLUGS", settings.MODOMETA_FORMATS
            )

        fmt_desc = f"formats: {', '.join(formats)}" if formats else "all formats"
        self.stdout.write(f"Building search index ({fmt_desc}) -> {dest_path}...")

        t0 = time.perf_counter()
        data = build_search_index_data(formats=formats)

        # Write atomically via temp file
        temp_path = dest_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        temp_path.replace(dest_path)

        duration = time.perf_counter() - t0
        size_kb = dest_path.stat().st_size / 1024

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully built search index ({size_kb:.1f} KB, "
                f"{len(data['archetypes'])} archetypes, {len(data['players']):,} players) "
                f"in {duration:.2f}s -> {dest_path}"
            )
        )
