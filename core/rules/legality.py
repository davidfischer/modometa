"""Legality engine verifying format legality, 4-of maximum, and deck size."""

import logging
from collections import defaultdict
from typing import Any

from core.models.card import Card
from core.models.card import CardLookup
from core.models.card import normalize_card_name


logger = logging.getLogger(__name__)


BASIC_LANDS = {
    "plains",
    "island",
    "swamp",
    "mountain",
    "forest",
    "wastes",
    "snow-covered plains",
    "snow-covered island",
    "snow-covered swamp",
    "snow-covered mountain",
    "snow-covered forest",
}

ANY_NUMBER_CARDS = {
    "relentless rats",
    "shadowborn apostle",
    "persistent petitioners",
    "dragon's approach",
    "rat colony",
    "slime against humanity",
    "templar knight",
    "hare apparent",
}


class LegalityEngine:
    """Deterministic MTG format legality and deck composition checker."""

    def __init__(self):
        self._lookup_cache: dict[str, Card | None] = {}
        self.unmatched_cards: set[str] = set()

    def resolve_card(self, card_name: str) -> Card | None:
        """Resolve card name to canonical Card model with in-memory caching."""
        norm = normalize_card_name(card_name)
        if norm in self._lookup_cache:
            return self._lookup_cache[norm]

        # Check lookup table
        try:
            lookup = (
                CardLookup.objects.select_related("card")
                .filter(lookup_name=norm)
                .order_by("-priority")
                .first()
            )
            if lookup:
                self._lookup_cache[norm] = lookup.card
                return lookup.card
        except Exception as e:
            logger.debug("Card lookup failed for %s: %s", norm, e)

        # Check Card directly
        card = (
            Card.objects.filter(normalized_name=norm).first()
            or Card.objects.filter(name__iexact=card_name).first()
        )
        self._lookup_cache[norm] = card
        if card is None and card_name and card_name.strip():
            self.unmatched_cards.add(card_name.strip())
        return card

    def validate_deck(
        self,
        mainboard: list[dict[str, Any]],
        sideboard: list[dict[str, Any]],
        format_name: str,
    ) -> tuple[bool, list[str], list[str]]:
        """Validate deck legality.

        Returns:
            (is_legal, error_messages, illegal_card_names)
        """
        fmt = format_name.lower().strip()
        errors: list[str] = []
        illegal_cards: set[str] = set()
        card_totals = defaultdict(int)

        mb_count = sum(item.get("count", 0) for item in mainboard)
        sb_count = sum(item.get("count", 0) for item in sideboard)

        if mb_count < 60:
            errors.append(f"Mainboard has only {mb_count} cards (minimum 60)")
        if sb_count > 15:
            errors.append(f"Sideboard has {sb_count} cards (maximum 15)")

        for section_name, section in [
            ("mainboard", mainboard),
            ("sideboard", sideboard),
        ]:
            for item in section:
                name = item.get("card") or item.get("CardName") or ""
                count = item.get("count") or item.get("Count") or 0
                norm = normalize_card_name(name)
                card_totals[norm] += count

                card = self.resolve_card(name)
                if card:
                    if not card.is_legal_in(fmt):
                        errors.append(
                            f"{name} is not legal or is banned in {fmt.capitalize()}"
                        )
                        illegal_cards.add(name)
                    # Vintage restricted check
                    if fmt == "vintage":
                        leg = card.legalities.get("vintage")
                        if leg == "restricted" and card_totals[norm] > 1:
                            errors.append(
                                f"{name} is restricted in Vintage (maximum 1 copy)"
                            )
                            illegal_cards.add(name)

        # 4-of limit check
        for norm, total in card_totals.items():
            if norm in BASIC_LANDS or norm in ANY_NUMBER_CARDS:
                continue
            if total > 4:
                errors.append(f"Deck contains {total} copies of {norm} (maximum 4)")
                illegal_cards.add(norm)

        is_legal = len(errors) == 0
        return is_legal, errors, sorted(list(illegal_cards))
