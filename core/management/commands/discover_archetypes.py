"""Management command to discover potential new archetypes using TF-IDF and clustering."""

from collections import Counter
from collections import defaultdict

import numpy as np
import yaml
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from scipy.sparse import csr_matrix
from sklearn.cluster import DBSCAN
from sklearn.feature_extraction.text import TfidfTransformer

from core.formats import FORMAT_NAMES
from core.formats import is_valid_format
from core.models.card import Card
from core.models.card import CardLookup
from core.models.card import normalize_card_name
from core.models.deck import Deck


def get_valid_card_names() -> set[str]:
    """Retrieve normalized set of all valid card names and aliases from database."""
    lookup_names = set(CardLookup.objects.values_list("lookup_name", flat=True))
    card_names = set(Card.objects.values_list("normalized_name", flat=True))
    raw_names = {
        normalize_card_name(n) for n in Card.objects.values_list("name", flat=True)
    }
    return lookup_names | card_names | raw_names


def is_known_card(card_name: str, valid_names: set[str]) -> bool:
    """Check if a card name is recognized either directly or via split/composite face."""
    norm = normalize_card_name(card_name)
    if not norm:
        return False
    if norm in valid_names:
        return True
    if " // " in norm:
        parts = norm.split(" // ")
        if parts[0].strip() in valid_names:
            return True
        if len(parts) > 1 and parts[1].strip() in valid_names:
            return True
    return False


