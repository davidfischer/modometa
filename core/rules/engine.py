"""MTG Archetype Classification Engine using mtg-archetypes, TF-IDF kNN, and Tactical Posture Fallback."""

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from typing import Iterable

from django.conf import settings
from mtg_archetypes import ArchetypeClassifier
from mtg_archetypes import slugify_archetype

from core.engine.knn import get_global_knn_index
from core.models.card import expand_card_counts
from core.models.card import normalize_card_name
from core.rules.colors import deduce_deck_colors


logger = logging.getLogger(__name__)

# Key cards used to infer tactical posture for unclassified decks
FAST_MANA_CARDS = {
    normalize_card_name(c)
    for c in {
        "dark ritual",
        "lotus petal",
        "lion's eye diamond",
        "chrome mox",
        "mox opal",
        "mox diamond",
        "rite of flame",
        "simian spirit guide",
        "elvish spirit guide",
        "ancient tomb",
        "city of traitors",
        "bazaar of baghdad",
        "mishra's workshop",
    }
}

TEMPO_CARDS = {
    normalize_card_name(c)
    for c in {
        "daze",
        "wasteland",
        "stifle",
        "spell pierce",
        "snuff out",
        "delver of secrets",
        "psychic frog",
        "counterspell",
        "monastery swiftspear",
        "spellstutter sprite",
    }
}

CONTROL_CARDS = {
    normalize_card_name(c)
    for c in {
        "supreme verdict",
        "toxic deluge",
        "wrath of the skies",
        "wrath of god",
        "prismatic ending",
        "swords to plowshares",
        "the one ring",
        "counterbalance",
        "teferi, time raveler",
        "teferi, hero of dominaria",
        "jace, the mind sculptor",
        "force of will",
        "chainer's edict",
        "sunfall",
    }
}

AGGRO_CARDS = {
    normalize_card_name(c)
    for c in {
        "goblin guide",
        "monastery swiftspear",
        "slickshot show-off",
        "lightning bolt",
        "lava spike",
        "jackal pup",
        "ocelot pride",
        "guide of souls",
        "kuldotha rebirth",
        "slippery bogle",
    }
}


@dataclass(frozen=True)
class KNNMatchResult:
    """Result of kNN similarity search against rule-classified decks."""

    archetype_name: str
    archetype_slug: str
    similarity: float
    matched_deck_id: str

    def __iter__(self):
        yield self.archetype_name
        yield self.archetype_slug
        yield self.similarity
        yield self.matched_deck_id

    def __getitem__(self, index: int):
        return (
            self.archetype_name,
            self.archetype_slug,
            self.similarity,
            self.matched_deck_id,
        )[index]


@dataclass(frozen=True)
class FallbackPostureResult:
    """Result of tactical posture heuristic fallback."""

    posture: str
    archetype_name: str
    archetype_slug: str

    def __iter__(self):
        yield self.posture
        yield self.archetype_name
        yield self.archetype_slug

    def __getitem__(self, index: int):
        return (self.posture, self.archetype_name, self.archetype_slug)[index]


class ClassificationType(StrEnum):
    """Method by which a deck archetype was classified."""

    RULE = "rule"
    KNN = "knn"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class ClassificationResult:
    """Result of full archetype classification pipeline."""

    archetype_name: str | None
    archetype_slug: str | None
    colors_code: str
    color_display_name: str
    classification_type: ClassificationType | str | None
    debug_info: dict[str, Any]

    @property
    def is_rule(self) -> bool:
        """True if deck was matched by an explicit rule."""
        return self.classification_type == ClassificationType.RULE

    @property
    def is_knn(self) -> bool:
        """True if deck was matched via TF-IDF kNN similarity."""
        return self.classification_type == ClassificationType.KNN

    @property
    def is_fallback(self) -> bool:
        """Backwards compatibility property: True if deck was assigned tactical posture or unclassified."""
        return self.classification_type not in (
            ClassificationType.RULE,
            ClassificationType.KNN,
        )

    @property
    def classification_method(self) -> str | None:
        """Convenience property for classification method string."""
        if self.classification_type is not None:
            return str(self.classification_type)
        return self.debug_info.get("classification_method")

    @property
    def name(self) -> str | None:
        """Alias for archetype_name."""
        return self.archetype_name

    @property
    def slug(self) -> str | None:
        """Alias for archetype_slug."""
        return self.archetype_slug

    @property
    def colors(self) -> str:
        """Alias for colors_code."""
        return self.colors_code

    @property
    def color_name(self) -> str:
        """Alias for color_display_name."""
        return self.color_display_name

    def __iter__(self):
        yield self.archetype_name
        yield self.archetype_slug
        yield self.colors_code
        yield self.color_display_name
        yield self.classification_type
        yield self.debug_info

    def __getitem__(self, index: int):
        return (
            self.archetype_name,
            self.archetype_slug,
            self.colors_code,
            self.color_display_name,
            self.classification_type,
            self.debug_info,
        )[index]


