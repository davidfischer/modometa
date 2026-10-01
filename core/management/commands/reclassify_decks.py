"""Management command to reclassify existing decks in the database using YAML archetype rules."""

import time
from collections import Counter
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Count
from tqdm import tqdm

from core.engine.knn import DeckKNNIndex
from core.formats import FORMAT_NAMES
from core.models.card import Card
from core.models.deck import ClassificationMethod
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
            help="Only reclassify decks that were not matched by an explicit rule (unclassified, fallback posture, or TF-IDF kNN).",
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
        parser.add_argument(
            "--rules-dir",
            type=str,
            default=None,
            help="Path to custom archetype rules directory (defaults to settings.ARCHETYPES_DIR or mtg-archetypes built-in rules).",
        )

    def handle(self, *args, **options):
        fmt_arg = options["format"]
        unclassified_only = options["unclassified"]
        batch_size = options["batch_size"]
        skip_knn = options["skip_knn"]
        dry_run = options["dry_run"]
        show_archetype_counts = options["show_archetype_counts"]
        rules_dir_arg = options.get("rules_dir")
        rules_dir = Path(rules_dir_arg) if rules_dir_arg else None

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

        engine = ArchetypeEngine(
            archetypes_dir=rules_dir, enable_knn=False, enable_posture=False
        )

        # Build Deck queryset
        qs = Deck.objects.all()
        if fmt_arg:
            qs = qs.filter(format=fmt_arg)
        if unclassified_only:
            qs = qs.exclude(classification_method=ClassificationMethod.RULE)

        total_decks = qs.count()
        if total_decks == 0:
            self.stdout.write(
                self.style.WARNING("No decks match the specified filter.")
            )
            return

        fmt_display = fmt_arg.capitalize() if fmt_arg else "All Formats"
        rules_count = (
            len(engine.get_rules(fmt_arg))
            if fmt_arg
            else sum(len(engine.get_rules(f)) for f in FORMAT_NAMES)
        )
        rule_suffix = "s" if rules_count != 1 else ""
        self.stdout.write(
            f"Evaluating {total_decks:,} decks into {rules_count:,} archetype rule{rule_suffix} for {fmt_display}"
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
        promoted_from_knn = 0
        demoted_to_fallback = 0
        demoted_knn_to_fallback = 0
        transitions = Counter()
        archetype_counts: Counter[str] = Counter()
        pending_updates: list[Deck] = []
        fallback_decks: list[Deck] = []
        orig_states: dict[str, tuple[str, str, str | None]] = {}

        pbar = tqdm(
            qs.iterator(chunk_size=batch_size),
            total=total_decks,
            desc="Reclassifying decks",
            unit="deck",
            mininterval=0.2,
        )

        for deck in pbar:
            result = engine.classify(
                deck.mainboard,
                deck.format,
                card_colors_map=card_colors,
                sideboard_cards=deck.sideboard,
            )

            if result.is_rule:
                arch_name = result.archetype_name
                arch_slug = result.archetype_slug
                colors_code = result.colors_code
                color_name = result.color_display_name
                method = ClassificationMethod.RULE

                archetype_counts[arch_name] += 1
                has_changed = (
                    deck.archetype != arch_name
                    or deck.archetype_slug != arch_slug
                    or deck.colors != colors_code
                    or deck.color_name != color_name
                    or deck.classification_method != method
                )

                if has_changed:
                    changed_count += 1
                    prev_method = deck.classification_method
                    if prev_method in (ClassificationMethod.FALLBACK, None):
                        promoted_from_fallback += 1
                    elif prev_method == ClassificationMethod.KNN:
                        promoted_from_knn += 1

                    if deck.archetype != arch_name:
                        transitions[f"{deck.archetype} -> {arch_name}"] += 1
                    elif deck.colors != colors_code:
                        transitions[
                            f"{deck.archetype} [{deck.colors} -> {colors_code}]"
                        ] += 1
                    elif deck.classification_method != method:
                        transitions[
                            f"{deck.archetype} ({deck.classification_method} -> {method})"
                        ] += 1

                if not dry_run:
                    deck.archetype = arch_name
                    deck.archetype_slug = arch_slug
                    deck.colors = colors_code
                    deck.color_name = color_name
                    deck.is_auto_classified = False
                    deck.classification_method = method
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
                                    "classification_method",
                                ],
                                batch_size=1000,
                            )
                        pending_updates.clear()
            else:
                orig_states[deck.id] = (
                    deck.archetype,
                    deck.colors,
                    deck.classification_method,
                )
                deck.colors = result.colors_code
                deck.color_name = result.color_display_name
                fallback_decks.append(deck)

        # Flush remaining rule updates
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
                        "classification_method",
                    ],
                    batch_size=1000,
                )
            pending_updates.clear()

        # Pass 2: kNN matching and posture fallback for decks not matched by rules
        knn_promoted = 0
        pass2_knn_count = 0
        pass2_fallback_count = 0
        fallback_count = len(fallback_decks)
        if fallback_count > 0:
            self.stdout.write(
                f"\nEvaluating {fallback_count:,} fallback decks with kNN against rule-classified decks..."
            )
            knn_index = DeckKNNIndex(format_filter=fmt_arg)
            has_knn_vectors = (
                knn_index.tfidf_matrix is not None
                and knn_index.tfidf_matrix.shape[0] > 0
            )
            engine_knn = ArchetypeEngine(archetypes_dir=rules_dir, enable_knn=True)
            pass2_updates: list[Deck] = []

            for deck in tqdm(
                fallback_decks,
                total=fallback_count,
                desc="Pass 2 classification",
                unit="deck",
                mininterval=0.2,
            ):
                knn_match = None
                if has_knn_vectors:
                    mb_items = [
                        {
                            "card": item.get("card", ""),
                            "count": int(item.get("count", 1)),
                        }
                        for item in deck.mainboard
                    ]
                    sb_items = [
                        {
                            "card": item.get("card", ""),
                            "count": int(item.get("count", 1)),
                        }
                        for item in (deck.sideboard or [])
                    ]
                    query_vec = knn_index.vector_from_decklist(mb_items, sb_items)
                    known_archetypes = engine_knn.get_known_archetypes(deck.format)
                    knn_match = knn_index.find_closest_classified(
                        query_vec=query_vec,
                        format_filter=deck.format,
                        known_archetypes=known_archetypes,
                        min_similarity=engine_knn.knn_threshold,
                        only_rule_classified=True,
                    )

                orig_arch, orig_colors, orig_method = orig_states[deck.id]

                if knn_match:
                    match_name = knn_match[0]
                    match_slug = knn_match[1]
                    pass2_knn_count += 1
                    archetype_counts[match_name] += 1

                    net_has_changed = (
                        orig_arch != match_name
                        or orig_colors != deck.colors
                        or orig_method != ClassificationMethod.KNN
                    )
                    if net_has_changed:
                        changed_count += 1
                        if orig_method in (ClassificationMethod.FALLBACK, None):
                            knn_promoted += 1

                        if orig_arch != match_name:
                            transitions[f"{orig_arch} -> {match_name} [kNN]"] += 1
                        elif orig_colors != deck.colors:
                            transitions[
                                f"{orig_arch} [{orig_colors} -> {deck.colors}]"
                            ] += 1
                        elif orig_method != ClassificationMethod.KNN:
                            transitions[f"{orig_arch} ({orig_method} -> knn)"] += 1

                    deck.archetype = match_name
                    deck.archetype_slug = match_slug
                    deck.classification_method = ClassificationMethod.KNN
                    deck.is_auto_classified = False
                else:
                    all_cards = deck.mainboard + (deck.sideboard or [])
                    fallback_posture = engine.get_fallback_posture(
                        all_cards, deck.color_name
                    )
                    pass2_fallback_count += 1
                    archetype_counts[fallback_posture.archetype_name] += 1

                    net_has_changed = (
                        orig_arch != fallback_posture.archetype_name
                        or orig_colors != deck.colors
                        or orig_method != ClassificationMethod.FALLBACK
                    )
                    if net_has_changed:
                        changed_count += 1
                        if orig_method == ClassificationMethod.RULE:
                            demoted_to_fallback += 1
                        elif orig_method == ClassificationMethod.KNN:
                            demoted_knn_to_fallback += 1

                        if orig_arch != fallback_posture.archetype_name:
                            transitions[
                                f"{orig_arch} -> {fallback_posture.archetype_name}"
                            ] += 1
                        elif orig_colors != deck.colors:
                            transitions[
                                f"{orig_arch} [{orig_colors} -> {deck.colors}]"
                            ] += 1
                        elif orig_method != ClassificationMethod.FALLBACK:
                            transitions[f"{orig_arch} ({orig_method} -> fallback)"] += 1

                    deck.archetype = fallback_posture.archetype_name
                    deck.archetype_slug = fallback_posture.archetype_slug
                    deck.classification_method = ClassificationMethod.FALLBACK
                    deck.is_auto_classified = True

                if not dry_run:
                    pass2_updates.append(deck)
                    if len(pass2_updates) >= batch_size:
                        with transaction.atomic():
                            Deck.objects.bulk_update(
                                pass2_updates,
                                [
                                    "archetype",
                                    "archetype_slug",
                                    "colors",
                                    "color_name",
                                    "is_auto_classified",
                                    "classification_method",
                                ],
                                batch_size=1000,
                            )
                        pass2_updates.clear()

            if not dry_run and pass2_updates:
                with transaction.atomic():
                    Deck.objects.bulk_update(
                        pass2_updates,
                        [
                            "archetype",
                            "archetype_slug",
                            "colors",
                            "color_name",
                            "is_auto_classified",
                            "classification_method",
                        ],
                        batch_size=1000,
                    )
                pass2_updates.clear()

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
        if promoted_from_knn:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  - Promoted from TF-IDF kNN to rule: {promoted_from_knn:,}"
                )
            )
        if knn_promoted:
            self.stdout.write(
                self.style.SUCCESS(f"  - Promoted via kNN: {knn_promoted:,} decks")
            )
        if demoted_to_fallback:
            self.stdout.write(
                self.style.WARNING(
                    f"  - Reverted to fallback posture: {demoted_to_fallback:,}"
                )
            )
        if demoted_knn_to_fallback:
            self.stdout.write(
                self.style.WARNING(
                    f"  - Reverted from TF-IDF kNN to fallback posture: {demoted_knn_to_fallback:,}"
                )
            )

        if transitions:
            self.stdout.write("\nTop Archetype Transitions:")
            for trans, count in transitions.most_common(10):
                self.stdout.write(f"  {count:,}x: {trans}")

        if show_archetype_counts:
            if not dry_run:
                counts_dict = dict(
                    Deck.objects.filter(format=fmt_arg)
                    .values_list("archetype")
                    .annotate(c=Count("id"))
                )
                method_counts = dict(
                    Deck.objects.filter(format=fmt_arg)
                    .values_list("classification_method")
                    .annotate(c=Count("id"))
                )
                rule_count = method_counts.get(ClassificationMethod.RULE, 0)
                knn_count = method_counts.get(ClassificationMethod.KNN, 0)
                fallback_count = method_counts.get(ClassificationMethod.FALLBACK, 0)
                unclassified_count = method_counts.get(None, 0)
                total_in_fmt = Deck.objects.filter(format=fmt_arg).count()
            elif unclassified_only and dry_run:
                base_counts = dict(
                    Deck.objects.filter(
                        format=fmt_arg,
                        classification_method=ClassificationMethod.RULE,
                    )
                    .values_list("archetype")
                    .annotate(c=Count("id"))
                )
                combined = Counter(base_counts) + archetype_counts
                counts_dict = dict(combined)
                base_rule_count = Deck.objects.filter(
                    format=fmt_arg,
                    classification_method=ClassificationMethod.RULE,
                ).count()
                rule_count = base_rule_count + (total_decks - len(fallback_decks))
                knn_count = pass2_knn_count
                fallback_count = pass2_fallback_count
                unclassified_count = 0
                total_in_fmt = Deck.objects.filter(format=fmt_arg).count()
            else:
                counts_dict = dict(archetype_counts)
                rule_count = total_decks - len(fallback_decks)
                knn_count = pass2_knn_count
                fallback_count = pass2_fallback_count
                unclassified_count = 0
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

            for name in sorted_archetypes:
                count = counts_dict.get(name, 0)
                pct = (count / total_in_fmt * 100) if total_in_fmt else 0.0
                self.stdout.write(f"  {name:<{col_width}} {count:>8,d} {pct:>6.1f}%")

            self.stdout.write(sep)
            rule_pct = (rule_count / total_in_fmt * 100) if total_in_fmt else 0.0
            knn_pct = (knn_count / total_in_fmt * 100) if total_in_fmt else 0.0
            fallback_pct = (
                (fallback_count / total_in_fmt * 100) if total_in_fmt else 0.0
            )
            yaml_label = "YAML Archetypes (Rule)"
            self.stdout.write(
                f"  {yaml_label:<{col_width}} {rule_count:>8,d} {rule_pct:>6.1f}%"
            )
            self.stdout.write(
                f"  {'TF-IDF Classified':<{col_width}} {knn_count:>8,d} {knn_pct:>6.1f}%"
            )
            self.stdout.write(
                f"  {'Fallback (Auto-classified)':<{col_width}} {fallback_count:>8,d} {fallback_pct:>6.1f}%"
            )
            if unclassified_count:
                unclass_pct = (
                    (unclassified_count / total_in_fmt * 100) if total_in_fmt else 0.0
                )
                self.stdout.write(
                    f"  {'Unclassified':<{col_width}} {unclassified_count:>8,d} {unclass_pct:>6.1f}%"
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

        total_changed = changed_count
        if not skip_knn and total_changed > 0:
            self.stdout.write(
                "\nRebuilding kNN similarity index with updated archetypes..."
            )
            call_command("build_knn", stdout=self.stdout, stderr=self.stderr)
        elif skip_knn and total_changed > 0:
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
