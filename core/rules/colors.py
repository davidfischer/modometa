"""Color combination detection and tactical posture inference.

Handles tricky MTG corner cases:
- Phyrexian mana cards (Surgical Extraction, Dismember, Mutagenic Growth)
- Pitch / free alternate cost / discard cards (Faerie Macabre, Simian Spirit Guide, Leylines)
- Mana base source evaluation (fetchlands, duals, basics, any-color sources)
"""

from typing import Iterable

from core.models.card import normalize_card_name


# Canonical MTG Color Combinations
COLOR_NAMES = {
    # 0 colors
    frozenset(): ("C", "Colorless"),
    # 1 color
    frozenset(["W"]): ("W", "Mono-White"),
    frozenset(["U"]): ("U", "Mono-Blue"),
    frozenset(["B"]): ("B", "Mono-Black"),
    frozenset(["R"]): ("R", "Mono-Red"),
    frozenset(["G"]): ("G", "Mono-Green"),
    # 2 colors (Guilds)
    frozenset(["W", "U"]): ("WU", "Azorius"),
    frozenset(["U", "B"]): ("UB", "Dimir"),
    frozenset(["B", "R"]): ("BR", "Rakdos"),
    frozenset(["R", "G"]): ("RG", "Gruul"),
    frozenset(["G", "W"]): ("WG", "Selesnya"),
    frozenset(["W", "B"]): ("WB", "Orzhov"),
    frozenset(["U", "R"]): ("UR", "Izzet"),
    frozenset(["B", "G"]): ("BG", "Golgari"),
    frozenset(["R", "W"]): ("RW", "Boros"),
    frozenset(["G", "U"]): ("GU", "Simic"),
    # 3 colors (Shards & Wedges)
    frozenset(["W", "U", "B"]): ("WUB", "Esper"),
    frozenset(["U", "B", "R"]): ("UBR", "Grixis"),
    frozenset(["B", "R", "G"]): ("BRG", "Jund"),
    frozenset(["R", "G", "W"]): ("WRG", "Naya"),
    frozenset(["G", "W", "U"]): ("WUG", "Bant"),
    frozenset(["W", "B", "G"]): ("WBG", "Abzan"),
    frozenset(["U", "R", "W"]): ("WUR", "Jeskai"),
    frozenset(["B", "G", "U"]): ("UBG", "Sultai"),
    frozenset(["R", "W", "B"]): ("WBR", "Mardu"),
    frozenset(["G", "U", "R"]): ("URG", "Temur"),
    # 4 colors
    frozenset(["U", "B", "R", "G"]): ("UBRG", "4-Color (Non-White)"),
    frozenset(["W", "B", "R", "G"]): ("WBRG", "4-Color (Non-Blue)"),
    frozenset(["W", "U", "R", "G"]): ("WURG", "4-Color (Non-Black)"),
    frozenset(["W", "U", "B", "G"]): ("WUBG", "4-Color (Non-Red)"),
    frozenset(["W", "U", "B", "R"]): ("WUBR", "4-Color (Non-Green)"),
    # 5 colors
    frozenset(["W", "U", "B", "R", "G"]): ("WUBRG", "5-Color"),
}

# Standard WUBRG sort order
COLOR_ORDER = {"W": 0, "U": 1, "B": 2, "R": 3, "G": 4}

# Cards with alternate costs, pitch effects, Phyrexian mana, or reanimation targets that
# must NEVER contribute to deck color identity.
PSEUDO_COLORED_CARDS = {
    # Phyrexian Mana
    "surgical extraction": {"B"},
    "dismember": {"B"},
    "gitaxian probe": {"U"},
    "mental misstep": {"U"},
    "mutagenic growth": {"G"},
    "gut shot": {"R"},
    "apostle's blessing": {"W"},
    "noxious revival": {"G"},
    "spellskite": {"U"},
    "vault scourge": {"B"},
    "porcelain legionnaire": {"W"},
    "moltensteel dragon": {"R"},
    "act of aggression": {"R"},
    "marrow shards": {"W"},
    "pith driller": {"B"},
    # Discard / Exile from hand for effect (not cast)
    "faerie macabre": {"B"},
    "simian spirit guide": {"R"},
    "elvish spirit guide": {"G"},
    "street wraith": {"B"},
    "chancellor of the annex": {"W"},
    "chancellor of the dross": {"B"},
    "chancellor of the forge": {"R"},
    "chancellor of the tangle": {"G"},
    "chancellor of the spires": {"U"},
    # Leylines (put directly onto battlefield)
    "leyline of the void": {"B"},
    "leyline of sanctity": {"W"},
    "leyline of anticipation": {"U"},
    "leyline of combustion": {"R"},
    "leyline of abundance": {"G"},
    "leyline of resonance": {"R"},
    "leyline of the guildpact": {"W", "U", "B", "R", "G"},
    "leyline of singularity": {"U"},
    "leyline of lifeforce": {"G"},
    "leyline of punishment": {"R"},
    "leyline of lightning": {"R"},
    # Pitch / Free Spells
    "mindbreak trap": {"U"},
    "once upon a time": {"G"},
    "land grant": {"G"},
    # Zero-cost Pacts
    "pact of negation": {"U"},
    "slaughter pact": {"B"},
    "pact of the titan": {"R"},
    "summoner's pact": {"G"},
    "intervention pact": {"W"},
    # Flashback / Alternate Cost
    "dread return": {"B"},
    # Creatures that are usually not cast
    "atraxa, grand unifier": {"W", "U", "B", "G"},
    "griselbrand": {"B"},
}

