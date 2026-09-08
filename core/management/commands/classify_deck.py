"""Management command to classify a deck from stdin or file."""

import sys
from pathlib import Path

from django.core.management.base import BaseCommand

from core.decklist import parse_text_decklist
from core.engine.knn import DeckKNNIndex
from core.engine.knn import get_global_knn_index
from core.formats import FORMAT_NAMES
from core.formats import is_valid_format
from core.models.card import Card
from core.rules.autodetect import FormatAutodetector
from core.rules.engine import ArchetypeEngine
from core.rules.legality import LegalityEngine


class Command(BaseCommand):
    help = "Classify a deck from stdin or file (autodetects format by default, or specify --format)"

    def add_arguments(self, parser):
        parser.add_argument(
            "file",
            nargs="?",
            type=str,
            help="Path to deck text file (reads from stdin if omitted)",
        )
        parser.add_argument(
            "--format",
            type=str,
            default="autodetect",
            help="Target format (default: autodetect, or specify legacy, modern, vintage, pioneer, standard, premodern, pauper)",
        )
        parser.add_argument(
            "--knn",
            action="store_true",
            default=True,
            help="Show k-nearest historical decks",
        )

    def handle(self, *args, **options):
        fmt_arg = options["format"].lower().strip()
        file_arg = options["file"]

        if file_arg:
            path = Path(file_arg)
            if not path.exists():
                self.stderr.write(f"File not found: {path}")
                return
            with open(path, "r", encoding="utf-8") as fp:
                lines = fp.readlines()
        else:
            # Read from stdin
            if sys.stdin.isatty():
                self.stdout.write("Paste your decklist and press Ctrl+D (EOF):")
            lines = sys.stdin.readlines()

        if not lines:
            self.stderr.write("No decklist content provided!")
            return

        mainboard, sideboard = parse_text_decklist(lines)
        total_mb = sum(item["count"] for item in mainboard)
        total_sb = sum(item["count"] for item in sideboard)

        # Autodetect format if requested
        was_autodetected = False
        if fmt_arg in ("autodetect", "auto", ""):
            detector = FormatAutodetector()
            fmt, _ = detector.detect_format(mainboard, sideboard)
            was_autodetected = True
        else:
            fmt = fmt_arg

        if not is_valid_format(fmt):
            self.stderr.write(
                self.style.ERROR(
                    f"Unsupported format '{fmt}'. Supported formats: {', '.join(sorted(FORMAT_NAMES))}"
                )
            )
            return

        # Classification
        engine = ArchetypeEngine()
        card_colors = dict(
            Card.objects.exclude(colors=[]).values_list("normalized_name", "colors")
        )

        (
            arch_name,
            arch_slug,
            colors_code,
            color_name,
            is_fallback,
            debug,
        ) = engine.classify(
            mainboard, fmt, card_colors_map=card_colors, sideboard_cards=sideboard
        )

        # Legality
        legality_engine = LegalityEngine()
        is_legal, errors, illegal_cards = legality_engine.validate_deck(
            mainboard, sideboard, fmt
        )

        # Output
        self.stdout.write("=" * 60)
        if was_autodetected:
            self.stdout.write(
                f"Format: {self.style.SUCCESS(fmt.capitalize())} (Autodetected based on card pool & legality)"
            )
        else:
            self.stdout.write(f"Format: {fmt.capitalize()} (Explicit)")
        self.stdout.write(f"Cards: {total_mb} mainboard, {total_sb} sideboard")
        self.stdout.write(
            self.style.SUCCESS(f"Archetype: {arch_name}")
            if not is_fallback
            else self.style.WARNING(f"Archetype (Fallback): {arch_name}")
        )
        self.stdout.write(f"Colors: {color_name} ({colors_code})")
        self.stdout.write("-" * 60)

        if not is_fallback and debug.get("matched_rule"):
            self.stdout.write(
                f"Matched Rule: {debug['matched_rule']} (Score: {debug.get('score')})"
            )
        elif is_fallback:
            self.stdout.write(f"Fallback Posture: {debug.get('fallback_posture')}")

        # Legality status
        if is_legal:
            self.stdout.write(
                self.style.SUCCESS(f"Legality in {fmt.capitalize()}: Legal")
            )
        else:
            self.stdout.write(
                self.style.ERROR(f"Legality in {fmt.capitalize()}: NOT LEGAL")
            )
            for err in errors:
                self.stdout.write(self.style.NOTICE(f"  - {err}"))

        # kNN Nearest Decks
        if options["knn"]:
            self.stdout.write("-" * 60)
            self.stdout.write(
                f"Top 5 Closest Decks in {fmt.capitalize()} (TF-IDF + 1-yr half-life):"
            )
            index = get_global_knn_index()
            if index is None:
                index = DeckKNNIndex(format_filter=fmt)
            query_vec = index.vector_from_decklist(mainboard, sideboard)
            neighbors = index.query(query_vec, top_k=5, format_filter=fmt)

            if neighbors:
                for idx, n in enumerate(neighbors, 1):
                    sim_pct = n["raw_similarity"] * 100
                    decay_pct = n["recency_weight"] * 100
                    final_pct = n["score"] * 100
                    self.stdout.write(
                        f"  {idx}. {n['player']} — {n['archetype']} ({n['date']}) "
                        f"[Sim: {sim_pct:.1f}%, Recency: {decay_pct:.1f}% -> Score: {final_pct:.1f}%]"
                    )
            else:
                self.stdout.write("  No matching historical decks found in database.")

        self.stdout.write("=" * 60)
