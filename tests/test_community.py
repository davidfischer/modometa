"""Unit and integration tests for LDCP community data ingestion, views, and admin."""

import io
import json
from datetime import date
from pathlib import Path

import pytest
from django.contrib.admin.sites import site
from django.core.management import call_command
from django.core.management.base import CommandError

from core.admin import MatchAdmin
from core.admin import TournamentAdmin
from core.models.deck import Deck
from core.models.match import Match
from core.models.tournament import Tournament
from core.pipeline.community import CommunityIngestionPipeline


@pytest.fixture
def mock_legacy_challenge():
    """Create a mock Legacy challenge in the database with Top 32 decklists and official Top 8 matches."""
    today = date.today()
    date_str = today.strftime("%Y-%m-%d")
    t_id = f"legacy-challenge-32-{date_str}9999"

    tournament = Tournament.objects.create(
        id=t_id,
        name=f"Legacy Challenge 32 {date_str}",
        format="legacy",
        event_type="challenge",
        date=today,
        player_count=64,
    )

    # Create decks for top 4 players (representing mtgo published decks)
    # Player1 (1st), Player2 (2nd), Player3 (3rd), Player4 (4th)
    d1 = Deck.objects.create(
        id=f"{t_id}_deck_1",
        tournament=tournament,
        format="legacy",
        player="Alice",
        player_lower="alice",
        rank=1,
        result="1st Place",
        is_top8=True,
        archetype="Delver",
        archetype_slug="delver",
    )
    d2 = Deck.objects.create(
        id=f"{t_id}_deck_2",
        tournament=tournament,
        format="legacy",
        player="Bob",
        player_lower="bob",
        rank=2,
        result="2nd Place",
        is_top8=True,
        archetype="Death & Taxes",
        archetype_slug="death-taxes",
    )
    d3 = Deck.objects.create(
        id=f"{t_id}_deck_3",
        tournament=tournament,
        format="legacy",
        player="Charlie",
        player_lower="charlie",
        rank=3,
        result="3rd Place",
        is_top8=True,
        archetype="Doomsday",
        archetype_slug="doomsday",
    )
    d4 = Deck.objects.create(
        id=f"{t_id}_deck_4",
        tournament=tournament,
        format="legacy",
        player="Diana",
        player_lower="diana",
        rank=4,
        result="4th Place",
        is_top8=True,
        archetype="Reanimator",
        archetype_slug="reanimator",
    )

    # Official MTGO Top 8 matches (Finals and Semifinals)
    Match.objects.create(
        id=f"{t_id}_finals_0",
        tournament=tournament,
        round_name="Finals",
        round_slug="finals",
        player1="Alice",
        player2="Bob",
        player1_deck=d1,
        player2_deck=d2,
        player1_wins=2,
        player2_wins=1,
        draws=0,
        source=Match.SOURCE_MTGO,
    )
    Match.objects.create(
        id=f"{t_id}_semifinals_0",
        tournament=tournament,
        round_name="Semifinals",
        round_slug="semifinals",
        player1="Alice",
        player2="Charlie",
        player1_deck=d1,
        player2_deck=d3,
        player1_wins=2,
        player2_wins=0,
        draws=0,
        source=Match.SOURCE_MTGO,
    )
    Match.objects.create(
        id=f"{t_id}_semifinals_1",
        tournament=tournament,
        round_name="Semifinals",
        round_slug="semifinals",
        player1="Bob",
        player2="Diana",
        player1_deck=d2,
        player2_deck=d4,
        player1_wins=2,
        player2_wins=0,
        draws=0,
        source=Match.SOURCE_MTGO,
    )

    return tournament


@pytest.mark.django_db
def test_match_source_default_and_choices():
    """Verify Match model source default value and choices."""
    t = Tournament.objects.create(
        id="t1",
        name="Test",
        format="legacy",
        event_type="challenge",
        date=date.today(),
    )
    m1 = Match.objects.create(
        id="m1",
        tournament=t,
        round_name="Finals",
        round_slug="finals",
        player1="P1",
        player2="P2",
    )
    assert m1.source == Match.SOURCE_MTGO
    assert m1.source == "mtgo"

    m2 = Match.objects.create(
        id="m2",
        tournament=t,
        round_name="Round 1",
        round_slug="round_01",
        player1="P1",
        player2="P2",
        source=Match.SOURCE_LDCP,
    )
    assert m2.source == Match.SOURCE_LDCP
    assert m2.source == "ldcp"

    m3 = Match.objects.create(
        id="m3",
        tournament=t,
        round_name="Round 1",
        round_slug="round_01",
        player1="P1",
        player2="P2",
        source=Match.SOURCE_VMC,
    )
    assert m3.source == Match.SOURCE_VMC
    assert m3.source == "vmc"
    assert Match.SOURCE_VMC in Match.COMMUNITY_SOURCES
    assert Match.SOURCE_LDCP in Match.COMMUNITY_SOURCES


