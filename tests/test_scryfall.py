import json

import pytest
from django.conf import settings

from core.models.card import Card
from core.pipeline.scryfall import NON_PLAYABLE_LAYOUTS
from core.pipeline.scryfall import PENALIZED_SETS
from core.pipeline.scryfall import USER_AGENT
from core.pipeline.scryfall import _calculate_print_rank
from core.pipeline.scryfall import _extract_card_faces
from core.pipeline.scryfall import _extract_card_name_and_type
from core.pipeline.scryfall import _extract_colors
from core.pipeline.scryfall import _extract_oracle_text
from core.pipeline.scryfall import ingest_scryfall_cards
from core.utils import get_user_agent


def test_user_agent_setting():
    assert hasattr(settings, "USER_AGENT")
    assert "Modometa" in settings.USER_AGENT
    assert USER_AGENT == settings.USER_AGENT
    assert get_user_agent() == settings.USER_AGENT


def test_print_rank_alpha_vs_beta():
    """Beta and later printings should be preferred over Alpha (lea) due to the set penalty."""
    alpha_underground_sea = {
        "id": "alpha-ugsea",
        "name": "Underground Sea",
        "set": "lea",
        "released_at": "1993-08-05",
        "reprint": False,
        "collector_number": "286",
    }
    beta_underground_sea = {
        "id": "beta-ugsea",
        "name": "Underground Sea",
        "set": "leb",
        "released_at": "1993-10-04",
        "reprint": True,
        "collector_number": "286",
    }
    unlimited_underground_sea = {
        "id": "2ed-ugsea",
        "name": "Underground Sea",
        "set": "2ed",
        "released_at": "1993-12-01",
        "reprint": True,
        "collector_number": "286",
    }

    alpha_swords = {
        "id": "alpha-stp",
        "name": "Swords to Plowshares",
        "set": "lea",
        "released_at": "1993-08-05",
        "reprint": False,
        "collector_number": "41",
    }
    beta_swords = {
        "id": "beta-stp",
        "name": "Swords to Plowshares",
        "set": "leb",
        "released_at": "1993-10-04",
        "reprint": True,
        "collector_number": "41",
    }

    # Beta beats Alpha
    assert _calculate_print_rank(beta_underground_sea) < _calculate_print_rank(
        alpha_underground_sea
    )
    assert _calculate_print_rank(beta_swords) < _calculate_print_rank(alpha_swords)

    # Unlimited beats Alpha (due to Alpha set penalty)
    assert _calculate_print_rank(unlimited_underground_sea) < _calculate_print_rank(
        alpha_underground_sea
    )
    # Beta beats Unlimited (earlier release date among non-penalized sets)
    assert _calculate_print_rank(beta_underground_sea) < _calculate_print_rank(
        unlimited_underground_sea
    )


def test_print_rank_regular_vs_variants():
    """Regular main set printing should be preferred over promo, showcase, and extended art."""
    # Guide of Souls: regular vs extended art vs prerelease promo
    guide_regular = {
        "id": "guide-reg",
        "name": "Guide of Souls",
        "set": "mh3",
        "released_at": "2024-06-14",
        "reprint": False,
        "collector_number": "29",
        "booster": True,
        "border_color": "black",
    }
    guide_extended = {
        "id": "guide-ea",
        "name": "Guide of Souls",
        "set": "mh3",
        "released_at": "2024-06-14",
        "reprint": False,
        "collector_number": "448",
        "booster": True,
        "frame_effects": ["extendedart"],
        "border_color": "borderless",
    }
    guide_promo = {
        "id": "guide-promo",
        "name": "Guide of Souls",
        "set": "pmh3",
        "released_at": "2024-06-07",  # Prerelease date earlier than main set
        "reprint": False,
        "collector_number": "29s",
        "promo": True,
        "promo_types": ["prerelease"],
    }

    assert _calculate_print_rank(guide_regular) < _calculate_print_rank(guide_extended)
    assert _calculate_print_rank(guide_regular) < _calculate_print_rank(guide_promo)

    # Voice of Victory: regular vs showcase
    voice_regular = {
        "id": "voice-reg",
        "name": "Voice of Victory",
        "set": "tdm",
        "released_at": "2025-04-11",
        "reprint": False,
        "collector_number": "33",
        "booster": True,
    }
    voice_showcase = {
        "id": "voice-sc",
        "name": "Voice of Victory",
        "set": "tdm",
        "released_at": "2025-04-11",
        "reprint": False,
        "collector_number": "331",
        "booster": True,
        "frame_effects": ["showcase"],
    }
    assert _calculate_print_rank(voice_regular) < _calculate_print_rank(voice_showcase)

    # Tamiyo, Inquisitive Student: regular vs bundle promo
    tamiyo_regular = {
        "id": "tamiyo-reg",
        "name": "Tamiyo, Inquisitive Student",
        "set": "mh3",
        "released_at": "2024-06-14",
        "reprint": False,
        "collector_number": "242",
        "booster": True,
    }
    tamiyo_bundle = {
        "id": "tamiyo-bundle",
        "name": "Tamiyo, Inquisitive Student",
        "set": "mh3",
        "released_at": "2024-06-14",
        "reprint": False,
        "collector_number": "443",
        "promo_types": ["bundle"],
    }
    assert _calculate_print_rank(tamiyo_regular) < _calculate_print_rank(tamiyo_bundle)