# Lands and mana producers mapping to the colors they produce or fetch
LAND_COLOR_MAP = {
    # Basics
    "plains": {"W"},
    "island": {"U"},
    "swamp": {"B"},
    "mountain": {"R"},
    "forest": {"G"},
    "snow-covered plains": {"W"},
    "snow-covered island": {"U"},
    "snow-covered swamp": {"B"},
    "snow-covered mountain": {"R"},
    "snow-covered forest": {"G"},
    # Fetchlands
    "flooded strand": {"W", "U"},
    "polluted delta": {"U", "B"},
    "bloodstained mire": {"B", "R"},
    "wooded foothills": {"R", "G"},
    "windswept heath": {"W", "G"},
    "marsh flats": {"W", "B"},
    "scalding tarn": {"U", "R"},
    "verdant catacombs": {"B", "G"},
    "arid mesa": {"W", "R"},
    "misty rainforest": {"U", "G"},
    "prismatic vista": {"W", "U", "B", "R", "G"},
    "fabled passage": {"W", "U", "B", "R", "G"},
    # Original Duals
    "tundra": {"W", "U"},
    "underground sea": {"U", "B"},
    "badlands": {"B", "R"},
    "taiga": {"R", "G"},
    "savannah": {"W", "G"},
    "scrubland": {"W", "B"},
    "volcanic island": {"U", "R"},
    "bayou": {"B", "G"},
    "plateau": {"W", "R"},
    "tropical island": {"U", "G"},
    # Shocklands
    "hallowed fountain": {"W", "U"},
    "watery grave": {"U", "B"},
    "blood crypt": {"B", "R"},
    "stomping ground": {"R", "G"},
    "temple garden": {"W", "G"},
    "godless shrine": {"W", "B"},
    "steam vents": {"U", "R"},
    "overgrown tomb": {"B", "G"},
    "sacred foundry": {"W", "R"},
    "breeding pool": {"U", "G"},
    # Surveil Lands
    "meticulous archive": {"W", "U"},
    "undercity sewers": {"U", "B"},
    "raucous theater": {"B", "R"},
    "commercial district": {"R", "G"},
    "lush portico": {"W", "G"},
    "shadowy backstreet": {"W", "B"},
    "thundering falls": {"U", "R"},
    "underground mortuary": {"B", "G"},
    "elegant parlor": {"W", "R"},
    "hedge maze": {"U", "G"},
    # Fastlands
    "seachrome coast": {"W", "U"},
    "darkslick shores": {"U", "B"},
    "blackcleave cliffs": {"B", "R"},
    "copperline gorge": {"R", "G"},
    "razorverge thicket": {"W", "G"},
    "concealed courtyard": {"W", "B"},
    "spirebluff canal": {"U", "R"},
    "blooming marsh": {"B", "G"},
    "inspiring vantage": {"W", "R"},
    "botanical sanctum": {"U", "G"},
    # Painlands
    "adarkar wastes": {"W", "U"},
    "underground river": {"U", "B"},
    "sulfurous springs": {"B", "R"},
    "karplusan forest": {"R", "G"},
    "brushland": {"W", "G"},
    "caves of koilos": {"W", "B"},
    "shivan reef": {"U", "R"},
    "llanowar wastes": {"B", "G"},
    "battlefield forge": {"W", "R"},
    "yavimaya coast": {"U", "G"},
    # Triomes
    "raugrin triome": {"U", "R", "W"},
    "savai triome": {"R", "W", "B"},
    "ketria triome": {"G", "U", "R"},
    "indatha triome": {"W", "B", "G"},
    "zagoth triome": {"B", "G", "U"},
    "spara's headquarters": {"G", "W", "U"},
    "raffine's tower": {"W", "U", "B"},
    "xander's lounge": {"U", "B", "R"},
    "ziatora's proving ground": {"B", "R", "G"},
    "jetmir's garden": {"R", "G", "W"},
    # Any-Color / Rainbow Lands
    "city of brass": {"W", "U", "B", "R", "G"},
    "mana confluence": {"W", "U", "B", "R", "G"},
    "gemstone mine": {"W", "U", "B", "R", "G"},
    "glimmervoid": {"W", "U", "B", "R", "G"},
    "spire of industry": {"W", "U", "B", "R", "G"},
    "cavern of souls": {"W", "U", "B", "R", "G"},
    "unclaimed territory": {"W", "U", "B", "R", "G"},
    "secluded courtyard": {"W", "U", "B", "R", "G"},
    "plaza of heroes": {"W", "U", "B", "R", "G"},
    "reflecting pool": {"W", "U", "B", "R", "G"},
    "forbidden orchard": {"W", "U", "B", "R", "G"},
    "tarnished citadel": {"W", "U", "B", "R", "G"},
    "aether hub": {"W", "U", "B", "R", "G"},
    # Artifact / Dork Mana Sources
    "black lotus": {"W", "U", "B", "R", "G"},
    "mox diamond": {"W", "U", "B", "R", "G"},
    "lotus petal": {"W", "U", "B", "R", "G"},
    "chrome mox": {"W", "U", "B", "R", "G"},
    "mox opal": {"W", "U", "B", "R", "G"},
    "mox amber": {"W", "U", "B", "R", "G"},
    "springleaf drum": {"W", "U", "B", "R", "G"},
    "birds of paradise": {"W", "U", "B", "R", "G"},
    "ignoble hierarch": {"B", "R", "G"},
    "noble hierarch": {"G", "W", "U"},
    "delighted halfling": {"W", "U", "B", "R", "G"},
    "deathrite shaman": {"B", "G"},
}