@pytest.mark.django_db
def test_ingest_community_swiss_and_skips_no_deck(
    tmp_path: Path, mock_legacy_challenge: Tournament
):
    """Test that Swiss matches between players with decklists are ingested, while players without are skipped."""
    t_id = mock_legacy_challenge.id
    community_dir = tmp_path / "community_data"
    community_dir.mkdir()

    json_file = community_dir / f"{t_id}.json"
    data = {
        "Tournament": {
            "Id": t_id,
            "Date": date.today().strftime("%Y-%m-%d"),
            "Name": mock_legacy_challenge.name,
        },
        "Rounds": [
            {
                "RoundName": "Round 1",
                "RoundType": "Swiss",
                "Matches": [
                    # Both Alice and Bob have decks -> Should ingest
                    {
                        "Player1": "Alice",
                        "Player2": "Bob",
                        "Result": "2-1-0",
                    },
                    # Charlie has deck, but UnknownPlayer does not -> Should skip
                    {
                        "Player1": "Charlie",
                        "Player2": "UnknownPlayer",
                        "Result": "2-0-0",
                    },
                ],
            },
            {
                "RoundName": "Round 2",
                "RoundType": "Swiss",
                "Matches": [
                    # Both Charlie and Diana have decks -> Should ingest
                    {
                        "Player1": "Charlie",
                        "Player2": "Diana",
                        "Result": "1-2-0",
                    },
                ],
            },
            {
                "RoundName": "Round 3",
                "RoundType": "Top8",
                "Matches": [
                    # Top 8 match matches official finals -> Should NOT double-ingest
                    {
                        "Player1": "Alice",
                        "Player2": "Bob",
                        "Result": "2-1-0",
                    }
                ],
            },
        ],
    }
    json_file.write_text(json.dumps(data))

    pipeline = CommunityIngestionPipeline()
    saved, skipped, warnings, disagreements = pipeline.ingest_file(json_file)

    assert saved == 2
    assert skipped == 1
    assert len(warnings) == 0
    assert len(disagreements) == 0

    # Verify official matches still intact and not duplicated
    official_matches = Match.objects.filter(
        tournament=mock_legacy_challenge, source=Match.SOURCE_MTGO
    )
    assert official_matches.count() == 3

    # Verify ingested LDCP matches
    ldcp_matches = Match.objects.filter(
        tournament=mock_legacy_challenge, source=Match.SOURCE_LDCP
    )
    assert ldcp_matches.count() == 2

    m1 = ldcp_matches.get(round_slug="round_01")
    assert m1.player1 == "Alice"
    assert m1.player2 == "Bob"
    assert m1.player1_deck.player == "Alice"
    assert m1.player2_deck.player == "Bob"
    assert m1.player1_wins == 2
    assert m1.player2_wins == 1
    assert m1.source == "ldcp"

    m2 = ldcp_matches.get(round_slug="round_02")
    assert m2.player1 == "Charlie"
    assert m2.player2 == "Diana"
    assert m2.player1_wins == 1
    assert m2.player2_wins == 2


