"""Management command to reclassify existing decks in the database using YAML archetype rules."""

import time
from collections import Counter

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Count
from tqdm import tqdm

from core.formats import FORMAT_NAMES
from core.models.card import Card
from core.models.deck import Deck
from core.rules.engine import ArchetypeEngine


class Command(BaseCommand):
    help = "Reclassify existing decks in the database using the latest YAML archetype rules."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            type=str,
            default=None,
            help="Target format (e.g. legacy, modern, vintage, pioneer, standard, premodern, pauper). Defaults to all formats.",
        )
        parser.add_argument(
            "--unclassified",
            action="store_true",
            help="Only reclassify decks that are currently marked as auto-classified / fallback posture.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5000,
            help="Number of decks to buffer before writing bulk updates to the database (default: 5000).",
        )
        parser.add_argument(
            "--skip-knn",
            action="store_true",
            help="Skip rebuilding the kNN deck similarity index after reclassification.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate reclassification and display stats without modifying the database.",
        )
        parser.add_argument(
            "--show-archetype-counts",
            action="store_true",
            help="Display deck counts for each archetype defined in the format's YAML rules (requires --format).",
        )

    def handle(self, *args, **options):
        fmt_arg = options["format"]
        unclassified_only = options["unclassified"]
        batch_size = options["batch_size"]
        skip_knn = options["skip_knn"]
        dry_run = options["dry_run"]
        show_archetype_counts = options["show_archetype_counts"]

        if show_archetype_counts and not fmt_arg:
            raise CommandError(
                "--show-archetype-counts requires --format to be specified (e.g. --format legacy)."
            )

        if fmt_arg:
            fmt_arg = fmt_arg.lower().strip()
            if fmt_arg not in FORMAT_NAMES:
                raise CommandError(
                    f"Unknown format '{fmt_arg}'. Supported formats: {', '.join(sorted(FORMAT_NAMES))}"
                )

        engine = ArchetypeEngine()

        # Build Deck queryset
        qs = Deck.objects.all()
        if fmt_arg:
            qs = qs.filter(format=fmt_arg)
        if unclassified_only:
            qs = qs.filter(is_auto_classified=True)

        total_decks = qs.count()
        if total_decks == 0:
            self.stdout.write(
                self.style.WARNING("No decks match the specified filter.")
            )
            return

        fmt_display = fmt_arg.capitalize() if fmt_arg else "All Formats"
        self.stdout.write(
            f"Evaluating {total_decks:,} decks in {fmt_display}"
            + (" (unclassified only)" if unclassified_only else "")
            + (" [DRY RUN]" if dry_run else "")
            + "..."
        )

        # Preload card colors for fast color deduction
        card_colors: dict[str, list[str]] = dict(
            Card.objects.exclude(colors=[]).values_list("normalized_name", "colors")
        )

        t0 = time.perf_counter()
        changed_count = 0
        promoted_from_fallback = 0
        demoted_to_fallback = 0
        transitions = Counter()
        archetype_counts: Counter[str] = Counter()
        fallback_decks_count = 0
        pending_updates: list[Deck] = []

        pbar = tqdm(
            qs.iterator(chunk_size=batch_size),
            total=total_decks,
            desc="Reclassifying decks",
            unit="deck",
            mininterval=0.2,
        )

        for deck in pbar:
            (
                arch_name,
                arch_slug,
                colors_code,
                color_name,
                is_fallback,
                _,
            ) = engine.classify(
                deck.mainboard,
                deck.format,
                card_colors_map=card_colors,
                sideboard_cards=deck.sideboard,
            )

            archetype_counts[arch_name] += 1
            if is_fallback:
                fallback_decks_count += 1

            has_changed = (
                deck.archetype != arch_name
                or deck.archetype_slug != arch_slug
                or deck.colors != colors_code
                or deck.color_name != color_name
                or deck.is_auto_classified != is_fallback
            )

            if has_changed:
                changed_count += 1
                if deck.is_auto_classified and not is_fallback:
                    promoted_from_fallback += 1
                elif not deck.is_auto_classified and is_fallback:
                    demoted_to_fallback += 1

                if deck.archetype != arch_name:
                    transitions[f"{deck.archetype} -> {arch_name}"] += 1
                elif deck.colors != colors_code:
                    transitions[
                        f"{deck.archetype} [{deck.colors} -> {colors_code}]"
                    ] += 1

                if not dry_run:
                    deck.archetype = arch_name
                    deck.archetype_slug = arch_slug
                    deck.colors = colors_code
                    deck.color_name = color_name
                    deck.is_auto_classified = is_fallback
                    pending_updates.append(deck)

                    if len(pending_updates) >= batch_size:
                        with transaction.atomic():
                            Deck.objects.bulk_update(
                                pending_updates,
                                [
                                    "archetype",
                                    "archetype_slug",
                                    "colors",
                                    "color_name",
                                    "is_auto_classified",
                                ],
                                batch_size=1000,
                            )
                        pending_updates.clear()

        # Flush remaining updates
        if not dry_run and pending_updates:
            with transaction.atomic():
                Deck.objects.bulk_update(
                    pending_updates,
                    [
                        "archetype",
                        "archetype_slug",
                        "colors",
                        "color_name",
                        "is_auto_classified",
                    ],
                    batch_size=1000,
                )
            pending_updates.clear()

        duration = time.perf_counter() - t0

        self.stdout.write("=" * 60)
        self.stdout.write(
            f"Processed {total_decks:,} decks in {duration:.2f}s ({total_decks / max(duration, 0.001):,.0f} decks/sec)"
        )
        self.stdout.write(f"Decks updated: {changed_count:,} / {total_decks:,}")
        if promoted_from_fallback:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  - Classified previously unclassified decks: {promoted_from_fallback:,}"
                )
            )
        if demoted_to_fallback:
            self.stdout.write(
                self.style.WARNING(
                    f"  - Reverted to fallback posture: {demoted_to_fallback:,}"
                )
            )

        if transitions:
            self.stdout.write("\nTop Archetype Transitions:")
            for trans, count in transitions.most_common(10):
                self.stdout.write(f"  {count:,}x: {trans}")

        if show_archetype_counts:
            if unclassified_only and not dry_run:
                counts_dict = dict(
                    Deck.objects.filter(format=fmt_arg)
                    .values_list("archetype")
                    .annotate(c=Count("id"))
                )
                fallback_count = Deck.objects.filter(
                    format=fmt_arg, is_auto_classified=True
                ).count()
                total_in_fmt = Deck.objects.filter(format=fmt_arg).count()
            elif unclassified_only and dry_run:
                base_counts = dict(
                    Deck.objects.filter(format=fmt_arg, is_auto_classified=False)
                    .values_list("archetype")
                    .annotate(c=Count("id"))
                )
                combined = Counter(base_counts) + archetype_counts
                counts_dict = dict(combined)
                fallback_count = fallback_decks_count
                total_in_fmt = Deck.objects.filter(format=fmt_arg).count()
            else:
                counts_dict = dict(archetype_counts)
                fallback_count = fallback_decks_count
                total_in_fmt = total_decks

            yaml_rules = engine.get_rules(fmt_arg)
            yaml_archetype_names = list(dict.fromkeys(r["name"] for r in yaml_rules))
            sorted_archetypes = sorted(
                yaml_archetype_names,
                key=lambda name: (-counts_dict.get(name, 0), name.lower()),
            )

            col_width = max(
                max((len(name) for name in yaml_archetype_names), default=30), 30
            )
            sep = "-" * (col_width + 20)
            self.stdout.write(f"\nArchetype Breakdown ({fmt_display}):")
            self.stdout.write("=" * (col_width + 20))
            self.stdout.write(f"  {'Archetype':<{col_width}} {'Count':>8} {'Pct':>7}")
            self.stdout.write(sep)

            yaml_total = 0
            for name in sorted_archetypes:
                count = counts_dict.get(name, 0)
                yaml_total += count
                pct = (count / total_in_fmt * 100) if total_in_fmt else 0.0
                self.stdout.write(f"  {name:<{col_width}} {count:>8,d} {pct:>6.1f}%")

            self.stdout.write(sep)
            yaml_pct = (yaml_total / total_in_fmt * 100) if total_in_fmt else 0.0
            fallback_pct = (
                (fallback_count / total_in_fmt * 100) if total_in_fmt else 0.0
            )
            yaml_label = f"YAML Archetypes ({len(sorted_archetypes)} total)"
            self.stdout.write(
                f"  {yaml_label:<{col_width}} {yaml_total:>8,d} {yaml_pct:>6.1f}%"
            )
            self.stdout.write(
                f"  {'Fallback (Auto-classified)':<{col_width}} {fallback_count:>8,d} {fallback_pct:>6.1f}%"
            )
            self.stdout.write(
                f"  {'Total Decks':<{col_width}} {total_in_fmt:>8,d} {100.0:>6.1f}%"
            )
            self.stdout.write("=" * (col_width + 20))

        if dry_run:
            self.stdout.write(
                self.style.NOTICE(
                    "\nDry run completed. No database changes were saved."
                )
            )
            return

        # Rebuild kNN index if decks changed and not skipped
        if not skip_knn and changed_count > 0:
            self.stdout.write(
                "\nRebuilding kNN similarity index with updated archetypes..."
            )
            call_command("build_knn", stdout=self.stdout, stderr=self.stderr)
        elif skip_knn and changed_count > 0:
            self.stdout.write(
                self.style.NOTICE(
                    "\nSkipped kNN index rebuild (--skip-knn). Remember to run 'uv run modometa build_knn' later."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "\nAll decks already matched current archetype rules."
                )
            )
