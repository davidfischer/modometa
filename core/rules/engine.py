"""Declarative YAML Archetype Classification Engine with Tactical Posture Fallback."""

import logging
import re
from pathlib import Path
from typing import Any
from typing import Iterable

import yaml
from django.conf import settings

from core.models.card import expand_card_counts
from core.models.card import expand_card_names
from core.models.card import normalize_card_name
from core.rules.colors import deduce_deck_colors


logger = logging.getLogger(__name__)

# Key cards used to infer tactical posture for unclassified decks
FAST_MANA_CARDS = {
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

TEMPO_CARDS = {
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

CONTROL_CARDS = {
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

AGGRO_CARDS = {
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


def slugify_archetype(name: str) -> str:
    """Convert archetype name to clean URL slug."""
    s = name.lower().strip()
    s = re.sub(r"[/\\]", "-", s)
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


class ArchetypeEngine:
    """Evaluates decks against YAML archetype signatures with deterministic priority."""

    def __init__(self, archetypes_dir: Path | None = None):
        self.archetypes_dir = archetypes_dir or settings.ARCHETYPES_DIR
        self._rules_by_format: dict[str, list[dict[str, Any]]] = {}
        self.load_all_rules()

    def load_all_rules(self) -> None:
        """Load and normalize YAML rules for each format."""
        if not self.archetypes_dir.exists():
            logger.warning(
                "Archetypes directory %s does not exist", self.archetypes_dir
            )
            return

        for yaml_file in self.archetypes_dir.glob("*.yaml"):
            fmt = yaml_file.stem.lower()
            try:
                with open(yaml_file, "r", encoding="utf-8") as fp:
                    raw_rules = yaml.safe_load(fp) or []
            except Exception as e:
                logger.error("Error reading %s: %s", yaml_file, e)
                continue

            parsed_rules = []
            for r in raw_rules:
                mandatory = {}
                for item in r.get("mandatory", []):
                    if isinstance(item, dict):
                        c_name = item.get("card") or item.get("name") or ""
                        try:
                            min_cnt = int(item.get("min", item.get("count", 1)))
                        except (ValueError, TypeError):
                            min_cnt = 1
                    else:
                        c_name = str(item)
                        min_cnt = 1
                    norm = normalize_card_name(c_name)
                    if norm:
                        mandatory[norm] = min_cnt

                signatures = {normalize_card_name(c) for c in r.get("signatures", []) if c}
                anti_signatures = {
                    normalize_card_name(c) for c in r.get("anti_signatures", []) if c
                }
                min_sig = r.get("min_signatures", max(1, min(2, len(signatures))))
                priority = r.get("priority", 50)
                category = r.get("category", "Midrange")

                parsed_rules.append(
                    {
                        "name": r["name"],
                        "slug": slugify_archetype(r["name"]),
                        "category": category,
                        "priority": priority,
                        "mandatory": mandatory,
                        "signatures": signatures,
                        "min_signatures": min_sig,
                        "anti_signatures": anti_signatures,
                        "colors": r.get("colors"),
                    }
                )

            # Sort by priority descending
            parsed_rules.sort(key=lambda x: x["priority"], reverse=True)
            self._rules_by_format[fmt] = parsed_rules
            logger.info("Loaded %d archetype rules for %s", len(parsed_rules), fmt)

    def get_rules(self, format_name: str) -> list[dict[str, Any]]:
        """Return parsed rules for the given format."""
        return self._rules_by_format.get(format_name.lower().strip(), [])

    def classify(
        self,
        mainboard_cards: Iterable[Any],
        format_name: str,
        card_colors_map: dict[str, list[str]] | None = None,
        sideboard_cards: Iterable[Any] | None = None,
    ) -> tuple[str, str, str, str, bool, dict[str, Any]]:
        """Classify deck cards into archetype and color combination.

        Args:
            mainboard_cards: Iterable of mainboard cards (card names or dicts with card and count).
            format_name: Format to classify against.
            card_colors_map: Optional precomputed card -> colors mapping.
            sideboard_cards: Optional iterable of sideboard cards (e.g. companions).

        Returns:
            (archetype_name, archetype_slug, colors_code, color_display_name, is_fallback, debug_info)
        """
        fmt = format_name.lower().strip()
        mb_counts = expand_card_counts(mainboard_cards)
        sb_counts = expand_card_counts(sideboard_cards or [])
        total_counts = mb_counts + sb_counts

        norm_mainboard = set(mb_counts.keys())
        norm_cards = set(total_counts.keys())

        # Step 1: Deduce colors (based on mainboard mana base & spells)
        colors_code, color_display_name = deduce_deck_colors(
            norm_mainboard, card_colors_map
        )

        # Step 2: Try explicit YAML rules
        rules = self._rules_by_format.get(fmt, [])
        best_match = None
        best_score = -1
        debug_matches = []

        for rule in rules:
            # Check mandatory cards: each must meet minimum quantity
            if rule["mandatory"]:
                if any(
                    total_counts.get(req_card, 0) < min_cnt
                    for req_card, min_cnt in rule["mandatory"].items()
                ):
                    continue

            # Check anti_signatures: NONE may be present
            if rule["anti_signatures"] and any(
                c in norm_cards for c in rule["anti_signatures"]
            ):
                continue

            # Check signatures
            sig_hits = [c for c in rule["signatures"] if c in norm_cards]
            if len(sig_hits) >= rule["min_signatures"]:
                score = rule["priority"] * 10 + len(sig_hits)
                debug_matches.append(
                    {
                        "rule": rule["name"],
                        "hits": len(sig_hits),
                        "score": score,
                    }
                )
                if score > best_score:
                    best_score = score
                    best_match = rule

        if best_match:
            debug_info = {
                "matched_rule": best_match["name"],
                "score": best_score,
                "all_matches": debug_matches,
            }
            return (
                best_match["name"],
                best_match["slug"],
                colors_code,
                color_display_name,
                False,
                debug_info,
            )

        # Step 3: Heuristic Posture + Color Fallback
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

        debug_info = {
            "matched_rule": None,
            "fallback_posture": posture,
            "fallback_name": fallback_name,
        }

        return (
            fallback_name,
            fallback_slug,
            colors_code,
            color_display_name,
            True,
            debug_info,
        )