@pytest.mark.django_db
def test_ingest_community_disagreement_warning(
    tmp_path: Path, mock_legacy_challenge: Tournament
):
    """Test that disagreements in Top 8 pairings or scores produce warnings and strict mode raises CommandError."""
    t_id = mock_legacy_challenge.id
    community_dir = tmp_path / "community_data"
    community_dir.mkdir()

    json_file = community_dir / f"{t_id}.json"
    data = {
        "Tournament": {
            "Id": t_id,
            "Date": date.today().strftime("%Y-%m-%d"),
            "Name": mock_legacy_challenge.name,
        },
        "Rounds": [
            {
                "RoundName": "Round 1",
                "RoundType": "Swiss",
                "Matches": [
                    {
                        "Player1": "Alice",
                        "Player2": "Bob",
                        "Result": "2-0-0",
                    }
                ],
            },
            {
                "RoundName": "Finals",
                "RoundType": "Top8",
                "Matches": [
                    # Disagreement in score: official was 2-1-0, LDCP reports 2-0-0
                    {
                        "Player1": "Alice",
                        "Player2": "Bob",
                        "Result": "2-0-0",
                    },
                    # Disagreement in pairing: GhostPlayer vs Diana not in official matches
                    {
                        "Player1": "GhostPlayer",
                        "Player2": "Diana",
                        "Result": "2-1-0",
                    },
                ],
            },
        ],
    }
    json_file.write_text(json.dumps(data))

    out = io.StringIO()
    # Running without strict emits warnings but succeeds
    call_command("ingest_community", dir=str(community_dir), stdout=out)
    output = out.getvalue()
    assert "Disagreements found in Top 8 matches" in output
    assert "Top 8 score disagreement" in output
    assert "Top 8 pairing disagreement" in output

    # Ingested Swiss match was still saved
    assert (
        Match.objects.filter(
            tournament=mock_legacy_challenge, source=Match.SOURCE_LDCP
        ).count()
        == 1
    )

    # Running with --strict raises CommandError
    with pytest.raises(CommandError, match="Aborted due to"):
        call_command(
            "ingest_community", dir=str(community_dir), force=True, strict=True
        )


@pytest.mark.django_db
def test_ingest_community_missing_official_data_warns_and_skips(tmp_path: Path):
    """Test that a tournament without official data in the database raises a warning and does not ingest anything."""
    community_dir = tmp_path / "community_data"
    community_dir.mkdir()

    missing_id = "legacy-challenge-32-2026-09-10999999"
    json_file = community_dir / f"{missing_id}.json"
    data = {
        "Tournament": {
            "Id": missing_id,
            "Date": "2026-09-10",
            "Name": "Legacy Challenge 32",
        },
        "Rounds": [
            {
                "RoundName": "Round 1",
                "RoundType": "Swiss",
                "Matches": [
                    {"Player1": "Alice", "Player2": "Bob", "Result": "2-0-0"},
                ],
            }
        ],
    }
    json_file.write_text(json.dumps(data))

    pipeline = CommunityIngestionPipeline()
    saved, skipped, warnings, disagreements = pipeline.ingest_file(json_file)
    assert saved == 0
    assert skipped == 0
    assert len(warnings) == 1
    assert "has no official tournament data in database" in warnings[0]
    assert Match.objects.filter(tournament_id=missing_id).count() == 0

    out = io.StringIO()
    call_command("ingest_community", dir=str(community_dir), stdout=out)
    output = out.getvalue()
    assert "Warnings (1):" in output
    assert "has no official tournament data in database" in output
    assert "0 community match(es) saved" in output


