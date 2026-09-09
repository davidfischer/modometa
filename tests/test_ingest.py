"""Unit and integration tests for ingest_tournaments management command."""

import json
from datetime import date
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import call_command

from core.models.deck import Deck
from core.models.tournament import Tournament


@pytest.fixture
def mock_tournaments_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with mock MTGO tournament JSON files."""
    tourn_dir = tmp_path / "Tournaments" / "MTGO"
    tourn_dir.mkdir(parents=True)

    today = date.today()
    date_recent = today.strftime("%Y-%m-%d")
    date_older = (today - timedelta(days=20)).strftime("%Y-%m-%d")

    file_older = tourn_dir / f"legacy-league-{date_older}.json"
    file_recent = tourn_dir / f"legacy-league-{date_recent}.json"

    data_older = {
        "Tournament": {
            "Name": f"Legacy League {date_older}",
            "Date": date_older,
            "Uri": f"https://www.mtgo.com/decklist/legacy-league-{date_older}",
        },
        "Decks": [
            {
                "Result": "5-0",
                "AnchorUri": f"https://www.mtgo.com/decklist/legacy-league-{date_older}#deck_1",
                "Player": "OldPlayer",
                "Mainboard": [{"CardName": "Force of Will", "Count": 4}],
                "Sideboard": [],
            }
        ],
    }

    data_recent = {
        "Tournament": {
            "Name": f"Legacy League {date_recent}",
            "Date": date_recent,
            "Uri": f"https://www.mtgo.com/decklist/legacy-league-{date_recent}",
        },
        "Decks": [
            {
                "Result": "5-0",
                "AnchorUri": f"https://www.mtgo.com/decklist/legacy-league-{date_recent}#deck_1",
                "Player": "RecentPlayer1",
                "Mainboard": [{"CardName": "Lightning Bolt", "Count": 4}],
                "Sideboard": [],
            }
        ],
    }

    file_older.write_text(json.dumps(data_older))
    file_recent.write_text(json.dumps(data_recent))

    return tourn_dir


@pytest.mark.django_db
def test_refresh_days_reingests_only_recent_tournaments(mock_tournaments_dir: Path):
    """Test that --refresh-days only replaces tournaments within the cutoff window."""
    today = date.today()
    date_recent = today.strftime("%Y-%m-%d")
    date_older = (today - timedelta(days=20)).strftime("%Y-%m-%d")

    # 1. Initial ingestion of both tournaments
    call_command(
        "ingest_tournaments",
        dir=str(mock_tournaments_dir),
        skip_knn=True,
    )

    tourn_older = Tournament.objects.get(id=f"legacy-league-{date_older}")
    tourn_recent = Tournament.objects.get(id=f"legacy-league-{date_recent}")
    assert tourn_older.decks.count() == 1
    assert tourn_recent.decks.count() == 1

    # 2. Update both files to add a second deck to each
    file_older = mock_tournaments_dir / f"legacy-league-{date_older}.json"
    file_recent = mock_tournaments_dir / f"legacy-league-{date_recent}.json"

    data_older = json.loads(file_older.read_text())
    data_older["Decks"].append(
        {
            "Result": "5-0",
            "AnchorUri": f"https://www.mtgo.com/decklist/legacy-league-{date_older}#deck_2",
            "Player": "OldPlayer2",
            "Mainboard": [{"CardName": "Force of Will", "Count": 4}],
            "Sideboard": [],
        }
    )
    file_older.write_text(json.dumps(data_older))

    data_recent = json.loads(file_recent.read_text())
    data_recent["Decks"].append(
        {
            "Result": "5-0",
            "AnchorUri": f"https://www.mtgo.com/decklist/legacy-league-{date_recent}#deck_2",
            "Player": "RecentPlayer2",
            "Mainboard": [{"CardName": "Lightning Bolt", "Count": 4}],
            "Sideboard": [],
        }
    )
    file_recent.write_text(json.dumps(data_recent))

    # 3. Run ingest with --refresh-days 7
    # This should refresh the recent tournament (dated today), but skip the 20-day-old tournament
    call_command(
        "ingest_tournaments",
        dir=str(mock_tournaments_dir),
        refresh_days=7,
        skip_knn=True,
    )

    # 4. Verify results
    tourn_older.refresh_from_db()
    tourn_recent.refresh_from_db()

    # The older tournament was skipped because it's older than 7 days, so it still has 1 deck
    assert tourn_older.decks.count() == 1
    assert not Deck.objects.filter(player="OldPlayer2").exists()

    # The recent tournament was refreshed and now has both decks!
    assert tourn_recent.decks.count() == 2
    assert Deck.objects.filter(player="RecentPlayer2").exists()


@pytest.mark.django_db
def test_refresh_days_calculates_from_database_latest_date(tmp_path: Path):
    """When cache has not updated for days, refresh window is anchored to DB's latest date."""
    tourn_dir = tmp_path / "Tournaments" / "MTGO"
    tourn_dir.mkdir(parents=True)

    # Database latest date is 2026-09-03
    db_latest = date(2026, 9, 3)
    db_older = date(2026, 8, 15)  # 19 days prior

    t_older = Tournament.objects.create(
        id="legacy-league-2026-08-15",
        name="Legacy League 2026-08-15",
        format="legacy",
        event_type="league",
        date=db_older,
    )
    Deck.objects.create(
        id="deck_older_1",
        tournament=t_older,
        format="legacy",
        player="P1",
        player_lower="p1",
        result="5-0",
        archetype="Burn",
        archetype_slug="burn",
        colors="R",
        color_name="Mono-Red",
    )

    t_recent = Tournament.objects.create(
        id="legacy-league-2026-09-03",
        name="Legacy League 2026-09-03",
        format="legacy",
        event_type="league",
        date=db_latest,
    )
    Deck.objects.create(
        id="deck_recent_1",
        tournament=t_recent,
        format="legacy",
        player="P2",
        player_lower="p2",
        result="5-0",
        archetype="Burn",
        archetype_slug="burn",
        colors="R",
        color_name="Mono-Red",
    )

    # Disk cache has matching dates (no newer data, decklistcache hasn't updated in days)
    # But the 2026-09-03 file on disk now has 2 decks (was partial, now completed)
    file_recent = tourn_dir / "legacy-league-2026-09-03.json"
    file_older = tourn_dir / "legacy-league-2026-08-15.json"

    file_recent.write_text(
        json.dumps(
            {
                "Tournament": {
                    "Name": "Legacy League 2026-09-03",
                    "Date": "2026-09-03",
                },
                "Decks": [
                    {
                        "Result": "5-0",
                        "AnchorUri": "https://www.mtgo.com/1",
                        "Player": "P2",
                        "Mainboard": [{"CardName": "Lightning Bolt", "Count": 4}],
                        "Sideboard": [],
                    },
                    {
                        "Result": "5-0",
                        "AnchorUri": "https://www.mtgo.com/2",
                        "Player": "P3_New",
                        "Mainboard": [{"CardName": "Chain Lightning", "Count": 4}],
                        "Sideboard": [],
                    },
                ],
            }
        )
    )

    file_older.write_text(
        json.dumps(
            {
                "Tournament": {
                    "Name": "Legacy League 2026-08-15",
                    "Date": "2026-08-15",
                },
                "Decks": [
                    {
                        "Result": "5-0",
                        "AnchorUri": "https://www.mtgo.com/old_new",
                        "Player": "P_Should_Be_Skipped",
                        "Mainboard": [{"CardName": "Mountain", "Count": 4}],
                        "Sideboard": [],
                    }
                ],
            }
        )
    )

    # Run ingest with --refresh-days 8
    # Cutoff should be 2026-09-03 - 8 days = 2026-08-26
    call_command(
        "ingest_tournaments",
        dir=str(tourn_dir),
        refresh_days=8,
        skip_knn=True,
    )

    # 2026-09-03 is >= 2026-08-26, so it was replaced and has both decks
    t_recent.refresh_from_db()
    assert t_recent.decks.count() == 2
    assert Deck.objects.filter(player="P3_New").exists()

    # 2026-08-15 is < 2026-08-26, so it was skipped and still has its original single deck
    t_older.refresh_from_db()
    assert t_older.decks.count() == 1
    assert not Deck.objects.filter(player="P_Should_Be_Skipped").exists()