class Command(BaseCommand):
    help = "Discover unclassified archetype clusters using TF-IDF and clustering"

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            type=str,
            default="legacy",
            help="Format to analyze (e.g. legacy, modern, vintage)",
        )
        parser.add_argument(
            "--min-cluster",
            type=int,
            default=4,
            help="Minimum number of decks to form an archetype cluster (default: 4)",
        )
        parser.add_argument(
            "--min-cluster-size",
            type=int,
            default=None,
            help="Alias for --min-cluster",
        )
        parser.add_argument(
            "--eps",
            type=float,
            default=0.42,
            help="DBSCAN cosine distance threshold (default: 0.42)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Analyze all decks instead of only unclassified/fallback decks",
        )

    def handle(self, *args, **options):
        fmt = options["format"].lower().strip()
        if not is_valid_format(fmt):
            raise CommandError(
                f"Unsupported format '{fmt}'. Supported formats: {', '.join(sorted(FORMAT_NAMES))}"
            )
        min_cluster = (
            options["min_cluster_size"]
            if options.get("min_cluster_size") is not None
            else options["min_cluster"]
        )
        eps = options["eps"]
        analyze_all = options["all"]

        valid_names = get_valid_card_names()
        if not valid_names:
            raise CommandError(
                "No cards found in database. Run 'python manage.py sync_scryfall' first."
            )

        # 1. Validate existing YAML archetype rules for this format
        yaml_file = settings.ARCHETYPES_DIR / f"{fmt}.yaml"
        if yaml_file.exists():
            try:
                with open(yaml_file, "r", encoding="utf-8") as fp:
                    existing_rules = yaml.safe_load(fp) or []
            except Exception as e:
                raise CommandError(f"Error reading {yaml_file}: {e}")

            for r in existing_rules:
                arch_name = r.get("name", "Unknown")
                for key in ("mandatory", "signatures", "anti_signatures"):
                    for item in r.get(key, []):
                        card = (
                            item.get("card") or item.get("name")
                            if isinstance(item, dict)
                            else item
                        )
                        if not is_known_card(card, valid_names):
                            raise CommandError(
                                f"Unrecognized card '{card}' found in {yaml_file} for archetype '{arch_name}' ({key})."
                            )

        qs = Deck.objects.filter(format=fmt)
        if not analyze_all:
            qs = qs.filter(is_auto_classified=True)

        decks = list(qs.values("id", "player", "archetype", "mainboard", "sideboard"))
        if len(decks) < min_cluster:
            self.stdout.write(
                f"Not enough candidate decks found for {fmt.capitalize()} ({len(decks)} decks found, min {min_cluster})."
            )
            return

        self.stdout.write(
            f"Analyzing {len(decks)} decks in {fmt.capitalize()} for undiscovered archetype clusters..."
        )

        # Build TF-IDF with card validation
        vocab_map = {}
        rows, cols, data = [], [], []

        for row_idx, d in enumerate(decks):
            counts = Counter()
            for item in d.get("mainboard", []):
                raw_card = item.get("card", "")
                if not raw_card:
                    continue
                if not is_known_card(raw_card, valid_names):
                    raise CommandError(
                        f"Unrecognized card '{raw_card}' found in mainboard of deck {d['id']} (player: {d['player']})."
                    )
                c = normalize_card_name(raw_card)
                if c:
                    counts[c] += item.get("count", 1)

            for item in d.get("sideboard", []):
                raw_card = item.get("card", "")
                if raw_card and not is_known_card(raw_card, valid_names):
                    raise CommandError(
                        f"Unrecognized card '{raw_card}' found in sideboard of deck {d['id']} (player: {d['player']})."
                    )

            for card, cnt in counts.items():
                if card not in vocab_map:
                    vocab_map[card] = len(vocab_map)
                rows.append(row_idx)
                cols.append(vocab_map[card])
                data.append(cnt)

        inv_vocab = {idx: card for card, idx in vocab_map.items()}
        term_matrix = csr_matrix(
            (data, (rows, cols)), shape=(len(decks), len(vocab_map)), dtype=np.float32
        )
        tfidf = TfidfTransformer(norm="l2", sublinear_tf=True).fit_transform(
            term_matrix
        )

        # Cluster with DBSCAN on cosine distance
        clustering = DBSCAN(eps=eps, min_samples=min_cluster, metric="cosine")
        labels = clustering.fit_predict(tfidf)

        cluster_groups = defaultdict(list)
        for idx, label in enumerate(labels):
            if label != -1:  # ignore noise
                cluster_groups[label].append(idx)

        cluster_groups = {
            cid: idxs
            for cid, idxs in cluster_groups.items()
            if len(idxs) >= min_cluster
        }

        if not cluster_groups:
            self.stdout.write("No distinct clusters found with current threshold.")
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Discovered {len(cluster_groups)} candidate archetype clusters!\n"
            )
        )

        sorted_clusters = sorted(
            cluster_groups.items(),
            key=lambda item: (len(item[1]), -item[0]),
            reverse=True,
        )

        for rank, (cluster_id, deck_indices) in enumerate(sorted_clusters, start=1):
            cluster_size = len(deck_indices)
            sub_matrix = tfidf[deck_indices]
            # Average TF-IDF per card in cluster
            mean_tfidf = np.asarray(sub_matrix.mean(axis=0)).ravel()

            # Card frequency in cluster (% of decks that include it)
            card_freq = Counter()
            for idx in deck_indices:
                for item in decks[idx]["mainboard"]:
                    c = normalize_card_name(item.get("card", ""))
                    if c:
                        card_freq[c] += 1

            # Top distinctive cards by TF-IDF weight
            top_card_indices = np.argsort(mean_tfidf)[::-1][:15]
            distinctive_cards = [
                (inv_vocab[i], card_freq[inv_vocab[i]] / cluster_size, mean_tfidf[i])
                for i in top_card_indices
                if inv_vocab[i]
            ]

            mandatory_candidates = [
                card for card, freq, _ in distinctive_cards if freq >= 0.95
            ][:2]
            signature_candidates = [
                card for card, freq, _ in distinctive_cards if 0.50 <= freq < 0.95
            ][:6]

            sample_players = [decks[i]["player"] for i in deck_indices[:4]]
            proposed_name = (
                distinctive_cards[0][0].title()
                if distinctive_cards
                else f"Cluster-{rank}"
            )

            self.stdout.write("=" * 60)
            self.stdout.write(
                self.style.WARNING(
                    f"Candidate Cluster #{rank}: {cluster_size} Decks (Sample Players: {', '.join(sample_players)})"
                )
            )
            self.stdout.write("Suggested YAML Rule Definition:")
            self.stdout.write(
                "------------------------------------------------------------"
            )
            self.stdout.write(f'- name: "{proposed_name}"')
            self.stdout.write('  category: "Midrange"')
            self.stdout.write("  priority: 90")
            if mandatory_candidates:
                self.stdout.write("  mandatory:")
                for c in mandatory_candidates:
                    self.stdout.write(f'    - "{c}"')
            self.stdout.write("  signatures:")
            for c in signature_candidates:
                self.stdout.write(f'    - "{c}"')
            self.stdout.write(
                f"  min_signatures: {max(2, min(3, len(signature_candidates)))}"
            )
            self.stdout.write("=" * 60 + "\n")