@pytest.mark.django_db
def test_ingest_community_idempotency_and_force(
    tmp_path: Path, mock_legacy_challenge: Tournament
):
    """Test that running ingest_community twice skips unless --force is given."""
    t_id = mock_legacy_challenge.id
    community_dir = tmp_path / "community_data"
    community_dir.mkdir()

    json_file = community_dir / f"{t_id}.json"
    data = {
        "Tournament": {
            "Id": t_id,
            "Date": date.today().strftime("%Y-%m-%d"),
            "Name": mock_legacy_challenge.name,
        },
        "Rounds": [
            {
                "RoundName": "Round 1",
                "RoundType": "Swiss",
                "Matches": [
                    {"Player1": "Alice", "Player2": "Bob", "Result": "2-0-0"},
                ],
            }
        ],
    }
    json_file.write_text(json.dumps(data))

    # First run: 1 match saved
    call_command("ingest_community", dir=str(community_dir))
    assert (
        Match.objects.filter(
            tournament=mock_legacy_challenge, source=Match.SOURCE_LDCP
        ).count()
        == 1
    )

    # Second run without force: skips existing
    out = io.StringIO()
    call_command("ingest_community", dir=str(community_dir), stdout=out)
    assert "0 community match(es) saved" in out.getvalue()
    assert (
        Match.objects.filter(
            tournament=mock_legacy_challenge, source=Match.SOURCE_LDCP
        ).count()
        == 1
    )

    # Third run with force: replaces matches
    call_command("ingest_community", dir=str(community_dir), force=True, stdout=out)
    assert "1 community match(es) saved" in out.getvalue()
    assert (
        Match.objects.filter(
            tournament=mock_legacy_challenge, source=Match.SOURCE_LDCP
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_archetype_matrix_ldcp_credit(client, mock_legacy_challenge: Tournament):
    """Test that the archetype matrix view credits LDCP when LDCP matches exist in the window."""
    # 1. Before LDCP matches, request legacy matrix -> no LDCP banner
    res = client.get("/legacy/matrix/?days=30")
    assert res.status_code == 200
    assert res.context["has_ldcp_data"] is False
    assert "Legacy Data Collection Project" not in res.content.decode("utf-8")

    # 2. Add LDCP Swiss match in window
    Match.objects.create(
        id=f"{mock_legacy_challenge.id}_round_01_0",
        tournament=mock_legacy_challenge,
        round_name="Round 1",
        round_slug="round_01",
        player1="Alice",
        player2="Bob",
        player1_deck=mock_legacy_challenge.decks.get(player="Alice"),
        player2_deck=mock_legacy_challenge.decks.get(player="Bob"),
        player1_wins=2,
        player2_wins=1,
        draws=0,
        source=Match.SOURCE_LDCP,
    )

    # 3. Request legacy matrix again -> has LDCP banner & Patreon link
    res_ldcp = client.get("/legacy/matrix/?days=30")
    assert res_ldcp.status_code == 200
    assert res_ldcp.context["has_ldcp_data"] is True
    content = res_ldcp.content.decode("utf-8")
    assert "Legacy Data Collection Project" in content
    assert "patreon.com/legacydatacollection" in content


@pytest.mark.django_db
def test_admin_match_and_tournament_registrations(mock_legacy_challenge: Tournament):
    """Test that MatchAdmin is registered with select_related to prevent N+1 queries."""
    assert Match in site._registry
    match_admin = site._registry[Match]
    assert isinstance(match_admin, MatchAdmin)
    assert "source" in match_admin.list_display
    assert "tournament_date" in match_admin.list_display
    assert match_admin.list_select_related == ("tournament",)

    t_admin = site._registry[Tournament]
    assert isinstance(t_admin, TournamentAdmin)
    assert "name" in t_admin.list_display


def test_faq_references_ldcp_augmentation(client):
    """Test that the FAQ / About page mentions augmenting official data with LDCP and VMC."""
    res = client.get("/faq/")
    assert res.status_code == 200
    content = res.content.decode("utf-8")
    assert "Legacy Data Collection Project" in content
    assert "patreon.com/legacydatacollection" in content
    assert "Vintage MTGO Community" in content
    assert "discord.gg/2eVcsjK" in content
    assert "augment" in content


@pytest.fixture
def mock_vintage_challenge():
    """Create a mock Vintage challenge in the database with Top 32 decklists and official Top 8 matches."""
    today = date.today()
    date_str = today.strftime("%Y-%m-%d")
    t_id = f"vintage-challenge-32-{date_str}8888"

    tournament = Tournament.objects.create(
        id=t_id,
        name=f"Vintage Challenge 32 {date_str}",
        format="vintage",
        event_type="challenge",
        date=today,
        player_count=48,
    )

    d1 = Deck.objects.create(
        id=f"{t_id}_deck_1",
        tournament=tournament,
        format="vintage",
        player="Alice",
        player_lower="alice",
        rank=1,
        result="1st Place",
        is_top8=True,
        archetype="Jewel Shops",
        archetype_slug="jewel-shops",
    )
    d2 = Deck.objects.create(
        id=f"{t_id}_deck_2",
        tournament=tournament,
        format="vintage",
        player="Bob",
        player_lower="bob",
        rank=2,
        result="2nd Place",
        is_top8=True,
        archetype="Lurrus Saga",
        archetype_slug="lurrus-saga",
    )
    d3 = Deck.objects.create(
        id=f"{t_id}_deck_3",
        tournament=tournament,
        format="vintage",
        player="Charlie",
        player_lower="charlie",
        rank=3,
        result="3rd Place",
        is_top8=True,
        archetype="Doomsday",
        archetype_slug="doomsday",
    )
    d4 = Deck.objects.create(
        id=f"{t_id}_deck_4",
        tournament=tournament,
        format="vintage",
        player="Diana",
        player_lower="diana",
        rank=4,
        result="4th Place",
        is_top8=True,
        archetype="Oath of Druids",
        archetype_slug="oath-of-druids",
    )

    Match.objects.create(
        id=f"{t_id}_finals_0",
        tournament=tournament,
        round_name="Finals",
        round_slug="finals",
        player1="Alice",
        player2="Bob",
        player1_deck=d1,
        player2_deck=d2,
        player1_wins=2,
        player2_wins=1,
        draws=0,
        source=Match.SOURCE_MTGO,
    )
    Match.objects.create(
        id=f"{t_id}_semifinals_0",
        tournament=tournament,
        round_name="Semifinals",
        round_slug="semifinals",
        player1="Alice",
        player2="Charlie",
        player1_deck=d1,
        player2_deck=d3,
        player1_wins=2,
        player2_wins=0,
        draws=0,
        source=Match.SOURCE_MTGO,
    )
    Match.objects.create(
        id=f"{t_id}_semifinals_1",
        tournament=tournament,
        round_name="Semifinals",
        round_slug="semifinals",
        player1="Bob",
        player2="Diana",
        player1_deck=d2,
        player2_deck=d4,
        player1_wins=2,
        player2_wins=0,
        draws=0,
        source=Match.SOURCE_MTGO,
    )

    return tournament


@pytest.mark.django_db
def test_ingest_community_vintage_matches(
    tmp_path: Path, mock_vintage_challenge: Tournament
):
    """Test ingesting Vintage community data sets source=vmc and ignores Top 8."""
    t_id = mock_vintage_challenge.id
    community_dir = tmp_path / "datasources" / "vintage-mtgo-community" / "2026" / "09"
    community_dir.mkdir(parents=True)

    json_file = community_dir / f"{t_id}.json"
    data = {
        "Tournament": {
            "Id": t_id,
            "Date": date.today().strftime("%Y-%m-%d"),
            "Name": mock_vintage_challenge.name,
            "Source": "vintage-mtgo-community",
        },
        "Rounds": [
            {
                "RoundName": "Round 1",
                "RoundType": "Swiss",
                "Matches": [
                    {"Player1": "Alice", "Player2": "Bob", "Result": "2-1-0"},
                    {"Player1": "Charlie", "Player2": "Unknown", "Result": "2-0-0"},
                ],
            },
            {
                "RoundName": "Finals",
                "RoundType": "Top8",
                "Matches": [
                    {"Player1": "Alice", "Player2": "Bob", "Result": "2-1-0"},
                ],
            },
        ],
    }
    json_file.write_text(json.dumps(data))

    pipeline = CommunityIngestionPipeline()
    files = pipeline.find_files(tmp_path)
    assert len(files) == 1
    assert files[0] == json_file

    result = pipeline.ingest_files(files)
    assert result.matches_saved == 1
    assert result.matches_skipped_no_deck == 1
    assert len(result.disagreements) == 0

    m = Match.objects.get(id=f"{t_id}_round_01_0")
    assert m.source == Match.SOURCE_VMC
    assert m.source == "vmc"
    assert m.player1 == "Alice"
    assert m.player2 == "Bob"
    assert m.player1_wins == 2
    assert m.player2_wins == 1


@pytest.mark.django_db
def test_ingest_community_format_filter(
    tmp_path: Path,
    mock_legacy_challenge: Tournament,
    mock_vintage_challenge: Tournament,
):
    """Test that --format filters ingestion to only matching format files."""
    ds_dir = tmp_path / "datasources"
    legacy_dir = ds_dir / "legacy-data-collection" / "2026" / "09"
    legacy_dir.mkdir(parents=True)
    vintage_dir = ds_dir / "vintage-mtgo-community" / "2026" / "09"
    vintage_dir.mkdir(parents=True)

    leg_file = legacy_dir / f"{mock_legacy_challenge.id}.json"
    leg_file.write_text(
        json.dumps(
            {
                "Tournament": {
                    "Id": mock_legacy_challenge.id,
                    "Date": date.today().strftime("%Y-%m-%d"),
                    "Name": mock_legacy_challenge.name,
                    "Source": "legacy-data-collection",
                },
                "Rounds": [
                    {
                        "RoundName": "Round 1",
                        "RoundType": "Swiss",
                        "Matches": [
                            {"Player1": "Alice", "Player2": "Bob", "Result": "2-0-0"}
                        ],
                    }
                ],
            }
        )
    )

    vin_file = vintage_dir / f"{mock_vintage_challenge.id}.json"
    vin_file.write_text(
        json.dumps(
            {
                "Tournament": {
                    "Id": mock_vintage_challenge.id,
                    "Date": date.today().strftime("%Y-%m-%d"),
                    "Name": mock_vintage_challenge.name,
                    "Source": "vintage-mtgo-community",
                },
                "Rounds": [
                    {
                        "RoundName": "Round 1",
                        "RoundType": "Swiss",
                        "Matches": [
                            {"Player1": "Alice", "Player2": "Bob", "Result": "2-1-0"}
                        ],
                    }
                ],
            }
        )
    )

    pipeline = CommunityIngestionPipeline()
    # Test find_files with format filter
    legacy_files = pipeline.find_files(ds_dir, format_filter="legacy")
    assert len(legacy_files) == 1
    assert legacy_files[0] == leg_file

    vintage_files = pipeline.find_files(ds_dir, format_filter="vintage")
    assert len(vintage_files) == 1
    assert vintage_files[0] == vin_file

    all_files = pipeline.find_files(ds_dir)
    assert len(all_files) == 2

    # Ingest only vintage via management command
    call_command("ingest_community", dir=str(ds_dir), format="vintage")
    assert (
        Match.objects.filter(
            tournament=mock_vintage_challenge, source=Match.SOURCE_VMC
        ).count()
        == 1
    )
    assert (
        Match.objects.filter(
            tournament=mock_legacy_challenge, source=Match.SOURCE_LDCP
        ).count()
        == 0
    )

    # Now ingest legacy
    call_command("ingest_community", dir=str(ds_dir), format="legacy")
    assert (
        Match.objects.filter(
            tournament=mock_legacy_challenge, source=Match.SOURCE_LDCP
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_archetype_matrix_vintage_vmc_credit(
    client, mock_vintage_challenge: Tournament
):
    """Test that the archetype matrix view credits VMC when Vintage community matches exist."""
    res = client.get("/vintage/matrix/?days=30")
    assert res.status_code == 200
    assert res.context["has_vmc_data"] is False
    assert "Vintage MTGO Community" not in res.content.decode("utf-8")

    Match.objects.create(
        id=f"{mock_vintage_challenge.id}_round_01_0",
        tournament=mock_vintage_challenge,
        round_name="Round 1",
        round_slug="round_01",
        player1="Alice",
        player2="Bob",
        player1_deck=mock_vintage_challenge.decks.get(player="Alice"),
        player2_deck=mock_vintage_challenge.decks.get(player="Bob"),
        player1_wins=2,
        player2_wins=1,
        draws=0,
        source=Match.SOURCE_VMC,
    )

    res_vmc = client.get("/vintage/matrix/?days=30")
    assert res_vmc.status_code == 200
    assert res_vmc.context["has_vmc_data"] is True
    assert res_vmc.context["has_community_data"] is True
    content = res_vmc.content.decode("utf-8")
    assert "Vintage MTGO Community" in content
    assert "https://discord.gg/2eVcsjK" in content
    assert "Legacy Data Collection Project" not in content


@pytest.mark.django_db
def test_community_ingestion_populates_sheet_id(
    tmp_path: Path, mock_legacy_challenge: Tournament
):
    """Test that community ingestion populates tournament community_sheet_id and properties."""
    ldcp_dir = tmp_path / "datasources" / "legacy-data-collection"
    ldcp_dir.mkdir(parents=True)

    json_file = ldcp_dir / f"{mock_legacy_challenge.id}.json"
    data = {
        "Tournament": {
            "Id": mock_legacy_challenge.id,
            "Date": mock_legacy_challenge.date.strftime("%Y-%m-%d"),
            "Name": mock_legacy_challenge.name,
            "Source": "legacy-data-collection",
            "SheetId": "1FNRLozqu7UVO4PYxqsg-Jl128lQ-pyodtlBHWwHVx-0",
        },
        "Players": [],
        "Rounds": [],
    }
    json_file.write_text(json.dumps(data))

    pipeline = CommunityIngestionPipeline()
    pipeline.ingest_file(json_file)

    mock_legacy_challenge.refresh_from_db()
    assert (
        mock_legacy_challenge.community_sheet_id
        == "1FNRLozqu7UVO4PYxqsg-Jl128lQ-pyodtlBHWwHVx-0"
    )
    assert (
        mock_legacy_challenge.community_sheet_url
        == "https://docs.google.com/spreadsheets/d/1FNRLozqu7UVO4PYxqsg-Jl128lQ-pyodtlBHWwHVx-0/edit"
    )
    assert (
        mock_legacy_challenge.community_source_name == "Legacy Data Collection Project"
    )
