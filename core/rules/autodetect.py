"""Format autodetection engine for MTGO decklists.

Analyzes card pool legality, ban lists, rarity/sets, and archetype affinities
across the 7 supported MTGO formats:
- Standard
- Pioneer
- Modern
- Pauper
- Premodern
- Legacy
- Vintage
"""

from typing import Any

from core.formats import FORMAT_SLUGS
from core.formats import FORMAT_SPECIFICITY
from core.models.card import Card
from core.models.card import CardLookup
from core.models.card import normalize_card_name
from core.rules.engine import ArchetypeEngine


ALL_FORMATS = FORMAT_SLUGS


class FormatAutodetector:
    """Intelligently determines the target format of a decklist."""

    def __init__(self):
        self.archetype_engine = ArchetypeEngine()
        self._card_cache: dict[str, Card | None] = {}

    def _resolve_card(self, card_name: str) -> Card | None:
        norm = normalize_card_name(card_name)
        if norm in self._card_cache:
            return self._card_cache[norm]

        lookup = (
            CardLookup.objects.select_related("card")
            .filter(lookup_name=norm)
            .order_by("-priority")
            .first()
        )
        if lookup:
            self._card_cache[norm] = lookup.card
            return lookup.card

        card = (
            Card.objects.filter(normalized_name=norm).first()
            or Card.objects.filter(name__iexact=card_name).first()
        )
        self._card_cache[norm] = card
        return card

    def detect_format(
        self,
        mainboard: list[dict[str, Any]] | list[str],
        sideboard: list[dict[str, Any]] | list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Detect the most appropriate format for the deck.

        Returns:
            (best_format_slug, debug_info)
        """
        # Normalize inputs
        mb_items: list[tuple[str, int]] = []
        for item in mainboard:
            if isinstance(item, dict):
                mb_items.append((item.get("card", ""), int(item.get("count", 1))))
            elif isinstance(item, str):
                mb_items.append((item, 1))

        sb_items: list[tuple[str, int]] = []
        for item in sideboard or []:
            if isinstance(item, dict):
                sb_items.append((item.get("card", ""), int(item.get("count", 1))))
            elif isinstance(item, str):
                sb_items.append((item, 1))

        all_entries = mb_items + sb_items
        all_unique_names = list({name for name, _ in all_entries if name})
        mb_names = [name for name, _ in mb_items if name]
        sb_names = [name for name, _ in sb_items if name]

        # Resolve card models
        card_models: dict[str, Card | None] = {
            name: self._resolve_card(name) for name in all_unique_names
        }

        # Check for Vintage-exclusive indicators directly from Scryfall card data
        # (Cards that are legal/restricted in Vintage, but banned or not legal in Legacy)
        has_vintage_exclusive = False
        for name in all_unique_names:
            card = card_models.get(name)
            if not card or not card.legalities:
                continue
            v_leg = card.legalities.get("vintage", "not_legal")
            l_leg = card.legalities.get("legacy", "not_legal")
            if v_leg in ("legal", "restricted") and l_leg in ("banned", "not_legal"):
                has_vintage_exclusive = True
                break

        format_scores: dict[str, float] = {}
        format_diagnostics: dict[str, dict[str, Any]] = {}

        for fmt in ALL_FORMATS:
            not_legal_cards = []
            banned_cards = []
            legal_cards = []
            restricted_errors = []

            for name, count in all_entries:
                if not name:
                    continue
                card = card_models.get(name)
                if not card:
                    continue

                leg = card.legalities.get(fmt, "not_legal")
                if leg == "legal":
                    legal_cards.append(name)
                elif leg == "restricted" and fmt == "vintage":
                    if count > 1:
                        restricted_errors.append(f"{name} ({count} copies)")
                    else:
                        legal_cards.append(name)
                elif leg == "banned":
                    banned_cards.append(name)
                else:  # not_legal
                    not_legal_cards.append(name)

            not_legal_count = len(not_legal_cards)
            banned_count = len(banned_cards)

            # Score calculation:
            # - Heavily penalize cards outside the format's card pool (not_legal)
            # - Moderately penalize cards on the ban list (banned)
            # - Reward matching archetype rules
            # - Add specificity bonus
            score = 0.0

            if not_legal_count == 0:
                # Fully within the card pool
                score += 500.0

                if banned_count == 0:
                    # 100% legal today!
                    score += 500.0
                else:
                    # Historically in format pool, but currently has bans
                    score -= banned_count * 50.0

                if fmt == "vintage" and restricted_errors:
                    score -= len(restricted_errors) * 100.0

                # Specificity bonus
                score += FORMAT_SPECIFICITY.get(fmt, 0)
            else:
                # Has cards outside format's card pool
                score -= not_legal_count * 100.0
                score -= banned_count * 50.0

            # Test Archetype engine match
            (
                arch_name,
                arch_slug,
                _,
                _,
                is_fallback,
                debug_arch,
            ) = self.archetype_engine.classify(mb_names, fmt, sideboard_cards=sb_names)

            if not is_fallback:
                # Direct YAML rule matched in this format!
                rule_prio = debug_arch.get("score", 50)
                score += 150.0 + rule_prio

            # Special case for Vintage:
            # If the deck has Vintage-exclusive cards (Power 9, Bazaar, Workshop, etc.)
            # Vintage gets a massive boost
            if fmt == "vintage" and has_vintage_exclusive:
                score += 600.0

            # Special case for Pauper:
            # If all cards are common and not_legal_count == 0
            if fmt == "pauper" and not_legal_count == 0 and banned_count == 0:
                score += 100.0

            format_scores[fmt] = score
            format_diagnostics[fmt] = {
                "score": score,
                "not_legal_count": not_legal_count,
                "not_legal_sample": not_legal_cards[:3],
                "banned_count": banned_count,
                "banned_sample": banned_cards[:3],
                "matched_archetype": arch_name if not is_fallback else None,
            }

        # Select format with highest score
        best_format = max(format_scores, key=format_scores.get)  # type: ignore

        debug_info = {
            "best_format": best_format,
            "has_vintage_exclusive": has_vintage_exclusive,
            "scores": format_scores,
            "diagnostics": format_diagnostics,
        }

        return best_format, debug_info
