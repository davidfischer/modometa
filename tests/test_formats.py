"""Tests for format validation, choices, check constraints, and rejection of unsupported formats."""

from datetime import date
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.utils import IntegrityError

from core.formats import Format
from core.formats import is_valid_format
from core.models.deck import Deck
from core.models.tournament import Tournament
from core.pipeline.ingest import deduce_format_and_type


def test_is_valid_format():
    assert is_valid_format("modern") is True
    assert is_valid_format("LEGACY") is True
    assert is_valid_format("commander") is False
    assert is_valid_format("brawl") is False
    assert is_valid_format("") is False
    assert is_valid_format(None) is False


def test_deduce_format_rejects_unsupported():
    # Unsupported formats
    fmt, _ = deduce_format_and_type(
        "commander-league-2026-01-01.json", {"Name": "Commander League"}
    )
    assert fmt is None

    fmt, _ = deduce_format_and_type(
        "duel-commander-challenge-2026-01-01.json", {"Name": "Duel Commander Challenge"}
    )
    assert fmt is None

    # Limited / Draft / Sealed rejected even if containing modern/standard in name
    fmt, _ = deduce_format_and_type(
        "modern-masters-draft-2026-01-01.json", {"Name": "Modern Masters Draft"}
    )
    assert fmt is None

    fmt, _ = deduce_format_and_type(
        "standard-sealed-preliminary-2026-01-01.json", {"Name": "Standard Sealed"}
    )
    assert fmt is None

    # Valid supported formats
    fmt, evt = deduce_format_and_type(
        "modern-challenge-32-2026-01-01.json", {"Name": "Modern Challenge 32"}
    )
    assert fmt == "modern"
    assert evt == "challenge"


@pytest.mark.django_db
def test_tournament_model_rejects_unsupported_format():
    tourn = Tournament(
        id="tourn_unsupported",
        name="Commander Gala",
        format="commander",
        event_type="league",
        date=date(2026, 1, 1),
    )
    # Model validation rejection
    with pytest.raises(ValidationError):
        tourn.full_clean()

    # DB CheckConstraint rejection
    with pytest.raises(IntegrityError):
        tourn.save()


@pytest.mark.django_db
def test_deck_model_rejects_unsupported_format():
    valid_tourn = Tournament.objects.create(
        id="tourn_valid_format",
        name="Modern Event",
        format=Format.MODERN,
        event_type="league",
        date=date(2026, 1, 1),
    )
    deck = Deck(
        id="deck_unsupported",
        tournament=valid_tourn,
        format="historic",
        player="PlayerOne",
        player_lower="playerone",
        result="5-0",
        archetype="Unknown",
        archetype_slug="unknown",
    )
    # Model validation rejection
    with pytest.raises(ValidationError):
        deck.full_clean()

    # DB CheckConstraint rejection
    with pytest.raises(IntegrityError):
        deck.save()


@pytest.mark.django_db
def test_ingest_tournaments_command_rejects_unsupported_format():
    with pytest.raises(CommandError, match="Unsupported format 'commander'"):
        call_command("ingest_tournaments", format="commander")


@pytest.mark.django_db
def test_classify_deck_command_rejects_unsupported_format():
    err = StringIO()
    call_command(
        "classify_deck",
        file="tests/testdecks/legacy_delver.txt",
        format="commander",
        stderr=err,
    )
    assert "Unsupported format 'commander'" in err.getvalue()


@pytest.mark.django_db
def test_discover_archetypes_command_rejects_unsupported_format():
    with pytest.raises(CommandError, match="Unsupported format 'commander'"):
        call_command("discover_archetypes", format="commander")


@pytest.mark.django_db
def test_discover_archetypes_errors_on_empty_card_database():
    from core.models.card import Card, CardLookup
    CardLookup.objects.all().delete()
    Card.objects.all().delete()
    with pytest.raises(CommandError, match="No cards found in database"):
        call_command("discover_archetypes", format="legacy")


@pytest.mark.django_db
def test_discover_archetypes_errors_on_unrecognized_yaml_card(monkeypatch, tmp_path):
    import yaml
    from django.conf import settings
    from core.models.card import Card

    Card.objects.create(name="Force of Will", normalized_name="force of will")
    Card.objects.create(name="Brainstorm", normalized_name="brainstorm")

    # Create dummy yaml with unrecognized card
    yaml_dir = tmp_path / "archetypes"
    yaml_dir.mkdir()
    yaml_file = yaml_dir / "legacy.yaml"
    rules = [
        {
            "name": "Izzet Delver",
            "signatures": ["force of will", "invalid doodle card"],
        }
    ]
    with open(yaml_file, "w") as fp:
        yaml.safe_dump(rules, fp)

    monkeypatch.setattr(settings, "ARCHETYPES_DIR", yaml_dir)

    with pytest.raises(CommandError, match="Unrecognized card 'invalid doodle card'"):
        call_command("discover_archetypes", format="legacy")


