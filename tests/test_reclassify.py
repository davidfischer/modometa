"""Unit and integration tests for reclassify_decks management command."""

from datetime import date
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models.deck import Deck
from core.models.tournament import Tournament


@pytest.fixture
def sample_tournament(db):
    return Tournament.objects.create(
        id="legacy_test_tourn_1",
        name="Legacy Challenge 32",
        format="legacy",
        event_type="challenge",
        date=date(2026, 1, 1),
    )


@pytest.mark.django_db
def test_reclassify_updates_archetype(sample_tournament):
    """Decks with updated card matches get classified and saved."""
    deck = Deck.objects.create(
        id="legacy_test_deck_1",
        tournament=sample_tournament,
        format="legacy",
        player="DelverFan",
        player_lower="delverfan",
        result="5-0",
        archetype="Mono-Blue Midrange",
        archetype_slug="mono-blue-midrange",
        is_auto_classified=True,
        colors="U",
        color_name="Mono-Blue",
        mainboard=[
            {"card": "Delver of Secrets", "count": 4},
            {"card": "Daze", "count": 4},
            {"card": "Lightning Bolt", "count": 4},
            {"card": "Force of Will", "count": 4},
            {"card": "Volcanic Island", "count": 4},
            {"card": "Brainstorm", "count": 4},
        ],
        sideboard=[],
    )

    out = StringIO()
    call_command("reclassify_decks", format="legacy", skip_knn=True, stdout=out)

    deck.refresh_from_db()
    assert deck.archetype == "Izzet Delver"
    assert deck.archetype_slug == "izzet-delver"
    assert deck.is_auto_classified is False
    assert "Decks updated: 1 / 1" in out.getvalue()
    assert "Classified previously unclassified decks: 1" in out.getvalue()


@pytest.mark.django_db
def test_reclassify_dry_run(sample_tournament):
    """Dry run should not persist changes to the database."""
    deck = Deck.objects.create(
        id="legacy_test_deck_dry",
        tournament=sample_tournament,
        format="legacy",
        player="DryRunner",
        player_lower="dryrunner",
        result="5-0",
        archetype="Mono-Blue Midrange",
        archetype_slug="mono-blue-midrange",
        is_auto_classified=True,
        colors="U",
        color_name="Mono-Blue",
        mainboard=[
            {"card": "Delver of Secrets", "count": 4},
            {"card": "Daze", "count": 4},
            {"card": "Lightning Bolt", "count": 4},
            {"card": "Force of Will", "count": 4},
            {"card": "Volcanic Island", "count": 4},
            {"card": "Brainstorm", "count": 4},
        ],
        sideboard=[],
    )

    out = StringIO()
    call_command(
        "reclassify_decks", format="legacy", dry_run=True, skip_knn=True, stdout=out
    )

    deck.refresh_from_db()
    assert deck.archetype == "Mono-Blue Midrange"
    assert deck.is_auto_classified is True
    assert "Dry run completed. No database changes were saved." in out.getvalue()


@pytest.mark.django_db
def test_reclassify_unclassified_filter(sample_tournament):
    """--unclassified flag only evaluates decks where is_auto_classified is True."""
    # Deck 1: Classified
    d1 = Deck.objects.create(
        id="deck_classified",
        tournament=sample_tournament,
        format="legacy",
        player="Player1",
        player_lower="player1",
        result="5-0",
        archetype="Izzet Delver",
        archetype_slug="izzet-delver",
        is_auto_classified=False,
        colors="UR",
        color_name="Izzet",
        mainboard=[{"card": "Delver of Secrets", "count": 4}],
    )
    # Deck 2: Auto-classified
    d2 = Deck.objects.create(
        id="deck_unclassified",
        tournament=sample_tournament,
        format="legacy",
        player="Player2",
        player_lower="player2",
        result="5-0",
        archetype="Mono-Blue Midrange",
        archetype_slug="mono-blue-midrange",
        is_auto_classified=True,
        colors="U",
        color_name="Mono-Blue",
        mainboard=[
            {"card": "Delver of Secrets", "count": 4},
            {"card": "Daze", "count": 4},
            {"card": "Lightning Bolt", "count": 4},
            {"card": "Force of Will", "count": 4},
            {"card": "Volcanic Island", "count": 4},
            {"card": "Brainstorm", "count": 4},
        ],
    )

    out = StringIO()
    call_command(
        "reclassify_decks",
        format="legacy",
        unclassified=True,
        skip_knn=True,
        stdout=out,
    )

    assert "Evaluating 1 decks in Legacy (unclassified only)" in out.getvalue()
    d1.refresh_from_db()
    assert d1.archetype == "Izzet Delver"
    d2.refresh_from_db()
    assert d2.archetype == "Izzet Delver"


@pytest.mark.django_db
def test_reclassify_invalid_format():
    """Passing an unknown format raises CommandError."""
    with pytest.raises(CommandError, match="Unknown format 'invalid_format'"):
        call_command("reclassify_decks", format="invalid_format")


@pytest.mark.django_db
def test_reclassify_matches_sideboard_companion(sample_tournament):
    """reclassify_decks takes sideboard cards into account, matching Yorion Death & Taxes."""
    deck = Deck.objects.create(
        id="legacy_test_yorion_dnt",
        tournament=sample_tournament,
        format="legacy",
        player="YorionEnjoyer",
        player_lower="yorionenjoyer",
        result="5-0",
        archetype="Death & Taxes",
        archetype_slug="death-taxes",
        is_auto_classified=False,
        colors="W",
        color_name="Mono-White",
        mainboard=[
            {"card": "Stoneforge Mystic", "count": 4},
            {"card": "Recruiter of the Guard", "count": 4},
            {"card": "Aether Vial", "count": 4},
            {"card": "Solitude", "count": 4},
            {"card": "Karakas", "count": 4},
            {"card": "Swords to Plowshares", "count": 4},
            {"card": "Plains", "count": 20},
        ],
        sideboard=[
            {"card": "Yorion, Sky Nomad", "count": 1},
            {"card": "Rest in Peace", "count": 2},
        ],
    )

    out = StringIO()
    call_command("reclassify_decks", format="legacy", skip_knn=True, stdout=out)

    deck.refresh_from_db()
    assert deck.archetype == "Yorion Death & Taxes"
    assert deck.archetype_slug == "yorion-death-taxes"
    assert deck.colors == "W"