def deduce_deck_colors(
    mainboard_cards: Iterable[str],
    card_colors_map: dict[str, list[str]] | None = None,
) -> tuple[str, str]:
    """Deduce canonical color code (e.g. 'UB', 'WUBRG', 'C') and display name (e.g. 'Dimir').

    Uses a two-step validation:
    1. Detect colors producable by the mana base (lands + permanent mana sources).
    2. Detect colors required by genuine mainboard spells (excluding pseudo-colored / alternate cost cards).
    A color is confirmed if it is BOTH producable and castable, OR if mana base is pure mono-color.
    """
    card_colors_map = card_colors_map or {}
    norm_mainboard = [normalize_card_name(c) for c in mainboard_cards]

    # Step 1: Detect mana base production
    producible_colors: set[str] = set()
    has_any_color_source = False

    for card in norm_mainboard:
        if card in LAND_COLOR_MAP:
            colors = LAND_COLOR_MAP[card]
            if len(colors) == 5:
                has_any_color_source = True
            producible_colors.update(colors)

    # Step 2: Detect colors needed by castable spells
    needed_colors: set[str] = set()
    for card in norm_mainboard:
        # Excluded pseudo-colored cards never count toward deck colors
        if card in PSEUDO_COLORED_CARDS:
            continue

        # Otherwise check card colors
        cols = card_colors_map.get(card, [])
        for col in cols:
            needed_colors.add(col)

    # Step 3: Reconcile producible and needed
    if has_any_color_source:
        # With rainbow sources, needed colors take priority
        final_colors = needed_colors.intersection(set("WUBRG"))
    else:
        # Only colors that can be both produced and cast
        if producible_colors and needed_colors:
            final_colors = producible_colors.intersection(needed_colors)
        elif producible_colors:
            # E.g. all lands deck or lands control
            final_colors = producible_colors
        else:
            final_colors = needed_colors

    # Sort final colors in standard WUBRG order
    sorted_colors = sorted(list(final_colors), key=lambda c: COLOR_ORDER.get(c, 99))
    color_key = frozenset(sorted_colors)

    if color_key in COLOR_NAMES:
        return COLOR_NAMES[color_key]

    # 4+ colors fallback
    code = "".join(sorted_colors)
    if len(sorted_colors) == 4:
        return (code, "4-Color")
    if len(sorted_colors) >= 5:
        return ("WUBRG", "5-Color")
    if len(sorted_colors) == 0:
        return ("C", "Colorless")

    return (code, f"{code} Deck")
