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
    normalize_card_name(k): v
    for k, v in {
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
        "phyrexian metamorph": {"U"},
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
    }.items()
}

# Lands and mana producers mapping to the colors they produce or fetch
LAND_COLOR_MAP = {
    normalize_card_name(k): v
    for k, v in {
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
        # NOTE: Fetchlands (Flooded Strand, Polluted Delta, Misty Rainforest, etc.)
        # are intentionally EXCLUDED. Fetchlands cannot produce mana on their own
        # and require an actual fetchable land in the deck. Excluding them prevents
        # off-color fetchlands from causing false-positive color classifications.
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
        # Tangolands / Battle lands
        "prairie stream": {"W", "U"},
        "sunken hollow": {"U", "B"},
        "smoldering marsh": {"B", "R"},
        "cinder glade": {"R", "G"},
        "canopy vista": {"W", "G"},
        # Bicycle / Cycling Duals
        "irrigated farmland": {"W", "U"},
        "fetid pools": {"U", "B"},
        "canyon slough": {"B", "R"},
        "sheltered thicket": {"R", "G"},
        "scattered groves": {"W", "G"},
        # Snow Duals
        "alpine meadow": {"W", "R"},
        "arctic treeline": {"W", "G"},
        "glacial floodplain": {"W", "U"},
        "highland forest": {"R", "G"},
        "ice tunnel": {"U", "B"},
        "rimewood falls": {"U", "G"},
        "snowfield sinkhole": {"W", "B"},
        "sulfurous mire": {"B", "R"},
        "volatile fjord": {"U", "R"},
        "woodland chasm": {"B", "G"},
        # Dominaria United Duals
        "idyllic beachfront": {"W", "U"},
        "contaminated aquifer": {"U", "B"},
        "geothermal bog": {"B", "R"},
        "wooded ridgeline": {"R", "G"},
        "radiant grove": {"W", "G"},
        "sunlit marsh": {"W", "B"},
        "molten tributary": {"U", "R"},
        "haunted mire": {"B", "G"},
        "sacred peaks": {"W", "R"},
        "tangled islet": {"U", "G"},
        # Eldraine Type Lands & Dryad Arbor
        "dryad arbor": {"G"},
        "mystic sanctuary": {"U"},
        "witch's cottage": {"B"},
        "dwarven mine": {"R"},
        "gingerbread cabin": {"G"},
        "idyllic grange": {"W"},
        # Creature Lands & Restless Lands
        "celestial colonnade": {"W", "U"},
        "creeping tar pit": {"U", "B"},
        "lavaclaw reaches": {"B", "R"},
        "raging ravine": {"R", "G"},
        "stirring wildwood": {"W", "G"},
        "shambling vent": {"W", "B"},
        "wandering fumarole": {"U", "R"},
        "hissing quagmire": {"B", "G"},
        "needle spires": {"W", "R"},
        "lumbering falls": {"U", "G"},
        "restless anchorage": {"W", "U"},
        "restless reef": {"U", "B"},
        "restless cottage": {"B", "G"},
        "restless bivouac": {"W", "R"},
        "restless spire": {"U", "R"},
        "restless vents": {"B", "R"},
        "restless prairie": {"W", "G"},
        "restless vinestalk": {"U", "G"},
        "restless fortress": {"W", "B"},
        "restless ridgeline": {"R", "G"},
        # Bouncelands
        "azorius chancery": {"W", "U"},
        "dimir aqueduct": {"U", "B"},
        "rakdos carnarium": {"B", "R"},
        "gruul turf": {"R", "G"},
        "selesnya sanctuary": {"W", "G"},
        "orzhov basilica": {"W", "B"},
        "izzet boilerworks": {"U", "R"},
        "golgari rot farm": {"B", "G"},
        "boros garrison": {"W", "R"},
        "simic growth chamber": {"U", "G"},
        # Checklands
        "glacial fortress": {"W", "U"},
        "drowned catacomb": {"U", "B"},
        "dragonskull summit": {"B", "R"},
        "rootbound crag": {"R", "G"},
        "sunpetal grove": {"W", "G"},
        "isolated chapel": {"W", "B"},
        "sulfur falls": {"U", "R"},
        "woodland cemetery": {"B", "G"},
        "clifftop retreat": {"W", "R"},
        "hinterland harbor": {"U", "G"},
        # Horizon / Canopy Lands
        "horizon canopy": {"W", "G"},
        "fiery islet": {"U", "R"},
        "nurturing peatland": {"B", "G"},
        "silent clearing": {"W", "B"},
        "sunbaked canyon": {"W", "R"},
        "waterlogged grove": {"U", "G"},
        # Slowlands
        "deserted beach": {"W", "U"},
        "shipwreck marsh": {"U", "B"},
        "haunted ridge": {"B", "R"},
        "rockfall vale": {"R", "G"},
        "overgrown farmland": {"W", "G"},
        "shattered sanctum": {"W", "B"},
        "stormcarved coast": {"U", "R"},
        "deathcap glade": {"B", "G"},
        "sundown pass": {"W", "R"},
        "dreamroot cascade": {"U", "G"},
        # Artifact Lands & Bridges
        "ancient den": {"W"},
        "seat of the synod": {"U"},
        "vault of whispers": {"B"},
        "great furnace": {"R"},
        "tree of tales": {"G"},
        "razortide bridge": {"W", "U"},
        "mistvault bridge": {"U", "B"},
        "drossforge bridge": {"B", "R"},
        "slagwoods bridge": {"R", "G"},
        "thornglint bridge": {"W", "G"},
        "goldmire bridge": {"W", "B"},
        "silverbluff bridge": {"U", "R"},
        "darkmoss bridge": {"B", "G"},
        "rustvale bridge": {"W", "R"},
        "tanglepool bridge": {"U", "G"},
        # Channel Lands
        "eiganjo, seat of the empire": {"W"},
        "otawara, soaring city": {"U"},
        "takenuma, abandoned mire": {"B"},
        "sokenzan, crucible of defiance": {"R"},
        "boseiju, who endures": {"G"},
        # Pathways
        "barkchannel pathway // tidechannel pathway": {"U", "G"},
        "blightstep pathway // searstep pathway": {"B", "R"},
        "branchloft pathway // boulderloft pathway": {"W", "G"},
        "brightclimb pathway // grimclimb pathway": {"W", "B"},
        "clearwater pathway // murkwater pathway": {"U", "B"},
        "cragcrown pathway // timbercrown pathway": {"R", "G"},
        "darkbore pathway // slitherbore pathway": {"B", "G"},
        "hengegate pathway // mistgate pathway": {"W", "U"},
        "needleverge pathway // pillarverge pathway": {"W", "R"},
        "riverglide pathway // lavaglide pathway": {"U", "R"},
        # Filter Lands
        "mystic gate": {"W", "U"},
        "sunken ruins": {"U", "B"},
        "graven cairns": {"B", "R"},
        "fire-lit thicket": {"R", "G"},
        "wooded bastion": {"W", "G"},
        "fetid heath": {"W", "B"},
        "cascade bluffs": {"U", "R"},
        "twilight mire": {"B", "G"},
        "rugged prairie": {"W", "R"},
        "flooded grove": {"U", "G"},
        # Notable Utility Lands
        "karakas": {"W"},
        "tolarian academy": {"U"},
        "gaea's cradle": {"G"},
        "valakut, the molten pinnacle": {"R"},
        "lake of the dead": {"B"},
        "phyrexian tower": {"B"},
        "cabal coffers": {"B"},
        "castle locthwain": {"B"},
        "castle vantress": {"U"},
        "castle ardenvale": {"W"},
        "castle embereth": {"R"},
        "castle garenbrig": {"G"},
        "bojuka bog": {"B"},
        "khalni garden": {"G"},
        "barren moor": {"B"},
        "lonely sandbar": {"U"},
        "secluded steppe": {"W"},
        "forgotten cave": {"R"},
        "tranquil thicket": {"G"},
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
    }.items()
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
    for orig_card, card in zip(mainboard_cards, norm_mainboard, strict=False):
        # Excluded pseudo-colored cards never count toward deck colors
        if card in PSEUDO_COLORED_CARDS:
            continue

        # Otherwise check card colors (check normalized name first, then fallback to raw or lower)
        cols = card_colors_map.get(card)
        if cols is None and isinstance(orig_card, str):
            cols = card_colors_map.get(orig_card) or card_colors_map.get(
                orig_card.lower(), []
            )
        for col in cols or []:
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
