"""Unit tests for format autodetection engine using file-backed decklists."""

from pathlib import Path

import pytest

from core.decklist import parse_text_decklist
from core.models.card import Card
from core.rules.autodetect import FormatAutodetector


TESTDECKS_DIR = Path(__file__).parent / "testdecks"


@pytest.fixture
def detector():
    return FormatAutodetector()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "deck_filename,expected_format",
    [
        ("legacy_delver.txt", "legacy"),
        ("vintage_paradoxical_outcome.txt", "vintage"),
        ("vintage_workshop.txt", "vintage"),
        ("modern_amulet_titan.txt", "modern"),
        ("pioneer_phoenix.txt", "pioneer"),
        ("pauper_dimir_terror.txt", "pauper"),
        ("premodern_psychatog.txt", "premodern"),
        ("standard_red_deck_wins.txt", "standard"),
    ],
)
def test_autodetect_testdecks(detector, deck_filename, expected_format):
    """Verify that file-backed decklists in tests/testdecks autodetect the correct format."""
    deck_path = TESTDECKS_DIR / deck_filename
    assert deck_path.exists(), f"Test deck file not found: {deck_path}"

    mainboard, sideboard = parse_text_decklist(deck_path)
    assert len(mainboard) > 0, f"Failed to parse mainboard from {deck_filename}"

    detected_format, debug = detector.detect_format(mainboard, sideboard)
    assert detected_format == expected_format, (
        f"Expected {expected_format} for {deck_filename}, got {detected_format}. Diagnostics: {debug.get('diagnostics')}"
    )


@pytest.mark.django_db
def test_scryfall_dynamic_vintage_detection(detector):
    """Verify Vintage detection dynamically identifies cards banned in Legacy but legal/restricted in Vintage."""
    Card.objects.create(
        id="workshop-id",
        oracle_id="workshop-oracle",
        name="Mishra's Workshop",
        normalized_name="mishra's workshop",
        type_line="Land",
        legalities={
            "vintage": "legal",
            "legacy": "banned",
            "modern": "not_legal",
            "pioneer": "not_legal",
            "standard": "not_legal",
            "pauper": "not_legal",
            "premodern": "not_legal",
        },
    )
    Card.objects.create(
        id="lodestone-id",
        oracle_id="lodestone-oracle",
        name="Lodestone Golem",
        normalized_name="lodestone golem",
        type_line="Artifact Creature",
        legalities={
            "vintage": "restricted",
            "legacy": "legal",
            "modern": "legal",
            "pioneer": "not_legal",
            "standard": "not_legal",
            "pauper": "not_legal",
            "premodern": "not_legal",
        },
    )
    Card.objects.create(
        id="trinisphere-id",
        oracle_id="trinisphere-oracle",
        name="Trinisphere",
        normalized_name="trinisphere",
        type_line="Artifact",
        legalities={
            "vintage": "restricted",
            "legacy": "legal",
            "modern": "legal",
            "pioneer": "not_legal",
            "standard": "not_legal",
            "pauper": "not_legal",
            "premodern": "not_legal",
        },
    )

    # Mishra's Workshop is legal in Vintage, banned in Legacy (not Power 9)
    mb = [
        {"card": "Mishra's Workshop", "count": 4},
        {"card": "Lodestone Golem", "count": 1},
        {"card": "Trinisphere", "count": 1},
    ]
    fmt, debug = detector.detect_format(mb)
    assert fmt == "vintage"
    assert (
        debug["diagnostics"]["vintage"]["score"]
        > debug["diagnostics"]["legacy"]["score"]
    )


@pytest.mark.django_db
def test_decklist_parser_features():
    """Verify decklist parser handles sideboard headers, counts, comments, and set codes."""
    sample = """
    // Main deck
    4 Brainstorm (EMA) 40
    2 Murktide Regent [MH2]
    Lightning Bolt
    # Comment line

    Sideboard
    2 Pyroblast
    SB: 1 Surgical Extraction
    """
    mb, sb = parse_text_decklist(sample)

    assert mb == [
        {"card": "Brainstorm", "count": 4},
        {"card": "Murktide Regent", "count": 2},
        {"card": "Lightning Bolt", "count": 1},
    ]
    assert sb == [
        {"card": "Pyroblast", "count": 2},
        {"card": "Surgical Extraction", "count": 1},
    ]
