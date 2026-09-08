"""Unit tests for Scryfall-backed legality engine."""

import pytest

from core.models.card import Card
from core.rules.legality import LegalityEngine


@pytest.fixture
def legality():
    return LegalityEngine()


@pytest.mark.django_db
def test_legal_deck_passes(legality):
    """A standard 60-card legal Legacy deck passes legality check."""
    mainboard = [
        {"card": "Delver of Secrets", "count": 4},
        {"card": "Daze", "count": 4},
        {"card": "Force of Will", "count": 4},
        {"card": "Brainstorm", "count": 4},
        {"card": "Ponder", "count": 4},
        {"card": "Lightning Bolt", "count": 4},
        {"card": "Murktide Regent", "count": 2},
        {"card": "Dragon's Rage Channeler", "count": 4},
        {"card": "Wasteland", "count": 4},
        {"card": "Volcanic Island", "count": 4},
        {"card": "Scalding Tarn", "count": 4},
        {"card": "Flooded Strand", "count": 4},
        {"card": "Island", "count": 14},
    ]
    sideboard = [
        {"card": "Red Elemental Blast", "count": 2},
        {"card": "Surgical Extraction", "count": 2},
    ]
    is_legal, errors, illegal_cards = legality.validate_deck(
        mainboard, sideboard, "legacy"
    )
    assert is_legal is True
    assert len(errors) == 0


@pytest.mark.django_db
def test_banned_card_fails(legality):
    """Deck with Grief in Legacy is flagged as not legal."""
    Card.objects.create(
        id="test-grief",
        oracle_id="test-grief-oracle",
        name="Grief",
        normalized_name="grief",
        legalities={"legacy": "banned", "vintage": "legal"},
    )
    mainboard = [
        {"card": "Grief", "count": 4},
        {"card": "Reanimate", "count": 4},
        {"card": "Swamp", "count": 52},
    ]
    sideboard = []
    is_legal, errors, illegal_cards = legality.validate_deck(
        mainboard, sideboard, "legacy"
    )
    assert is_legal is False
    assert any("Grief is not legal or is banned" in e for e in errors)


@pytest.mark.django_db
def test_vintage_restricted_limit(legality):
    """Deck with 2 copies of Black Lotus in Vintage is flagged as restricted."""
    Card.objects.create(
        id="test-lotus",
        oracle_id="test-lotus-oracle",
        name="Black Lotus",
        normalized_name="black lotus",
        legalities={"vintage": "restricted"},
    )
    mainboard = [
        {"card": "Black Lotus", "count": 2},
        {"card": "Island", "count": 58},
    ]
    sideboard = []
    is_legal, errors, illegal_cards = legality.validate_deck(
        mainboard, sideboard, "vintage"
    )
    assert is_legal is False
    assert any("restricted" in e.lower() for e in errors)


@pytest.mark.django_db
def test_alternate_name_resolution(legality):
    """CardLookup resolves alternate printed/flavor names to the canonical card."""
    card = Card.objects.create(
        id="test-hide-on-ceiling",
        oracle_id="test-hide-oracle",
        name="Hide on the Ceiling",
        normalized_name="hide on the ceiling",
        legalities={"legacy": "legal", "vintage": "legal"},
    )
    from core.models.card import CardLookup

    CardLookup.objects.create(
        lookup_name="spectral restitching",
        canonical_name="Hide on the Ceiling",
        card=card,
        priority=110,
    )

    resolved = legality.resolve_card("Spectral Restitching")
    assert resolved is not None
    assert resolved.name == "Hide on the Ceiling"


@pytest.mark.django_db
def test_consolidate_deck_entries():
    """Duplicate cards across split entries are consolidated into single line counts."""
    from core.pipeline.ingest import _consolidate_deck_entries

    raw = [
        {"card": "Brainstorm", "count": 2},
        {"card": "Force of Will", "count": 4},
        {"card": "Brainstorm", "count": 2},
    ]
    consolidated = _consolidate_deck_entries(raw)
    assert len(consolidated) == 2
    by_name = {item["card"]: item["count"] for item in consolidated}
    assert by_name["Brainstorm"] == 4
    assert by_name["Force of Will"] == 4
