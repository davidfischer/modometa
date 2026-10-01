"""Unit and integration tests for reclassify_decks management command."""

from datetime import date
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models.deck import ClassificationMethod
from core.models.deck import Deck
from core.models.tournament import Tournament


@pytest.fixture(autouse=True)
def configure_reclassify_rules(settings, test_rules_dir):
    settings.ARCHETYPES_DIR = test_rules_dir


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
    assert deck.archetype == "Test Tempo"
    assert deck.archetype_slug == "test-tempo"
    assert deck.is_auto_classified is False
    assert deck.classification_method == "rule"
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
        archetype="Test Tempo",
        archetype_slug="test-tempo",
        classification_method="rule",
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
        classification_method="fallback",
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

    assert (
        "Evaluating 1 decks into" in out.getvalue()
        and "archetype rules for Legacy (unclassified only)" in out.getvalue()
    )
    d1.refresh_from_db()
    assert d1.archetype == "Test Tempo"
    d2.refresh_from_db()
    assert d2.archetype == "Test Tempo"


@pytest.mark.django_db
def test_reclassify_unclassified_includes_knn_decks(sample_tournament):
    """--unclassified re-evaluates decks previously categorized with TF-IDF kNN."""
    # Deck 1: Rule-classified (should be skipped by --unclassified)
    d_rule = Deck.objects.create(
        id="deck_rule",
        tournament=sample_tournament,
        format="legacy",
        player="RulePlayer",
        player_lower="ruleplayer",
        result="5-0",
        archetype="Test Tempo",
        archetype_slug="test-tempo",
        is_auto_classified=False,
        classification_method="rule",
        colors="UR",
        color_name="Izzet",
        mainboard=[{"card": "Delver of Secrets", "count": 4}],
    )
    # Deck 2: Previously categorized via kNN, now matches an explicit rule
    d_knn_promoted = Deck.objects.create(
        id="deck_knn_promoted",
        tournament=sample_tournament,
        format="legacy",
        player="KnnPlayer1",
        player_lower="knnplayer1",
        result="5-0",
        archetype="Mono-Blue Midrange",
        archetype_slug="mono-blue-midrange",
        is_auto_classified=False,
        classification_method="knn",
        colors="UR",
        color_name="Izzet",
        mainboard=[
            {"card": "Delver of Secrets", "count": 4},
            {"card": "Daze", "count": 4},
            {"card": "Lightning Bolt", "count": 4},
            {"card": "Force of Will", "count": 4},
            {"card": "Volcanic Island", "count": 4},
            {"card": "Brainstorm", "count": 4},
        ],
    )
    # Deck 3: Previously categorized via kNN, but no longer matches and has no rule match
    d_knn_demoted = Deck.objects.create(
        id="deck_knn_demoted",
        tournament=sample_tournament,
        format="legacy",
        player="KnnPlayer2",
        player_lower="knnplayer2",
        result="5-0",
        archetype="Temur Delver",
        archetype_slug="temur-delver",
        is_auto_classified=False,
        classification_method="knn",
        colors="U",
        color_name="Mono-Blue",
        mainboard=[
            {"card": "Wastes", "count": 20},
            {"card": "Hedron Crawler", "count": 4},
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

    output = out.getvalue()
    assert (
        "Evaluating 2 decks into" in output
        and "archetype rules for Legacy (unclassified only)" in output
    )
    assert "Promoted from TF-IDF kNN to rule: 1" in output
    assert "Reverted from TF-IDF kNN to fallback posture: 1" in output

    d_rule.refresh_from_db()
    assert d_rule.classification_method == "rule"

    d_knn_promoted.refresh_from_db()
    assert d_knn_promoted.archetype == "Test Tempo"
    assert d_knn_promoted.classification_method == "rule"
    assert d_knn_promoted.is_auto_classified is False

    d_knn_demoted.refresh_from_db()
    assert d_knn_demoted.classification_method == "fallback"
    assert d_knn_demoted.is_auto_classified is True


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
    assert deck.archetype == "Test Companion"
    assert deck.archetype_slug == "test-companion"
    assert deck.colors == "W"


@pytest.mark.django_db
def test_show_archetype_counts_requires_format():
    """--show-archetype-counts without --format raises CommandError."""
    with pytest.raises(
        CommandError, match="--show-archetype-counts requires --format to be specified"
    ):
        call_command("reclassify_decks", show_archetype_counts=True)


@pytest.mark.django_db
def test_show_archetype_counts_output(sample_tournament):
    """--show-archetype-counts prints the archetype breakdown table with counts and totals."""
    Deck.objects.create(
        id="deck_izzet_delver",
        tournament=sample_tournament,
        format="legacy",
        player="Player1",
        player_lower="player1",
        result="5-0",
        archetype="Mono-Blue Midrange",
        archetype_slug="mono-blue-midrange",
        is_auto_classified=True,
        colors="UR",
        color_name="Izzet",
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
    # A deck that falls back to heuristic posture
    Deck.objects.create(
        id="deck_fallback",
        tournament=sample_tournament,
        format="legacy",
        player="Player2",
        player_lower="player2",
        result="3-2",
        archetype="Unknown Midrange",
        archetype_slug="unknown-midrange",
        is_auto_classified=True,
        colors="C",
        color_name="Colorless",
        mainboard=[
            {"card": "Wastes", "count": 20},
            {"card": "Hedron Crawler", "count": 4},
        ],
        sideboard=[],
    )

    out = StringIO()
    call_command(
        "reclassify_decks",
        format="legacy",
        show_archetype_counts=True,
        skip_knn=True,
        stdout=out,
    )

    output = out.getvalue()
    assert "Archetype Breakdown (Legacy):" in output
    assert "Test Tempo" in output
    assert "YAML Archetypes" in output
    assert "TF-IDF Classified" in output
    assert "Fallback (Auto-classified)" in output
    assert "Total Decks" in output


@pytest.mark.django_db
def test_show_archetype_counts_dry_run(sample_tournament):
    """--show-archetype-counts works during dry run mode without modifying database."""
    Deck.objects.create(
        id="deck_dry_delver",
        tournament=sample_tournament,
        format="legacy",
        player="Player1",
        player_lower="player1",
        result="5-0",
        archetype="Mono-Blue Midrange",
        archetype_slug="mono-blue-midrange",
        is_auto_classified=True,
        colors="UR",
        color_name="Izzet",
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
        "reclassify_decks",
        format="legacy",
        dry_run=True,
        show_archetype_counts=True,
        skip_knn=True,
        stdout=out,
    )

    output = out.getvalue()
    assert "Archetype Breakdown (Legacy):" in output
    assert "Test Tempo" in output
    assert "Dry run completed. No database changes were saved." in output


@pytest.mark.django_db
def test_reclassify_sets_fallback_method(sample_tournament):
    """Decks that do not match rules get classification_method='fallback'."""
    deck = Deck.objects.create(
        id="legacy_test_deck_fallback",
        tournament=sample_tournament,
        format="legacy",
        player="Brewmaster",
        player_lower="brewmaster",
        result="3-2",
        archetype="Old Name",
        archetype_slug="old-name",
        is_auto_classified=False,
        classification_method="rule",
        colors="C",
        color_name="Colorless",
        mainboard=[
            {"card": "Wastes", "count": 20},
            {"card": "Hedron Crawler", "count": 4},
        ],
        sideboard=[],
    )

    out = StringIO()
    call_command("reclassify_decks", format="legacy", skip_knn=True, stdout=out)

    deck.refresh_from_db()
    assert deck.is_auto_classified is True
    assert deck.classification_method == "fallback"


SHARED_DELVER_CARDS = [
    {"card": "Daze", "count": 4},
    {"card": "Lightning Bolt", "count": 4},
    {"card": "Force of Will", "count": 4},
    {"card": "Volcanic Island", "count": 4},
    {"card": "Brainstorm", "count": 4},
    {"card": "Ponder", "count": 4},
    {"card": "Wasteland", "count": 4},
    {"card": "Misty Rainforest", "count": 4},
    {"card": "Scalding Tarn", "count": 4},
    {"card": "Flooded Strand", "count": 4},
    {"card": "Spell Pierce", "count": 4},
    {"card": "Preordain", "count": 4},
    {"card": "Murktide Regent", "count": 4},
    {"card": "Brazen Borrower", "count": 2},
]


@pytest.mark.django_db
def test_pass2_runs_with_skip_knn(sample_tournament):
    """--skip-knn skips rebuilding the index at the end, but Pass 2 kNN matching still runs."""
    Deck.objects.create(
        id="deck_rule_izzet",
        tournament=sample_tournament,
        format="legacy",
        player="RulePlayer",
        player_lower="ruleplayer",
        result="5-0",
        archetype="Test Tempo",
        archetype_slug="test-tempo",
        is_auto_classified=False,
        classification_method="rule",
        colors="UR",
        color_name="Izzet",
        mainboard=[{"card": "Delver of Secrets", "count": 4}] + SHARED_DELVER_CARDS,
        sideboard=[],
    )
    # Similar deck that lacks Delver of Secrets (fails rule, falls back to posture in Pass 1)
    d_knn = Deck.objects.create(
        id="deck_knn_izzet",
        tournament=sample_tournament,
        format="legacy",
        player="KnnPlayer",
        player_lower="knnplayer",
        result="5-0",
        archetype="Mono-Blue Midrange",
        archetype_slug="mono-blue-midrange",
        is_auto_classified=True,
        classification_method="fallback",
        colors="UR",
        color_name="Izzet",
        mainboard=[{"card": "Dragon's Rage Channeler", "count": 4}]
        + SHARED_DELVER_CARDS,
        sideboard=[],
    )

    out = StringIO()
    call_command("reclassify_decks", format="legacy", skip_knn=True, stdout=out)

    output = out.getvalue()
    assert "Promoted via kNN: 1 decks" in output
    assert "Skipped kNN index rebuild (--skip-knn)" in output

    d_knn.refresh_from_db()
    assert d_knn.archetype == "Test Tempo"
    assert d_knn.classification_method == "knn"
    assert d_knn.is_auto_classified is False


@pytest.mark.django_db
def test_pass2_runs_with_dry_run(sample_tournament):
    """--dry-run runs Pass 2 kNN matching and reports promotions/breakdown without saving."""
    Deck.objects.create(
        id="deck_rule_izzet_dry",
        tournament=sample_tournament,
        format="legacy",
        player="RulePlayer",
        player_lower="ruleplayer",
        result="5-0",
        archetype="Test Tempo",
        archetype_slug="test-tempo",
        is_auto_classified=False,
        classification_method="rule",
        colors="UR",
        color_name="Izzet",
        mainboard=[{"card": "Delver of Secrets", "count": 4}] + SHARED_DELVER_CARDS,
        sideboard=[],
    )
    d_knn = Deck.objects.create(
        id="deck_knn_izzet_dry",
        tournament=sample_tournament,
        format="legacy",
        player="KnnPlayer",
        player_lower="knnplayer",
        result="5-0",
        archetype="Mono-Blue Midrange",
        archetype_slug="mono-blue-midrange",
        is_auto_classified=True,
        classification_method="fallback",
        colors="UR",
        color_name="Izzet",
        mainboard=[{"card": "Dragon's Rage Channeler", "count": 4}]
        + SHARED_DELVER_CARDS,
        sideboard=[],
    )

    out = StringIO()
    call_command(
        "reclassify_decks",
        format="legacy",
        dry_run=True,
        show_archetype_counts=True,
        stdout=out,
    )

    output = out.getvalue()
    assert "Promoted via kNN: 1 decks" in output
    assert "TF-IDF Classified" in output
    assert "Dry run completed. No database changes were saved." in output

    d_knn.refresh_from_db()
    assert d_knn.archetype == "Mono-Blue Midrange"
    assert d_knn.classification_method == "fallback"


@pytest.mark.django_db
def test_phantom_transitions_suppressed(sample_tournament):
    """Decks that remain the same archetype via kNN do not produce round-trip phantom transitions."""
    Deck.objects.create(
        id="deck_rule_ref",
        tournament=sample_tournament,
        format="legacy",
        player="RulePlayer",
        player_lower="ruleplayer",
        result="5-0",
        archetype="Test Tempo",
        archetype_slug="test-tempo",
        is_auto_classified=False,
        classification_method="rule",
        colors="UR",
        color_name="Izzet",
        mainboard=[{"card": "Delver of Secrets", "count": 4}] + SHARED_DELVER_CARDS,
        sideboard=[],
    )
    deck_already_knn = Deck.objects.create(
        id="deck_already_knn",
        tournament=sample_tournament,
        format="legacy",
        player="KnnPlayer",
        player_lower="knnplayer",
        result="5-0",
        archetype="Test Tempo",
        archetype_slug="test-tempo",
        is_auto_classified=False,
        classification_method=ClassificationMethod.KNN,
        colors="UR",
        color_name="Izzet",
        mainboard=[{"card": "Dragon's Rage Channeler", "count": 4}]
        + SHARED_DELVER_CARDS,
        sideboard=[],
    )

    out = StringIO()
    call_command("reclassify_decks", format="legacy", skip_knn=True, stdout=out)

    output = out.getvalue()
    assert "Test Tempo -> Izzet Tempo" not in output
    assert "Izzet Tempo -> Test Tempo" not in output
    assert "Reverted from TF-IDF kNN to fallback posture" not in output

    deck_already_knn.refresh_from_db()
    assert deck_already_knn.archetype == "Test Tempo"
    assert deck_already_knn.classification_method == ClassificationMethod.KNN
