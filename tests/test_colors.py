"""Unit tests for color combination deduction and pseudo-cost filtering."""

from core.rules.colors import deduce_deck_colors


def test_mono_red_burn_not_black_with_surgical():
    """Mono-Red with Surgical Extraction in sideboard must NOT be classified as Black."""
    cards = [
        "Mountain",
        "Lightning Bolt",
        "Lava Spike",
        "Rift Bolt",
        "Monastery Swiftspear",
        "Surgical Extraction",  # Phyrexian B/P
    ]
    card_colors = {
        "lightning bolt": ["R"],
        "lava spike": ["R"],
        "rift bolt": ["R"],
        "monastery swiftspear": ["R"],
        "surgical extraction": ["B"],
    }
    code, name = deduce_deck_colors(cards, card_colors)
    assert code == "R"
    assert name == "Mono-Red"


def test_colorless_eldrazi_not_black_with_dismember():
    """Colorless Eldrazi with Dismember must NOT be classified as Black."""
    cards = [
        "Eldrazi Temple",
        "Eye of Ugin",
        "Wastes",
        "Thought-Knot Seer",
        "Reality Smasher",
        "Dismember",  # Phyrexian 1B/PB/P
    ]
    card_colors = {
        "thought-knot seer": [],
        "reality smasher": [],
        "dismember": ["B"],
    }
    code, name = deduce_deck_colors(cards, card_colors)
    assert code == "C"
    assert name == "Colorless"


def test_dimir_with_underground_sea():
    """Dimir Tempo with Underground Sea, Polluted Delta, Daze, Bowmasters."""
    cards = [
        "Underground Sea",
        "Polluted Delta",
        "Island",
        "Swamp",
        "Daze",
        "Orcish Bowmasters",
        "Brainstorm",
    ]
    card_colors = {
        "daze": ["U"],
        "orcish bowmasters": ["B"],
        "brainstorm": ["U"],
    }
    code, name = deduce_deck_colors(cards, card_colors)
    assert code == "UB"
    assert name == "Dimir"


def test_esper_control():
    """Esper Control with Tundra, Underground Sea, Scrubland."""
    cards = [
        "Tundra",
        "Underground Sea",
        "Scrubland",
        "Swords to Plowshares",
        "Counterspell",
        "Fatal Push",
    ]
    card_colors = {
        "swords to plowshares": ["W"],
        "counterspell": ["U"],
        "fatal push": ["B"],
    }
    code, name = deduce_deck_colors(cards, card_colors)
    assert code == "WUB"
    assert name == "Esper"


def test_faerie_macabre_does_not_add_black_to_mono_green():
    """Mono-Green with Faerie Macabre in sideboard remains Mono-Green."""
    cards = [
        "Forest",
        "Gaea's Cradle",
        "Heritage Druid",
        "Nettle Sentinel",
        "Faerie Macabre",  # Exiled from hand, not cast
    ]
    card_colors = {
        "heritage druid": ["G"],
        "nettle sentinel": ["G"],
        "faerie macabre": ["B"],
    }
    code, name = deduce_deck_colors(cards, card_colors)
    assert code == "G"
    assert name == "Mono-Green"
