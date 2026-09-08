"""Unit tests for archetype classification rules engine."""

import pytest

from core.rules.engine import ArchetypeEngine


@pytest.fixture
def engine():
    return ArchetypeEngine()


def test_mandatory_card_enforced(engine):
    """A Delver deck must have Delver of Secrets."""
    # Deck with Daze, Murktide, Lightning Bolt, Force of Will, but NO Delver
    cards = [
        "Daze",
        "Murktide Regent",
        "Lightning Bolt",
        "Force of Will",
        "Volcanic Island",
        "Brainstorm",
    ]
    name, slug, colors, color_name, is_fallback, debug = engine.classify(
        cards, "legacy"
    )
    # Should NOT be classified as Izzet Delver because Delver of Secrets is mandatory
    assert name != "Izzet Delver"


def test_mandatory_card_matched(engine):
    """When Delver of Secrets is included with signatures, matches Izzet Delver."""
    cards = [
        "Delver of Secrets",
        "Daze",
        "Lightning Bolt",
        "Force of Will",
        "Volcanic Island",
        "Brainstorm",
    ]
    name, slug, colors, color_name, is_fallback, debug = engine.classify(
        cards, "legacy"
    )
    assert name == "Izzet Delver"
    assert is_fallback is False


def test_anti_signatures_disqualification(engine):
    """Psychic Frog disqualifies a deck from Izzet Delver and classifies as Dimir Tempo."""
    cards = [
        "Psychic Frog",
        "Orcish Bowmasters",
        "Murktide Regent",
        "Daze",
        "Force of Will",
        "Underground Sea",
    ]
    name, slug, colors, color_name, is_fallback, debug = engine.classify(
        cards, "legacy"
    )
    assert name == "Dimir Tempo"
    assert is_fallback is False


def test_fallback_posture(engine):
    """An unknown deck without matched rules falls back to Colors + Tactical Posture."""
    cards = [
        "Mountain",
        "Goblin Guide",
        "Jackal Pup",
        "Lightning Bolt",
        "Lava Spike",
    ]
    name, slug, colors, color_name, is_fallback, debug = engine.classify(
        cards, "legacy"
    )
    assert "Mono-Red" in name
    assert is_fallback is True
    assert "Aggro" in name or "Tempo" in name


def test_dfc_canonical_name_matches_front_face_rule(engine):
    """Double-faced cards with canonical 'Front // Back' names must match rules written as 'Front'."""
    # Deck with canonical Scryfall DFC name 'Delver of Secrets // Insectile Aberration'
    cards = [
        "Delver of Secrets // Insectile Aberration",
        "Daze",
        "Lightning Bolt",
        "Force of Will",
        "Volcanic Island",
        "Dragon's Rage Channeler",
        "Murktide Regent",
    ]
    name, slug, colors, color_name, is_fallback, debug = engine.classify(
        cards, "legacy"
    )
    # Must match Izzet Delver, NOT Dimir Tempo
    assert name == "Izzet Delver"
    assert is_fallback is False


def test_dfc_anti_signatures_triggered_by_canonical_name(engine):
    """Anti-signature for 'delver of secrets' must be triggered by 'Delver of Secrets // Insectile Aberration'."""
    # Deck with both Dimir Tempo staples and Delver
    cards = [
        "Delver of Secrets // Insectile Aberration",
        "Daze",
        "Force of Will",
        "Murktide Regent",
        "Underground Sea",
        "Orcish Bowmasters",
    ]
    name, slug, colors, color_name, is_fallback, debug = engine.classify(
        cards, "legacy"
    )
    # Should NOT be classified as Dimir Tempo because Delver is an anti-signature
    assert name != "Dimir Tempo"


def test_expand_card_names():
    from core.models.card import expand_card_names

    cards = [
        "Delver of Secrets // Insectile Aberration",
        "Lightning Bolt",
        "Brazen Borrower // Petty Theft",
        "Fire // Ice",
    ]
    expanded = expand_card_names(cards)

    # Full canonical names
    assert "delver of secrets // insectile aberration" in expanded
    assert "brazen borrower // petty theft" in expanded
    assert "fire // ice" in expanded
    assert "lightning bolt" in expanded

    # Front and back faces
    assert "delver of secrets" in expanded
    assert "insectile aberration" in expanded
    assert "brazen borrower" in expanded
    assert "petty theft" in expanded
    assert "fire" in expanded
    assert "ice" in expanded


def test_sideboard_companion_matches_mandatory(engine):
    """Sideboard companion like Yorion satisfies mandatory rule for Yorion Death & Taxes."""
    dnt_mainboard = [
        "Stoneforge Mystic",
        "Recruiter of the Guard",
        "Aether Vial",
        "Solitude",
        "Karakas",
        "Swords to Plowshares",
        "Plains",
    ]

    # Without Yorion in sideboard -> regular Death & Taxes
    name_no_sb, _, colors_no_sb, _, is_fallback_no_sb, _ = engine.classify(
        dnt_mainboard, "legacy"
    )
    assert name_no_sb == "Death & Taxes"
    assert is_fallback_no_sb is False
    assert colors_no_sb == "W"

    # With Yorion in sideboard -> Yorion Death & Taxes
    name_sb, _, colors_sb, _, is_fallback_sb, _ = engine.classify(
        dnt_mainboard, "legacy", sideboard_cards=["Yorion, Sky Nomad", "Rest in Peace"]
    )
    assert name_sb == "Yorion Death & Taxes"
    assert is_fallback_sb is False
    # Mana base only produces White, so deck colors remain Mono-White
    assert colors_sb == "W"