def test_print_rank_non_standard_layout():
    """Normal playable layout should beat art series, tokens, and reversible cards."""
    normal_card = {
        "id": "normal-id",
        "name": "Murktide Regent",
        "set": "mh2",
        "layout": "normal",
        "released_at": "2021-06-18",
        "reprint": False,
        "collector_number": "52",
    }
    art_series_card = {
        "id": "art-id",
        "name": "Murktide Regent",
        "set": "amh2",
        "layout": "art_series",
        "released_at": "2021-06-18",
        "reprint": False,
        "collector_number": "1",
    }
    assert _calculate_print_rank(normal_card) < _calculate_print_rank(art_series_card)


def test_print_rank_penalized_sets():
    """Sets in PENALIZED_SETS (lea, om1, omb) should be deprioritized below original set printings."""
    assert "lea" in PENALIZED_SETS
    assert "om1" in PENALIZED_SETS
    assert "omb" in PENALIZED_SETS

    peter_spm = {
        "id": "spm-10",
        "name": "Peter Parker // Amazing Spider-Man",
        "set": "spm",
        "set_name": "Marvel's Spider-Man",
        "released_at": "2025-09-26",
        "reprint": False,
        "collector_number": "10",
        "digital": False,
    }
    peter_omenpaths = {
        "id": "om1-21",
        "name": "Peter Parker // Amazing Spider-Man",
        "set": "om1",
        "set_name": "Through the Omenpaths",
        "released_at": "2025-09-23",  # earlier date than SPM
        "reprint": True,
        "collector_number": "21",
        "digital": True,
    }
    assert _calculate_print_rank(peter_spm) < _calculate_print_rank(peter_omenpaths)


def test_extract_card_name_and_type_split_vs_mdfc_vs_prepare():
    # Split card keeps combined name and type
    split_card = {
        "name": "Fire // Ice",
        "layout": "split",
        "type_line": "Instant // Instant",
        "card_faces": [
            {"name": "Fire", "type_line": "Instant"},
            {"name": "Ice", "type_line": "Instant"},
        ],
    }
    name, type_line = _extract_card_name_and_type(split_card)
    assert name == "Fire // Ice"
    assert type_line == "Instant // Instant"

    # MDFC uses front face
    mdfc_card = {
        "name": "Boggart Trawler // Boggart Bog",
        "layout": "modal_dfc",
        "type_line": "Creature — Goblin Assassin // Land",
        "card_faces": [
            {"name": "Boggart Trawler", "type_line": "Creature — Goblin Assassin"},
            {"name": "Boggart Bog", "type_line": "Land"},
        ],
    }
    name, type_line = _extract_card_name_and_type(mdfc_card)
    assert name == "Boggart Trawler"
    assert type_line == "Creature — Goblin Assassin"

    # Adventure uses front face
    adv_card = {
        "name": "Brazen Borrower // Petty Theft",
        "layout": "adventure",
        "type_line": "Creature — Faerie Rogue // Instant — Adventure",
        "card_faces": [
            {"name": "Brazen Borrower", "type_line": "Creature — Faerie Rogue"},
            {"name": "Petty Theft", "type_line": "Instant — Adventure"},
        ],
    }
    name, type_line = _extract_card_name_and_type(adv_card)
    assert name == "Brazen Borrower"
    assert type_line == "Creature — Faerie Rogue"

    # Prepare card uses front face
    prep_card = {
        "name": "Emeritus of Woe // Demonic Tutor",
        "layout": "prepare",
        "type_line": "Creature — Vampire Warlock // Sorcery",
        "card_faces": [
            {"name": "Emeritus of Woe", "type_line": "Creature — Vampire Warlock"},
            {"name": "Demonic Tutor", "type_line": "Sorcery"},
        ],
    }
    name, type_line = _extract_card_name_and_type(prep_card)
    assert name == "Emeritus of Woe"
    assert type_line == "Creature — Vampire Warlock"