@pytest.mark.django_db
def test_discover_archetypes_errors_on_unrecognized_deck_card(monkeypatch, tmp_path):
    import yaml
    from django.conf import settings
    from core.models.card import Card
    from core.models.deck import Deck
    from core.models.tournament import Tournament

    Card.objects.create(name="Force of Will", normalized_name="force of will")

    # Valid yaml
    yaml_dir = tmp_path / "archetypes"
    yaml_dir.mkdir()
    yaml_file = yaml_dir / "legacy.yaml"
    rules = [
        {
            "name": "Delver",
            "signatures": ["force of will"],
        }
    ]
    with open(yaml_file, "w") as fp:
        yaml.safe_dump(rules, fp)

    monkeypatch.setattr(settings, "ARCHETYPES_DIR", yaml_dir)

    t = Tournament.objects.create(
        id="tourn-test-1", name="Legacy Challenge", format="legacy", date=date(2024, 1, 1)
    )
    Deck.objects.create(
        id="deck-test-1",
        tournament=t,
        format="legacy",
        player="Tester",
        player_lower="tester",
        archetype="Unknown",
        archetype_slug="unknown",
        is_auto_classified=True,
        mainboard=[{"card": "Bogus Unknown Card", "count": 4}],
        sideboard=[],
    )

    with pytest.raises(CommandError, match="Unrecognized card 'Bogus Unknown Card'"):
        call_command("discover_archetypes", format="legacy", min_cluster=1)


@pytest.mark.django_db
def test_discover_archetypes_sorted_by_cluster_size(monkeypatch, tmp_path):
    from io import StringIO
    import yaml
    from django.conf import settings
    from core.models.card import Card
    from core.models.deck import Deck
    from core.models.tournament import Tournament

    cards = [
        "Brainstorm",
        "Force of Will",
        "Ponder",
        "Lightning Bolt",
        "Chain Lightning",
        "Lava Spike",
    ]
    for c in cards:
        Card.objects.create(name=c, normalized_name=c.lower())

    yaml_dir = tmp_path / "archetypes"
    yaml_dir.mkdir()
    yaml_file = yaml_dir / "legacy.yaml"
    with open(yaml_file, "w") as fp:
        yaml.safe_dump([], fp)

    monkeypatch.setattr(settings, "ARCHETYPES_DIR", yaml_dir)

    t = Tournament.objects.create(
        id="tourn-sort-test", name="Legacy Challenge", format="legacy", date=date(2024, 1, 1)
    )

    # 3 Burn decks (larger cluster)
    for i in range(3):
        Deck.objects.create(
            id=f"deck-burn-{i}",
            tournament=t,
            format="legacy",
            player=f"BurnPlayer{i}",
            player_lower=f"burnplayer{i}",
            archetype="Unknown",
            archetype_slug="unknown",
            is_auto_classified=True,
            mainboard=[
                {"card": "Lightning Bolt", "count": 4},
                {"card": "Chain Lightning", "count": 4},
                {"card": "Lava Spike", "count": 4},
            ],
            sideboard=[],
        )

    # 2 Blue decks (smaller cluster)
    for i in range(2):
        Deck.objects.create(
            id=f"deck-blue-{i}",
            tournament=t,
            format="legacy",
            player=f"BluePlayer{i}",
            player_lower=f"blueplayer{i}",
            archetype="Unknown",
            archetype_slug="unknown",
            is_auto_classified=True,
            mainboard=[
                {"card": "Brainstorm", "count": 4},
                {"card": "Force of Will", "count": 4},
                {"card": "Ponder", "count": 4},
            ],
            sideboard=[],
        )

    out = StringIO()
    call_command("discover_archetypes", format="legacy", min_cluster=2, eps=0.2, stdout=out)
    output = out.getvalue()

    pos_3 = output.find("3 Decks")
    pos_2 = output.find("2 Decks")
    assert pos_3 != -1, "Expected cluster with 3 decks in output"
    assert pos_2 != -1, "Expected cluster with 2 decks in output"
    assert pos_3 < pos_2, "Expected larger cluster (3 Decks) to appear before smaller cluster (2 Decks)"

    # With min_cluster_size=3, only the 3-deck cluster should be discovered
    out2 = StringIO()
    call_command("discover_archetypes", format="legacy", min_cluster_size=3, eps=0.2, stdout=out2)
    output2 = out2.getvalue()
    assert "3 Decks" in output2
    assert "2 Decks" not in output2