def test_sideboard_anti_signatures_disqualification(engine):
    """Anti-signature card present in the sideboard disqualifies the archetype."""
    delver_mb = [
        "Delver of Secrets",
        "Daze",
        "Lightning Bolt",
        "Force of Will",
        "Volcanic Island",
        "Brainstorm",
    ]

    # Without Bowmasters -> Izzet Delver
    name_clean, _, _, _, _, _ = engine.classify(delver_mb, "legacy")
    assert name_clean == "Izzet Delver"

    # With Psychic Frog in sideboard -> Psychic Frog is anti-signature for Izzet Delver
    name_disqualified, _, _, _, _, _ = engine.classify(
        delver_mb, "legacy", sideboard_cards=["Psychic Frog"]
    )
    assert name_disqualified != "Izzet Delver"


def test_expand_card_counts():
    """Test expand_card_counts handles dicts, tuples, quantity strings, and multi-face cards."""
    from core.models.card import expand_card_counts

    cards = [
        {"card": "Delver of Secrets // Insectile Aberration", "count": 4},
        {"name": "Dragon's Rage Channeler", "count": 2},
        "4 Lightning Bolt",
        "2x Brainstorm",
        ("Force of Will", 4),
        "Ponder",
    ]
    counts = expand_card_counts(cards)

    assert counts["delver of secrets // insectile aberration"] == 4
    assert counts["delver of secrets"] == 4
    assert counts["insectile aberration"] == 4
    assert counts["dragons rage channeler"] == 2
    assert counts["lightning bolt"] == 4
    assert counts["brainstorm"] == 2
    assert counts["force of will"] == 4
    assert counts["ponder"] == 1


def test_mandatory_min_count_yaml_parsing_and_enforcement(tmp_path):
    """YAML rules with explicit min count are parsed and properly enforced across mainboard and sideboard."""
    yaml_content = """
- name: "Delver Test"
  category: "Tempo"
  mandatory:
    - card: "Delver of Secrets"
      min: 4
    - name: "Brainstorm"
      count: 2
    - "Ponder"
  signatures:
    - "Lightning Bolt"
  min_signatures: 1
"""
    (tmp_path / "testfmt.yaml").write_text(yaml_content, encoding="utf-8")
    engine = ArchetypeEngine(archetypes_dir=tmp_path)
    rules = engine.get_rules("testfmt")
    assert len(rules) == 1
    assert rules[0]["mandatory"] == {
        "delver of secrets": 4,
        "brainstorm": 2,
        "ponder": 1,
    }

    # Deck with only 3 Delver of Secrets -> fails mandatory minimum (4 required)
    deck_3_delver = [
        {"card": "Delver of Secrets", "count": 3},
        {"card": "Brainstorm", "count": 4},
        {"card": "Ponder", "count": 4},
        {"card": "Lightning Bolt", "count": 4},
        {"card": "Volcanic Island", "count": 4},
    ]
    name, _, _, _, is_fallback, _ = engine.classify(deck_3_delver, "testfmt")
    assert is_fallback is True
    assert name != "Delver Test"

    # Deck with 4 Delver of Secrets in mainboard -> matches
    deck_4_delver = [
        {"card": "Delver of Secrets", "count": 4},
        {"card": "Brainstorm", "count": 4},
        {"card": "Ponder", "count": 4},
        {"card": "Lightning Bolt", "count": 4},
        {"card": "Volcanic Island", "count": 4},
    ]
    name, _, _, _, is_fallback, _ = engine.classify(deck_4_delver, "testfmt")
    assert is_fallback is False
    assert name == "Delver Test"

    # Deck with 3 in mainboard + 1 in sideboard -> matches (total >= 4)
    sb = [{"card": "Delver of Secrets", "count": 1}]
    name_sb, _, _, _, is_fallback_sb, _ = engine.classify(
        deck_3_delver, "testfmt", sideboard_cards=sb
    )
    assert is_fallback_sb is False
    assert name_sb == "Delver Test"

    # Double-faced card canonical name with 4 copies -> matches
    deck_dfc = [
        {"card": "Delver of Secrets // Insectile Aberration", "count": 4},
        {"card": "Brainstorm", "count": 4},
        {"card": "Ponder", "count": 4},
        {"card": "Lightning Bolt", "count": 4},
        {"card": "Volcanic Island", "count": 4},
    ]
    name_dfc, _, _, _, is_fallback_dfc, _ = engine.classify(deck_dfc, "testfmt")
    assert is_fallback_dfc is False
    assert name_dfc == "Delver Test"


def test_mandatory_string_format_backward_compatibility(tmp_path):
    """String entries in mandatory rules default to min: 1."""
    yaml_content = """
- name: "Classic Delver"
  category: "Tempo"
  mandatory:
    - "delver of secrets"
  signatures:
    - "lightning bolt"
  min_signatures: 1
"""
    (tmp_path / "testfmt.yaml").write_text(yaml_content, encoding="utf-8")
    engine = ArchetypeEngine(archetypes_dir=tmp_path)
    rules = engine.get_rules("testfmt")
    assert rules[0]["mandatory"] == {"delver of secrets": 1}

    # 1 copy satisfies mandatory
    name, _, _, _, is_fallback, _ = engine.classify(
        [{"card": "Delver of Secrets", "count": 1}, {"card": "Lightning Bolt", "count": 4}],
        "testfmt",
    )
    assert is_fallback is False
    assert name == "Classic Delver"



