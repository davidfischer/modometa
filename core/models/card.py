"""Card and CardLookup models."""

import re
import unicodedata
import urllib.parse
from collections import Counter
from collections.abc import Iterable
from typing import Any

from django.db import models


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def get_scryfall_url(name: str | None = None, card_id: str | None = None) -> str:
    """Return the Scryfall URL for a card by name or Scryfall ID.

    If a card ID (UUID) is provided or passed as name, links directly to the card.
    Otherwise, performs an exact name search query.
    """
    if card_id:
        return f"https://scryfall.com/card/{card_id}"
    if name:
        stripped = name.strip()
        if _UUID_RE.match(stripped):
            return f"https://scryfall.com/card/{stripped}"
        return f"https://scryfall.com/search?q=%21%22{urllib.parse.quote_plus(stripped)}%22"
    return "https://scryfall.com/"


def get_gatherer_url(name: str | None) -> str:
    """Return the Wizards Gatherer URL for a card name.

    Handles split, adventure, and modal cards by querying the front face.
    """
    if not name:
        return "https://gatherer.wizards.com/"
    # Gatherer indexes split/adventure/modal cards under the primary (front) face
    front_face = name.split("//")[0].strip()
    return f"https://gatherer.wizards.com/Pages/Card/Details.aspx?name={urllib.parse.quote_plus(front_face)}"


def normalize_card_name(name: str) -> str:
    """Normalize card names for deterministic matching.

    Strips diacritics, punctuation, extra whitespace, and converts to lowercase.
    Splits double-faced cards to front face if needed.
    """
    if not name:
        return ""
    # Normalize unicode (decompose accents)
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().strip()
    # Normalize curly apostrophes / quotes
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    # Replace slashes in split cards (e.g. 'Fire // Ice' -> 'fire // ice')
    s = re.sub(r"\s*//\s*", " // ", s)
    # Remove unwanted punctuation except hyphen and slash
    s = re.sub(r"[^\w\s\-/]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def expand_card_counts(cards: Iterable[Any]) -> Counter[str]:
    """Extract card quantities and expand names to include full names and face names.

    Accepts an iterable of:
    - dict: {"card": str, "count": int} or {"name": str, "count": int}
    - tuple: (str, int)
    - str: "4 Lightning Bolt", "4x Lightning Bolt", or "Lightning Bolt"

    Returns:
        Counter mapping normalized full and face card names to their total copy count.
    """
    counts: Counter[str] = Counter()
    for item in cards:
        if not item:
            continue
        if isinstance(item, dict):
            c_name = item.get("card") or item.get("name") or ""
            try:
                c_count = int(item.get("count", 1))
            except ValueError, TypeError:
                c_count = 1
        elif isinstance(item, tuple) and len(item) == 2:
            c_name = item[0]
            try:
                c_count = int(item[1])
            except ValueError, TypeError:
                c_count = 1
        elif isinstance(item, str):
            m = re.match(r"^(\d+)x?\s+(.*)$", item.strip())
            if m:
                c_count = int(m.group(1))
                c_name = m.group(2)
            else:
                c_count = 1
                c_name = item
        else:
            c_name = str(item)
            c_count = 1

        norm = normalize_card_name(c_name)
        if norm:
            counts[norm] += c_count
            if " // " in norm:
                for face in norm.split(" // "):
                    face_norm = face.strip()
                    if face_norm:
                        counts[face_norm] += c_count
    return counts


def expand_card_names(cards: Iterable[Any]) -> set[str]:
    """Expand card names to include full normalized names and individual face names.

    For multi-face cards (double-faced, split, adventure, modal DFC), both the full
    composite name and each individual face name are included.

    For example, 'Delver of Secrets // Insectile Aberration' produces:
    - 'delver of secrets // insectile aberration'
    - 'delver of secrets'
    - 'insectile aberration'
    """
    return set(expand_card_counts(cards).keys())


class Card(models.Model):
    """Scryfall Oracle-level card entity."""

    id = models.CharField(max_length=64, primary_key=True, verbose_name="ID")
    oracle_id = models.CharField(max_length=64, db_index=True, verbose_name="Oracle ID")
    name = models.CharField(max_length=255, db_index=True)
    normalized_name = models.CharField(max_length=255, db_index=True)
    mana_cost = models.CharField(max_length=128, blank=True, null=True)
    # Defined as a float in Scryfall's data - probably for unset cards
    cmc = models.FloatField(default=0.0, verbose_name="CMC")
    type_line = models.CharField(max_length=255, blank=True, null=True)
    oracle_text = models.TextField(blank=True, null=True)
    colors = models.JSONField(default=list)
    color_identity = models.JSONField(default=list)
    image_uri = models.URLField(
        max_length=512, blank=True, null=True, verbose_name="Image URI"
    )
    legalities = models.JSONField(default=dict)
    is_land = models.BooleanField(default=False, db_index=True)
    is_basic_land = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.id:
            import uuid

            self.id = str(uuid.uuid4())
        if not self.oracle_id:
            import uuid

            self.oracle_id = str(uuid.uuid4())
        if not self.normalized_name and self.name:
            self.normalized_name = normalize_card_name(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name

    @property
    def scryfall_url(self) -> str:
        """URL to the card on Scryfall."""
        return get_scryfall_url(name=self.name, card_id=self.id)

    @property
    def gatherer_url(self) -> str:
        """URL to the card on Gatherer."""
        return get_gatherer_url(self.name)

    def get_scryfall_url(self) -> str:
        """URL to the card on Scryfall."""
        return self.scryfall_url

    def get_gatherer_url(self) -> str:
        """URL to the card on Gatherer."""
        return self.gatherer_url

    def is_legal_in(self, format_name: str) -> bool:
        """Check legality in target format ('legal' or 'restricted' in Vintage)."""
        fmt = format_name.lower().strip()
        leg = self.legalities.get(fmt, "not_legal")
        if fmt == "vintage":
            return leg in ("legal", "restricted")
        return leg == "legal"


class CardLookup(models.Model):
    """
    Normalized alias to canonical card lookup.

    Handles alternate names of cards such as Universes Within,
    Through the Omenpaths, Godzilla, and some Secret Lair
    alternate card names.

    Data comes from Scryfall.
    """

    lookup_name = models.CharField(max_length=255, primary_key=True)
    canonical_name = models.CharField(max_length=255, db_index=True)
    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="lookups")
    priority = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Resolution weight used to break ties when multiple cards share an alias "
            "or name (higher values take precedence during card resolution)."
        ),
    )

    def __str__(self) -> str:
        return f"{self.lookup_name} -> {self.canonical_name}"