def test_extract_card_faces_and_oracle_text():
    emeritus_card = {
        "name": "Emeritus of Woe // Demonic Tutor",
        "layout": "prepare",
        "oracle_text": None,
        "colors": ["B"],
        "card_faces": [
            {
                "name": "Emeritus of Woe",
                "mana_cost": "{3}{B}",
                "type_line": "Creature — Vampire Warlock",
                "oracle_text": "This creature enters prepared.",
                "power": "5",
                "toughness": "4",
            },
            {
                "name": "Demonic Tutor",
                "mana_cost": "{1}{B}",
                "type_line": "Sorcery",
                "oracle_text": "Search your library for a card.",
            },
        ],
    }
    faces = _extract_card_faces(emeritus_card)
    assert len(faces) == 2
    assert faces[0]["name"] == "Emeritus of Woe"
    assert faces[0]["power"] == "5"
    assert faces[0]["toughness"] == "4"
    assert faces[1]["name"] == "Demonic Tutor"
    assert faces[1]["type_line"] == "Sorcery"

    joined_text = _extract_oracle_text(emeritus_card)
    assert (
        joined_text
        == "This creature enters prepared.\n//\nSearch your library for a card."
    )


def test_extract_colors_fallback_for_transform():
    # Transform card with colors = None on top-level object
    delver_item = {
        "name": "Delver of Secrets // Insectile Aberration",
        "layout": "transform",
        "colors": None,
        "card_faces": [
            {"name": "Delver of Secrets", "colors": ["U"]},
            {"name": "Insectile Aberration", "colors": ["U"]},
        ],
    }
    assert _extract_colors(delver_item) == ["U"]


def test_non_playable_layouts_set():
    assert "art_series" in NON_PLAYABLE_LAYOUTS
    assert "token" in NON_PLAYABLE_LAYOUTS
    assert "double_faced_token" in NON_PLAYABLE_LAYOUTS
    assert "emblem" in NON_PLAYABLE_LAYOUTS
    assert "planar" in NON_PLAYABLE_LAYOUTS
    assert "scheme" in NON_PLAYABLE_LAYOUTS
    assert "vanguard" in NON_PLAYABLE_LAYOUTS
    assert "front_card" in NON_PLAYABLE_LAYOUTS


@pytest.mark.django_db
def test_ingest_skips_non_playable_cards(tmp_path):
    test_cards = [
        # 1. Playable card: Brainstorm
        {
            "id": "real-brainstorm-id",
            "oracle_id": "real-brainstorm-oracle",
            "name": "Brainstorm",
            "layout": "normal",
            "type_line": "Instant",
            "mana_cost": "{U}",
            "cmc": 1.0,
            "oracle_text": "Draw three cards, then put two cards from your hand on top of your library in any order.",
            "colors": ["U"],
            "color_identity": ["U"],
            "legalities": {"legacy": "legal", "vintage": "restricted"},
            "set": "ice",
            "set_name": "Ice Age",
            "collector_number": "61",
            "released_at": "1995-06-03",
        },
        # 2. Art series card with same name: Brainstorm
        {
            "id": "art-brainstorm-id",
            "oracle_id": "art-brainstorm-oracle",
            "name": "Brainstorm",
            "layout": "art_series",
            "type_line": "Card",
            "legalities": {},
            "set": "astx",
            "set_name": "Strixhaven Art Series",
            "collector_number": "65",
            "released_at": "2021-04-23",
        },
        # 3. Token
        {
            "id": "token-soldier-id",
            "oracle_id": "token-soldier-oracle",
            "name": "Soldier",
            "layout": "token",
            "type_line": "Token Creature — Soldier",
            "legalities": {},
            "set": "t10e",
            "set_name": "Tenth Edition Tokens",
            "collector_number": "1",
            "released_at": "2007-07-13",
        },
        # 4. Emblem
        {
            "id": "emblem-id",
            "oracle_id": "emblem-oracle",
            "name": "Liliana, the Last Hope Emblem",
            "layout": "emblem",
            "type_line": "Emblem — Liliana",
            "legalities": {},
            "set": "temn",
            "set_name": "Eldritch Moon Tokens",
            "collector_number": "8",
            "released_at": "2016-07-22",
        },
        # 5. Front card
        {
            "id": "front-card-id",
            "oracle_id": "front-card-oracle",
            "name": "Mordor",
            "layout": "front_card",
            "type_line": "Card",
            "legalities": {},
            "set": "fltr",
            "set_name": "Tales of Middle-earth Front Cards",
            "collector_number": "1",
            "released_at": "2023-06-23",
        },
        # 6. Blank / placeholder type line
        {
            "id": "blank-type-id",
            "oracle_id": "blank-type-oracle",
            "name": "Blank Card",
            "layout": "normal",
            "type_line": "Card",
            "legalities": {},
            "set": "smid",
            "set_name": "Innistrad Substitute Cards",
            "collector_number": "1",
            "released_at": "2021-09-24",
        },
    ]

    json_file = tmp_path / "scryfall_test.json"
    with open(json_file, "w", encoding="utf-8") as f:
        for card in test_cards:
            f.write(json.dumps(card) + "\n")

    card_count, lookup_count = ingest_scryfall_cards(json_file)
    assert card_count == 1
    assert Card.objects.count() == 1

    ingested = Card.objects.first()
    assert ingested.name == "Brainstorm"
    assert ingested.type_line == "Instant"
    assert ingested.id == "real-brainstorm-id"
