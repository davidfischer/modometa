"""Management command to build and save the global TF-IDF kNN deck similarity index."""

import os
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.engine.knn import build_knn_index


class Command(BaseCommand):
    help = "Build the global TF-IDF kNN deck similarity index across all tournaments into a compressed .npz file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Path to save the .npz index file (default: settings.KNN_INDEX_PATH)",
        )

    def handle(self, *args, **options):
        output_path = options.get("output")
        if output_path:
            output_path = Path(output_path)
        else:
            output_path = getattr(
                settings,
                "KNN_INDEX_PATH",
                Path(settings.BASE_DIR) / "data" / "knn_index.npz",
            )

        self.stdout.write(f"Building global kNN similarity index -> {output_path}...")
        t0 = time.perf_counter()
        dest, n_decks, n_features, non_zeros = build_knn_index(
            dest_path=output_path, stdout=self.stdout
        )
        duration = time.perf_counter() - t0
        size_mb = os.path.getsize(dest) / (1024 * 1024)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully built kNN index with {n_decks:,} decks, {n_features:,} unique cards, "
                f"{non_zeros:,} non-zeros in {duration:.1f}s -> {dest} ({size_mb:.2f} MB)"
            )
        )