class ArchetypeEngine:
    """Evaluates decks against mtg-archetypes rules, TF-IDF kNN closeness, and tactical posture fallback."""

    def __init__(
        self,
        archetypes_dir: Path | None = None,
        enable_knn: bool = True,
        knn_threshold: float = 0.85,
        enable_posture: bool = True,
    ):
        if archetypes_dir is None and getattr(settings, "configured", False):
            archetypes_dir = getattr(settings, "ARCHETYPES_DIR", None)
        self.classifier = ArchetypeClassifier(rules_dir=archetypes_dir)
        self.archetypes_dir = getattr(self.classifier, "rules_dir", archetypes_dir)
        self.enable_knn = enable_knn
        self.knn_threshold = knn_threshold
        self.enable_posture = enable_posture
        self._known_archetypes: dict[str, set[str]] = {}

    def load_all_rules(self) -> None:
        """Reload all rules from classifier."""
        self.classifier.load_all_rules()
        self._known_archetypes.clear()

    def get_known_archetypes(self, format_name: str) -> set[str]:
        """Return the set of known explicit archetype display names for a format."""
        fmt = format_name.lower().strip()
        if fmt not in self._known_archetypes:
            rules = self.classifier.get_rules(fmt)
            self._known_archetypes[fmt] = {r.name for r in rules}
        return self._known_archetypes[fmt]

    def get_rules(self, format_name: str) -> list[dict[str, Any]]:
        """Return parsed rules for the given format as dictionaries."""
        rules = self.classifier.get_rules(format_name)
        return [
            {
                "name": r.name,
                "slug": r.slug,
                "priority": r.priority,
                "category": r.category,
            }
            for r in rules
        ]

    def _find_knn_match(
        self,
        mainboard_cards: Iterable[Any],
        sideboard_cards: Iterable[Any] | None,
        format_name: str,
        knn_index: Any | None = None,
    ) -> KNNMatchResult | None:
        """Find the closest classified deck in the same format using TF-IDF index."""
        if knn_index is None:
            knn_index = get_global_knn_index()
        if knn_index is None or knn_index.tfidf_matrix is None:
            return None

        # Format input cards as expected by vector_from_decklist
        mb_items = [
            {
                "card": item.get("card", "") if isinstance(item, dict) else str(item),
                "count": int(item.get("count", 1)) if isinstance(item, dict) else 1,
            }
            for item in mainboard_cards
        ]
        sb_items = [
            {
                "card": item.get("card", "") if isinstance(item, dict) else str(item),
                "count": int(item.get("count", 1)) if isinstance(item, dict) else 1,
            }
            for item in (sideboard_cards or [])
        ]

        query_vec = knn_index.vector_from_decklist(mb_items, sb_items)
        known_archetypes = self.get_known_archetypes(format_name)
        match = knn_index.find_closest_classified(
            query_vec=query_vec,
            format_filter=format_name,
            known_archetypes=known_archetypes,
            min_similarity=self.knn_threshold,
            only_rule_classified=True,
        )
        if match is None:
            return None
        return KNNMatchResult(
            archetype_name=match[0],
            archetype_slug=match[1],
            similarity=match[2],
            matched_deck_id=match[3],
        )

    def find_knn_match(
        self,
        mainboard_cards: Iterable[Any],
        sideboard_cards: Iterable[Any] | None,
        format_name: str,
        knn_index: Any | None = None,
    ) -> KNNMatchResult | None:
        """Public method to find the closest classified deck in the same format using TF-IDF index."""
        return self._find_knn_match(
            mainboard_cards,
            sideboard_cards,
            format_name,
            knn_index=knn_index,
        )

    def _get_fallback_posture(
        self,
        cards: Iterable[Any],
        color_display_name: str,
    ) -> FallbackPostureResult:
        """Determine tactical posture (Aggro, Control, Combo, Tempo, Midrange) from cards."""
        if isinstance(cards, (set, frozenset)):
            norm_cards = cards
        elif isinstance(cards, dict):
            norm_cards = set(cards.keys())
        else:
            norm_cards = set(expand_card_counts(cards).keys())

        has_fast_mana = sum(1 for c in FAST_MANA_CARDS if c in norm_cards) >= 2
        has_tempo = sum(1 for c in TEMPO_CARDS if c in norm_cards) >= 2
        has_control = sum(1 for c in CONTROL_CARDS if c in norm_cards) >= 2
        has_aggro = sum(1 for c in AGGRO_CARDS if c in norm_cards) >= 2

        if has_fast_mana:
            posture = "Combo"
        elif has_tempo:
            posture = "Tempo"
        elif has_control:
            posture = "Control"
        elif has_aggro:
            posture = "Aggro"
        else:
            posture = "Midrange"

        fallback_name = f"{color_display_name} {posture}"
        fallback_slug = slugify_archetype(fallback_name)

        return FallbackPostureResult(
            posture=posture,
            archetype_name=fallback_name,
            archetype_slug=fallback_slug,
        )

    def get_fallback_posture(
        self,
        cards: Iterable[Any],
        color_display_name: str,
    ) -> FallbackPostureResult:
        """Public method to determine tactical posture fallback."""
        return self._get_fallback_posture(cards, color_display_name)

    def classify(
        self,
        mainboard_cards: Iterable[Any],
        format_name: str,
        card_colors_map: dict[str, list[str]] | None = None,
        sideboard_cards: Iterable[Any] | None = None,
        knn_index: Any | None = None,
    ) -> ClassificationResult:
        """Classify deck cards into archetype and color combination.

        Three-stage classification pipeline:
        1. Stage 1: Explicit rules from mtg-archetypes
        2. Stage 2: TF-IDF kNN closeness to a rule-classified deck (similarity >= knn_threshold)
        3. Stage 3: Heuristic Tactical Posture + Color fallback

        Returns:
            ClassificationResult containing archetype, colors, fallback flag, and debug info.
        """
        fmt = format_name.lower().strip()
        mb_counts = expand_card_counts(mainboard_cards)
        sb_counts = expand_card_counts(sideboard_cards or [])
        total_counts = mb_counts + sb_counts

        norm_mainboard = set(mb_counts.keys())

        # Step 1: Deduce colors (based on mainboard mana base & spells)
        colors_code, color_display_name = deduce_deck_colors(
            norm_mainboard, card_colors_map
        )

        # Step 2: Try explicit rules via mtg-archetypes
        clf_result = self.classifier.classify(
            mainboard_cards, sideboard_cards, format=fmt
        )
        if clf_result.matched and clf_result.name:
            debug_info = {
                "matched_rule": clf_result.matched_rule,
                "score": clf_result.priority * 10,
                "category": clf_result.category,
                "classification_method": "rule",
            }
            return ClassificationResult(
                archetype_name=clf_result.name,
                archetype_slug=clf_result.slug or slugify_archetype(clf_result.name),
                colors_code=colors_code,
                color_display_name=color_display_name,
                classification_type=ClassificationType.RULE,
                debug_info=debug_info,
            )

        # Step 3: TF-IDF kNN Closeness Fallback (only against rule-classified decks in same format)
        if self.enable_knn:
            knn_match = self._find_knn_match(
                mainboard_cards, sideboard_cards, fmt, knn_index=knn_index
            )
            if knn_match:
                debug_info = {
                    "matched_rule": None,
                    "matched_knn": knn_match.archetype_name,
                    "similarity": knn_match.similarity,
                    "matched_deck_id": knn_match.matched_deck_id,
                    "classification_method": "knn",
                }
                return ClassificationResult(
                    archetype_name=knn_match.archetype_name,
                    archetype_slug=knn_match.archetype_slug,
                    colors_code=colors_code,
                    color_display_name=color_display_name,
                    classification_type=ClassificationType.KNN,
                    debug_info=debug_info,
                )

        # Step 4: Heuristic Posture + Color Fallback
        if self.enable_posture:
            fallback = self._get_fallback_posture(total_counts, color_display_name)
            debug_info = {
                "matched_rule": None,
                "fallback_posture": fallback.posture,
                "fallback_name": fallback.archetype_name,
                "classification_method": "fallback",
            }
            return ClassificationResult(
                archetype_name=fallback.archetype_name,
                archetype_slug=fallback.archetype_slug,
                colors_code=colors_code,
                color_display_name=color_display_name,
                classification_type=ClassificationType.FALLBACK,
                debug_info=debug_info,
            )

        # Unclassified fallback when posture is disabled
        debug_info = {
            "matched_rule": None,
            "classification_method": None,
        }
        return ClassificationResult(
            archetype_name=None,
            archetype_slug=None,
            colors_code=colors_code,
            color_display_name=color_display_name,
            classification_type=None,
            debug_info=debug_info,
        )
