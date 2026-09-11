"""Integration tests for Modometa routes and views."""

import json
import re
import warnings
from datetime import date
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.http import HttpResponse
from django.test import Client
from django.test import RequestFactory
from django.test import override_settings

from core.engine.knn import set_global_knn_index
from core.models import Card
from core.models import CardLookup
from core.models import Deck
from core.models import Tournament
from core.pipeline.scryfall import _extract_mana_cost
from core.views import classify_card_type
from core.views import get_dataset_min_date
from core.views import get_dataset_start_year
from core.views import get_reference_date
from core.views import public_cache


@pytest.fixture
def client():
    return Client()


@pytest.mark.django_db
def test_home_view(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"MTGO Metagame Analyzer" in response.content
    assert len(response.context["format_metas"]) > 0
    assert "total_top8_slots" in response.context["format_metas"][0]
    assert b"Fan Content Policy" in response.content


@pytest.mark.django_db
def test_home_view_30d_toggle(client):
    response = client.get("/?days=30")
    assert response.status_code == 200


@pytest.mark.django_db
def test_format_overview_view(client):
    response = client.get("/legacy/")
    assert response.status_code == 200
    assert b"Legacy Metagame" in response.content
    assert "total_chall_decks" in response.context["stats"]
    assert "total_top8_slots" in response.context["stats"]
    assert "total_5_0s" in response.context["stats"]

    # Verify column header order: League Share before Challenge Share before Top 8 Share before T8 Momentum before Conversion
    content = response.content
    pos_ls = content.find(b">League Share</th>")
    pos_cs = content.find(b">Challenge Share</th>")
    pos_t8s = content.find(b">Top 8 Share</th>")
    pos_t8m = content.find(b">T8 Momentum</th>")
    pos_conv = content.find(b">Conversion</th>")
    assert (
        pos_ls != -1
        and pos_cs != -1
        and pos_t8s != -1
        and pos_t8m != -1
        and pos_conv != -1
    )
    assert pos_ls < pos_cs < pos_t8s < pos_t8m < pos_conv
    assert b">Colors</th>" not in content

    # Verify each archetype slug appears at most once in format overview
    archetypes = response.context["archetypes"]
    slugs = [a["slug"] for a in archetypes]
    assert len(slugs) == len(set(slugs)), "Each archetype must appear at most once"

    # Verify that metrics for an archetype match on archetype_detail
    if archetypes:
        first_arch = archetypes[0]
        arch_resp = client.get(f"/legacy/archetype/{first_arch['slug']}/")
        assert arch_resp.status_code == 200
        arch_stats = arch_resp.context["stats"]
        assert first_arch["top8_count"] == arch_stats["top8_count"]
        assert first_arch["chall_appearances"] == arch_stats["challenge_appearances"]
        assert first_arch["league_5_0_count"] == arch_stats["league_5_0_count"]
        assert first_arch["top8_share"] == arch_stats["top8_share"]
        assert first_arch["challenge_share"] == arch_stats["challenge_share"]
        assert first_arch["conversion_rate"] == arch_stats["conversion_rate"]
        assert first_arch["league_share"] == arch_stats["league_share"]

    cards_resp = client.get("/legacy/cards/?days=90")
    assert cards_resp.status_code == 200
    if response.context["common_cards"]:
        assert (
            response.context["common_cards"][0]["name"]
            == cards_resp.context["cards"][0]["name"]
        )
        assert (
            response.context["common_cards"][0]["adoption_pct"]
            == cards_resp.context["cards"][0]["adoption_pct"]
        )


@pytest.mark.django_db
def test_tournament_list_view(client):
    response = client.get("/legacy/tournaments/")
    assert response.status_code == 200
    assert b"Legacy Tournaments" in response.content


@pytest.mark.django_db
def test_tournament_detail_view(client):
    t = Tournament.objects.create(
        id="legacy-challenge-32-test",
        name="Legacy Challenge 32",
        format="legacy",
        event_type="challenge",
        date=date(2024, 1, 1),
    )
    Deck.objects.create(
        id="legacy-challenge-32-test_testplayer_1",
        tournament=t,
        format="legacy",
        player="TestPlayer",
        player_lower="testplayer",
        archetype="Dimir Tempo",
        archetype_slug="dimir-tempo",
        colors="UB",
        color_name="Dimir",
        result="1st Place",
        is_top8=True,
    )
    response = client.get(f"/legacy/tournaments/{t.id}/")
    assert response.status_code == 200
    assert b">Colors</th>" not in response.content
    assert b">Place / Result</th>" in response.content
    assert b">Deck</th>" in response.content
    assert b">Player</th>" in response.content
    assert b'title="Dimir"' in response.content


@pytest.mark.django_db
def test_tournament_detail_sort_order(client):
    t = Tournament.objects.create(
        id="legacy-swiss-test",
        name="Legacy LCQ",
        format="legacy",
        event_type="other",
        date=date(2024, 1, 1),
    )
    decks_data = [
        ("p_13", "1-3", None),
        ("p_50", "5-0", None),
        ("p_41", "4-1", None),
        ("p_31", "3-1", None),
        ("p_32", "3-2", None),
        ("p_1st", "1st Place", 1),
    ]
    for player, res, rank in decks_data:
        Deck.objects.create(
            id=f"legacy-swiss-test_{player}",
            tournament=t,
            format="legacy",
            player=player,
            player_lower=player.lower(),
            archetype="Delver",
            archetype_slug="delver",
            result=res,
            rank=rank,
        )
    response = client.get(f"/legacy/tournaments/{t.id}/")
    assert response.status_code == 200
    ordered_players = [d.player for d in response.context["decks"]]
    assert ordered_players == ["p_1st", "p_50", "p_41", "p_31", "p_32", "p_13"]


@pytest.mark.django_db
def test_player_detail_view(client):
    deck = Deck.objects.first()
    if not deck:
        t = Tournament.objects.create(
            id="legacy-challenge-2023-05",
            name="Legacy Challenge 32",
            format="legacy",
            date=date(2023, 5, 10),
            event_type="challenge",
        )
        deck = Deck.objects.create(
            tournament=t,
            player="Ark4n",
            player_lower="ark4n",
            format="legacy",
            archetype="Dimir Ninjas",
            archetype_slug="dimir-ninjas",
            result="1st Place",
            is_top8=True,
            mainboard=[{"card": "Brainstorm", "count": 4}],
            sideboard=[],
        )
    response = client.get(f"/player/{deck.player}/")
    assert response.status_code == 200
    assert deck.player.encode() in response.content
    assert b"League 5-0 Trophies" in response.content
    assert b"all time" in response.content
    assert "start_year" in response.context
    if response.context["start_year"]:
        assert f"(since {response.context['start_year']})".encode() in response.content
    assert "chall_appearances" in response.context
    assert "page_obj" in response.context
    assert response.context["page_obj"].paginator.per_page == 100

    # Verify League 5-0 Trophies stat card comes before Challenge T8 stat card
    l50_pos = response.content.find(b"League 5-0 Trophies")
    ct8_pos = response.content.find(b"Challenge T8")
    assert l50_pos != -1 and ct8_pos != -1
    assert l50_pos < ct8_pos

    # Verify Action column is gone and Date links to deck detail
    assert b">Action</th>" not in response.content
    assert b"View Deck" not in response.content
    deck_link = f"/player/{deck.player}/deck/{deck.tournament_id}/".encode()
    assert deck_link in response.content


@pytest.mark.django_db
def test_get_dataset_start_year():
    Tournament.objects.all().delete()
    assert get_dataset_min_date() is None
    assert get_dataset_start_year() is None

    Tournament.objects.create(
        id="tourn-other-2021",
        name="Other Event",
        format="legacy",
        date=date(2021, 6, 1),
        event_type="other",
    )
    assert get_dataset_min_date() == date(2021, 6, 1)
    assert get_dataset_start_year() == 2021

    Tournament.objects.create(
        id="tourn-league-2023",
        name="Legacy League",
        format="legacy",
        date=date(2023, 1, 15),
        event_type="league",
    )
    Tournament.objects.create(
        id="tourn-challenge-2024",
        name="Legacy Challenge",
        format="legacy",
        date=date(2024, 2, 20),
        event_type="challenge",
    )
    assert get_dataset_min_date() == date(2023, 1, 15)
    assert get_dataset_start_year() == 2023

    # Verify 4-hour caching behavior when not in test mode
    cache.clear()
    with override_settings(IS_TESTING=False):
        assert cache.get("dataset_min_date_v1") is None
        cached_val = get_dataset_min_date()
        assert cached_val == date(2023, 1, 15)
        assert cache.get("dataset_min_date_v1") == date(2023, 1, 15)
    cache.clear()


@pytest.mark.django_db
def test_player_detail_pagination(client):
    tourn = Tournament.objects.first()
    if tourn:
        player_name = "PaginationTestPlayer"
        decks = [
            Deck(
                tournament=tourn,
                player=player_name,
                player_lower=player_name.lower(),
                format="legacy",
                archetype="Delver",
                archetype_slug="delver",
                result="5-0",
                is_5_0=True,
                is_top8=False,
                mainboard=[{"card": "Brainstorm", "count": 4}],
                sideboard=[],
            )
            for _ in range(105)
        ]
        Deck.objects.bulk_create(decks)

        resp_p1 = client.get(f"/player/{player_name}/")
        assert resp_p1.status_code == 200
        assert len(resp_p1.context["decks"]) == 100
        assert resp_p1.context["page_obj"].paginator.num_pages == 2
        assert "1–100 of 105 events".encode() in resp_p1.content
        assert "Next →".encode() in resp_p1.content

        resp_p2 = client.get(f"/player/{player_name}/?page=2")
        assert resp_p2.status_code == 200
        assert len(resp_p2.context["decks"]) == 5
        assert "← Previous".encode() in resp_p2.content


@pytest.mark.django_db
def test_deck_detail_view(client):
    t = Tournament.objects.create(
        id="tourn-deck-detail-test",
        name="Vintage Challenge",
        format="vintage",
        date=date(2024, 6, 1),
    )
    deck = Deck.objects.create(
        id="deck-detail-test-1",
        tournament=t,
        format="vintage",
        player="DetailPlayer",
        player_lower="detailplayer",
        archetype="Oath of Druids",
        archetype_slug="oath-of-druids",
        result="1st",
        mainboard=[{"card": "Oath of Druids", "count": 4}],
        sideboard=[],
    )

    response = client.get(f"/player/{deck.player}/deck/{deck.tournament_id}/")
    assert response.status_code == 200
    assert b"Mainboard" in response.content
    assert b"Similar" in response.content

    # Archetype link in title and action button
    content = response.content.decode()
    arch_url = f"/{deck.format}/archetype/{deck.archetype_slug}/"
    assert arch_url in content
    assert "Archetype Overview" in content


@pytest.mark.django_db
def test_classify_card_type_precedence():
    # 1. Land creature -> land (Dryad Arbor)
    dryad = Card(name="Dryad Arbor", type_line="Land Creature — Forest Dryad")
    assert classify_card_type(dryad) == "land"

    # 2. Enchantment creature -> creature (Overlord of the Balemurk)
    overlord = Card(
        name="Overlord of the Balemurk",
        type_line="Enchantment Creature — Avatar Horror",
    )
    assert classify_card_type(overlord) == "creature"

    # 3. Artifact creature -> creature (Walking Ballista)
    ballista = Card(name="Walking Ballista", type_line="Artifact Creature — Construct")
    assert classify_card_type(ballista) == "creature"

    # 4. MDFC with Instant front face and Land back face -> instant (Sink into Stupor)
    sink = Card(
        name="Sink into Stupor // Soporific Springs",
        type_line="Instant // Land",
    )
    assert classify_card_type(sink) == "instant"

    # 5. MDFC with Sorcery front face and Land back face -> sorcery (Bala Ged Recovery)
    bala = Card(
        name="Bala Ged Recovery // Bala Ged Sanctuary",
        type_line="Sorcery // Land",
    )
    assert classify_card_type(bala) == "sorcery"

    # 6. Enchantment Land -> land (Urza's Saga)
    saga = Card(name="Urza's Saga", type_line="Enchantment Land — Urza's Saga")
    assert classify_card_type(saga) == "land"

    # 7. Battle MDFC -> battle (Invasion of Ikoria)
    ikoria = Card(
        name="Invasion of Ikoria // Zilortha, Apex of Ikoria",
        type_line="Battle — Siege // Legendary Creature — Dinosaur",
    )
    assert classify_card_type(ikoria) == "battle"

    # 8. None or empty type_line -> other
    assert classify_card_type(None) == "other"
    assert classify_card_type(Card(name="Mystery Card", type_line="")) == "other"


@pytest.mark.django_db
def test_deck_detail_mainboard_sections(client):
    t = Tournament.objects.create(
        id="tourn-deck-sections-test",
        name="Legacy Challenge 32",
        format="legacy",
        date=date(2024, 7, 1),
    )
    Card.objects.create(
        id="c-tamiyo",
        oracle_id="o-tamiyo",
        name="Tamiyo, Inquisitive Student",
        normalized_name="tamiyo, inquisitive student",
        type_line="Legendary Creature — Moonfolk Wizard // Legendary Planeswalker — Tamiyo",
        cmc=1.0,
    )
    Card.objects.create(
        id="c-bilbo",
        oracle_id="o-bilbo",
        name="Bilbo, Thief in the Night",
        normalized_name="bilbo, thief in the night",
        type_line="Legendary Creature — Halfling Rogue",
        cmc=2.0,
    )
    Card.objects.create(
        id="c-bowmasters",
        oracle_id="o-bowmasters",
        name="Orcish Bowmasters",
        normalized_name="orcish bowmasters",
        type_line="Creature — Orc Archer",
        cmc=2.0,
    )
    Card.objects.create(
        id="c-force",
        oracle_id="o-force",
        name="Force of Will",
        normalized_name="force of will",
        type_line="Instant",
        cmc=5.0,
    )
    Card.objects.create(
        id="c-brainstorm",
        oracle_id="o-brainstorm",
        name="Brainstorm",
        normalized_name="brainstorm",
        type_line="Instant",
        cmc=1.0,
    )
    Card.objects.create(
        id="c-island",
        oracle_id="o-island",
        name="Island",
        normalized_name="island",
        type_line="Basic Land — Island",
        cmc=0.0,
    )
    Card.objects.create(
        id="c-ee",
        oracle_id="o-ee",
        name="Engineered Explosives",
        normalized_name="engineered explosives",
        type_line="Artifact",
        cmc=0.0,
    )
    Card.objects.create(
        id="c-hydro",
        oracle_id="o-hydro",
        name="Hydroblast",
        normalized_name="hydroblast",
        type_line="Instant",
        cmc=1.0,
    )
    Card.objects.create(
        id="c-massacre",
        oracle_id="o-massacre",
        name="Massacre",
        normalized_name="massacre",
        type_line="Sorcery",
        cmc=4.0,
    )

    deck = Deck.objects.create(
        id="deck-sections-test-1",
        tournament=t,
        format="legacy",
        player="Ark4n",
        player_lower="ark4n",
        archetype="Dimir Midrange",
        archetype_slug="dimir-midrange",
        result="1st Place",
        mainboard=[
            {"card": "Force of Will", "count": 4},
            {"card": "Brainstorm", "count": 4},
            {"card": "Tamiyo, Inquisitive Student", "count": 4},
            {"card": "Orcish Bowmasters", "count": 2},
            {"card": "Bilbo, Thief in the Night", "count": 4},
            {"card": "Island", "count": 4},
        ],
        sideboard=[
            {"card": "Massacre", "count": 2},
            {"card": "Engineered Explosives", "count": 1},
            {"card": "Hydroblast", "count": 2},
        ],
    )

    response = client.get(f"/player/{deck.player}/deck/{deck.tournament_id}/")
    assert response.status_code == 200
    sections = response.context["mainboard_sections"]
    section_names = [s["name"] for s in sections]
    assert section_names == ["Creatures", "Instants", "Lands"]

    # Verify Creatures section: count=10, sorted by cmc then name
    creatures_sec = sections[0]
    assert creatures_sec["count"] == 10
    assert creatures_sec["icon"] == "creature"
    c_names = [c["card"] for c in creatures_sec["cards"]]
    # Tamiyo (1.0) < Bilbo (2.0) < Bowmasters (2.0)
    assert c_names == [
        "Tamiyo, Inquisitive Student",
        "Bilbo, Thief in the Night",
        "Orcish Bowmasters",
    ]

    # Verify Instants section: count=8, Brainstorm (1.0) < Force of Will (5.0)
    instants_sec = sections[1]
    assert instants_sec["count"] == 8
    assert [c["card"] for c in instants_sec["cards"]] == [
        "Brainstorm",
        "Force of Will",
    ]

    # Verify rendered HTML header has icon and count
    content = response.content.decode()
    assert "Creatures (10)" in content
    assert "ms-creature" in content
    assert "Instants (8)" in content
    assert "ms-instant" in content
    assert "Lands (4)" in content
    assert "ms-land" in content
    # Sideboard should be sorted by CMC ascending (EE: 0.0 < Hydroblast: 1.0 < Massacre: 4.0)
    assert "Sideboard" in content
    sb_cards = [c["card"] for c in response.context["deck"].sideboard]
    assert sb_cards == ["Engineered Explosives", "Hydroblast", "Massacre"]


@pytest.mark.django_db
def test_archetype_detail_view(client):
    deck = Deck.objects.filter(format="legacy").first()
    if deck:
        response = client.get(f"/legacy/archetype/{deck.archetype_slug}/")
        assert response.status_code == 200
        stats = response.context["stats"]
        assert "total_top8_slots" in stats
        assert "total_chall_decks" in stats
        assert "total_5_0s" in stats

        content = response.content
        assert b"League Share" in content
        assert b"League 5-0 Share" not in content
        assert b"Challenge Share" in content
        assert b"Challenge Top 8 Share" in content
        assert b"Challenge Conversion" in content

        # Check stat card ordering
        pos_ls = content.find(b"League Share")
        pos_cs = content.find(b"Challenge Share")
        pos_t8 = content.find(b"Challenge Top 8 Share")
        pos_cc = content.find(b"Challenge Conversion")
        assert pos_ls < pos_cs < pos_t8 < pos_cc

        # Check tooltips
        ls_tt = f'title="{stats["league_5_0_count"]}/{stats["total_5_0s"]}"'.encode()
        cs_tt = f'title="{stats["challenge_appearances"]}/{stats["total_chall_decks"]}"'.encode()
        t8_tt = f'title="{stats["top8_count"]}/{stats["total_top8_slots"]}"'.encode()
        cc_tt = (
            f'title="{stats["top8_count"]}/{stats["challenge_appearances"]}"'.encode()
        )
        assert ls_tt in content
        assert cs_tt in content
        assert t8_tt in content
        assert cc_tt in content

        # Verify Deck column is present with deck link and date is plain text
        assert b">Deck</th>" in content
        assert b">Date</th>" in content
        assert b"View \xe2\x86\x92" not in content
        if response.context["finishes"]:
            finish = response.context["finishes"][0]
            finish_link = f"/player/{finish.player}/deck/{finish.tournament_id}/{finish.deck_index}/".encode()
            assert finish_link in content
            date_str = finish.tournament.date.strftime("%Y-%m-%d").encode()
            assert f">{date_str.decode()}</a>".encode() not in content

        # Verify number of finishes in timeframe is shown
        assert "total_decks" in response.context
        assert (
            f"{response.context['total_decks']} finishes last {response.context['days']} days".encode()
            in content
        )

        # Verify Recent Tournament Finishes appears before Core Cards
        pos_finishes = content.find(b"Recent Tournament Finishes")
        pos_core = content.find(b"Core Cards")
        assert pos_finishes != -1 and pos_core != -1
        assert pos_finishes < pos_core

        # Verify Activity Heatmap appears after Challenge Conversion and before Recent Tournament Finishes
        pos_heatmap = content.find(b"Activity Heatmap")
        assert pos_heatmap != -1
        assert pos_cc < pos_heatmap < pos_finishes

        # Verify heatmap context
        assert "heatmap" in response.context
        heatmap = response.context["heatmap"]
        assert len(heatmap["weeks"]) == 27
        assert len(heatmap["weeks"][0]["days"]) == 7
        assert heatmap["weeks"][0]["days"][0]["date"].weekday() == 0  # Monday
        assert heatmap["weeks"][0]["days"][6]["date"].weekday() == 6  # Sunday
        assert "active_days_count" in heatmap
        assert "total_leagues" in heatmap
        assert "total_challenges" in heatmap

        # Verify Core Cards has mana_cost
        if response.context["core_cards"]:
            first_core = response.context["core_cards"][0]
            assert "mana_cost" in first_core


@pytest.mark.django_db
def test_archetype_detail_core_cards_sorting(client):
    """Core cards sort primarily by adoption % (desc) and secondarily by avg quantity (desc)."""
    t = Tournament.objects.create(
        id="tourn_core_cards_sort",
        name="Vintage Challenge",
        format="vintage",
        date=date(2024, 6, 1),
    )

    # 10 decks total:
    # - "Oath of Druids": in 10 decks, 4 copies each (100%, avg 4.0)
    # - "Black Lotus": in 10 decks, 1 copy each (100%, avg 1.0)
    # - "Force of Will": in 9 decks, 4 copies each (90%, avg 4.0)
    # - "Ancestral Recall": in 9 decks, 1 copy each (90%, avg 1.0)
    for i in range(10):
        mb = [
            {"card": "Oath of Druids", "count": 4},
            {"card": "Black Lotus", "count": 1},
        ]
        if i < 9:
            mb.append({"card": "Force of Will", "count": 4})
            mb.append({"card": "Ancestral Recall", "count": 1})

        Deck.objects.create(
            id=f"deck_sort_test_{i}",
            tournament=t,
            format="vintage",
            player=f"Player_{i}",
            player_lower=f"player_{i}",
            archetype="Oath of Druids",
            archetype_slug="oath-of-druids",
            result=f"{i + 1}th Place",
            mainboard=mb,
            sideboard=[],
        )

    response = client.get("/vintage/archetype/oath-of-druids/?days=90")
    assert response.status_code == 200
    core_cards = response.context["core_cards"]
    card_names = [c["name"] for c in core_cards]

    # 4-of at 100% must precede 1-of at 100%, which must precede 4-of at 90%, which precedes 1-of at 90%
    assert card_names == [
        "Oath of Druids",
        "Black Lotus",
        "Force of Will",
        "Ancestral Recall",
    ]


@pytest.mark.django_db
def test_archetype_detail_all_time_pagination_and_zero_timeframe(client):
    # Create anchor tournament so reference date is 2024-06-01 (making 2024-01-01 > 90d ago)
    Tournament.objects.create(
        id="tourn_anchor_arch",
        name="Legacy Anchor",
        format="legacy",
        date=date(2024, 6, 1),
    )

    # Create an archetype with finishes from 2024-01-01 (outside 90d window)
    t = Tournament.objects.create(
        id="tourn_old_arch",
        name="Legacy Challenge",
        format="legacy",
        date=date(2024, 1, 1),
    )
    for i in range(30):
        Deck.objects.create(
            id=f"tourn_old_arch_p_{i}_1",
            tournament=t,
            format="legacy",
            player=f"Player_{i}",
            player_lower=f"player_{i}",
            archetype="Orzhov Midrange",
            archetype_slug="orzhov-midrange",
            result="1st",
            mainboard=[{"card": "Swords to Plowshares", "count": 4}],
            sideboard=[],
        )

    response = client.get("/legacy/archetype/orzhov-midrange/?days=90")
    assert response.status_code == 200
    content = response.content.decode()

    # Upper right label
    assert "0 finishes last 90 days" in content

    # All-time finishes paginated (25 per page -> 30 finishes = 2 pages)
    assert "page_obj" in response.context
    page_obj = response.context["page_obj"]
    assert page_obj.paginator.count == 30
    assert len(response.context["finishes"]) == 25
    assert "1–25 of 30 finishes" in content
    assert "Next →" in content

    # Empty core cards section explanation
    assert (
        "No card statistics available for this archetype because there's no recent league or challenge decks."
        in content
    )


@pytest.mark.django_db
def test_archetype_activity_heatmap(client):
    ref = get_reference_date()

    # Create anchor tournament on ref date
    Tournament.objects.create(
        id="heatmap_tourn_anchor",
        name="Modern Anchor",
        format="modern",
        event_type="league",
        date=ref,
    )

    # 1. Day with 1x League 5-0
    t_league1 = Tournament.objects.create(
        id="heatmap_tourn_league_1",
        name="Modern League",
        format="modern",
        event_type="league",
        date=ref - timedelta(days=5),
    )
    Deck.objects.create(
        id="heatmap_deck_league_1",
        tournament=t_league1,
        format="modern",
        player="HeatmapPlayer1",
        player_lower="heatmapplayer1",
        archetype="Grixis Shadow",
        archetype_slug="grixis-shadow",
        is_5_0=True,
        result="5-0",
    )

    # 2. Day with 2x League 5-0s
    t_league2 = Tournament.objects.create(
        id="heatmap_tourn_league_2",
        name="Modern League 2",
        format="modern",
        event_type="league",
        date=ref - timedelta(days=10),
    )
    Deck.objects.create(
        id="heatmap_deck_league_2a",
        tournament=t_league2,
        format="modern",
        player="HeatmapPlayer2",
        player_lower="heatmapplayer2",
        archetype="Grixis Shadow",
        archetype_slug="grixis-shadow",
        is_5_0=True,
        result="5-0",
    )
    Deck.objects.create(
        id="heatmap_deck_league_2b",
        tournament=t_league2,
        format="modern",
        player="HeatmapPlayer3",
        player_lower="heatmapplayer3",
        archetype="Grixis Shadow",
        archetype_slug="grixis-shadow",
        is_5_0=True,
        result="5-0",
    )

    # 3. Day with Challenge entry (non-top 8)
    t_chall_entry = Tournament.objects.create(
        id="heatmap_tourn_chall_entry",
        name="Modern Challenge 32",
        format="modern",
        event_type="challenge",
        date=ref - timedelta(days=15),
    )
    Deck.objects.create(
        id="heatmap_deck_chall_entry",
        tournament=t_chall_entry,
        format="modern",
        player="HeatmapPlayer4",
        player_lower="heatmapplayer4",
        archetype="Grixis Shadow",
        archetype_slug="grixis-shadow",
        is_top8=False,
        rank=12,
        result="12th Place",
    )

    # 4. Day with both Challenge Top 8 AND League 5-0 (Priority test: Top 8 > League)
    same_day = ref - timedelta(days=20)
    t_same_chall = Tournament.objects.create(
        id="heatmap_tourn_same_chall",
        name="Modern Challenge 64",
        format="modern",
        event_type="challenge",
        date=same_day,
    )
    Deck.objects.create(
        id="heatmap_deck_same_chall",
        tournament=t_same_chall,
        format="modern",
        player="HeatmapPlayer5",
        player_lower="heatmapplayer5",
        archetype="Grixis Shadow",
        archetype_slug="grixis-shadow",
        is_top8=True,
        rank=3,
        result="3rd Place",
    )
    t_same_league = Tournament.objects.create(
        id="heatmap_tourn_same_league",
        name="Modern League Same Day",
        format="modern",
        event_type="league",
        date=same_day,
    )
    Deck.objects.create(
        id="heatmap_deck_same_league",
        tournament=t_same_league,
        format="modern",
        player="HeatmapPlayer6",
        player_lower="heatmapplayer6",
        archetype="Grixis Shadow",
        archetype_slug="grixis-shadow",
        is_5_0=True,
        result="5-0",
    )

    # 5. Day with Challenge 1st Place (Winner)
    t_winner = Tournament.objects.create(
        id="heatmap_tourn_winner",
        name="Modern Challenge Winner",
        format="modern",
        event_type="challenge",
        date=ref - timedelta(days=25),
    )
    Deck.objects.create(
        id="heatmap_deck_winner",
        tournament=t_winner,
        format="modern",
        player="HeatmapPlayer7",
        player_lower="heatmapplayer7",
        archetype="Grixis Shadow",
        archetype_slug="grixis-shadow",
        is_top8=True,
        rank=1,
        result="1st Place",
    )

    response = client.get("/modern/archetype/grixis-shadow/")
    assert response.status_code == 200
    heatmap = response.context["heatmap"]

    assert heatmap["active_days_count"] == 5
    assert heatmap["total_leagues"] == 4
    assert heatmap["total_challenges"] == 3
    assert heatmap["total_top8s"] == 2

    # Verify cell days map
    days_by_date = {}
    for w in heatmap["weeks"]:
        for d in w["days"]:
            days_by_date[d["date_str"]] = d

    # 1. League 1 day
    d1_date = ref - timedelta(days=5)
    d1_date_str = d1_date.strftime("%Y-%m-%d")
    d1 = days_by_date[d1_date_str]
    assert d1["color_class"] == "activity-league-1"
    assert d1["tooltip"] == f"{d1_date.strftime('%b')} {d1_date.day}: 1x League 5-0"
    assert d1["url"] == "/modern/tournaments/heatmap_tourn_league_1/"

    # 2. League 2 day
    d2_date = ref - timedelta(days=10)
    d2_date_str = d2_date.strftime("%Y-%m-%d")
    d2 = days_by_date[d2_date_str]
    assert d2["color_class"] == "activity-league-2"
    assert d2["tooltip"] == f"{d2_date.strftime('%b')} {d2_date.day}: 2x League 5-0s"
    assert d2["url"] == "/modern/tournaments/heatmap_tourn_league_2/"

    # 3. Challenge entry day
    d3_date = ref - timedelta(days=15)
    d3_date_str = d3_date.strftime("%Y-%m-%d")
    d3 = days_by_date[d3_date_str]
    assert d3["color_class"] == "activity-challenge-entry"
    assert (
        d3["tooltip"] == f"{d3_date.strftime('%b')} {d3_date.day}: 1x Challenge entry"
    )
    assert d3["url"] == "/modern/tournaments/heatmap_tourn_chall_entry/"

    # 4. Same day: Challenge Top 8 + League 5-0 (Challenge priority for color and link)
    d4_date_str = same_day.strftime("%Y-%m-%d")
    d4 = days_by_date[d4_date_str]
    assert d4["color_class"] == "activity-challenge-top8"
    assert (
        d4["tooltip"]
        == f"{same_day.strftime('%b')} {same_day.day}: 1x Challenge Top 8, 1x League 5-0"
    )
    assert d4["url"] == "/modern/tournaments/heatmap_tourn_same_chall/"

    # 5. Challenge winner day
    d5_date = ref - timedelta(days=25)
    d5_date_str = d5_date.strftime("%Y-%m-%d")
    d5 = days_by_date[d5_date_str]
    assert d5["color_class"] == "activity-challenge-winner"
    assert (
        d5["tooltip"] == f"{d5_date.strftime('%b')} {d5_date.day}: 1x Challenge Top 8"
    )
    assert d5["url"] == "/modern/tournaments/heatmap_tourn_winner/"

    # 6. Inactive day (no results -> no url link)
    d_inactive_date = ref - timedelta(days=3)
    d_inactive_date_str = d_inactive_date.strftime("%Y-%m-%d")
    d_inactive = days_by_date[d_inactive_date_str]
    assert d_inactive["color_class"] == "activity-level-0"
    assert (
        d_inactive["tooltip"]
        == f"{d_inactive_date.strftime('%b')} {d_inactive_date.day}: No tournament finishes"
    )
    assert d_inactive["url"] == ""

    # Verify link appears in rendered HTML
    assert b'href="/modern/tournaments/heatmap_tourn_same_chall/"' in response.content

    # 7. Check 30d toggle keeps 27 weeks in heatmap
    resp_30 = client.get("/modern/archetype/grixis-shadow/?days=30")
    assert resp_30.status_code == 200
    assert len(resp_30.context["heatmap"]["weeks"]) == 27


@pytest.mark.django_db
def test_player_activity_heatmap(client):
    ref = get_reference_date()

    # Create anchor tournament on ref date
    Tournament.objects.create(
        id="player_tourn_anchor",
        name="Modern Anchor",
        format="modern",
        event_type="league",
        date=ref,
    )

    player_name = "HeatmapHero"
    player_lower = player_name.lower()

    # 1. Day with 1x League 5-0 (Modern)
    t_league = Tournament.objects.create(
        id="player_tourn_league",
        name="Modern League",
        format="modern",
        event_type="league",
        date=ref - timedelta(days=5),
    )
    Deck.objects.create(
        id="player_deck_league",
        tournament=t_league,
        format="modern",
        player=player_name,
        player_lower=player_lower,
        archetype="Murktide",
        archetype_slug="murktide",
        is_5_0=True,
        result="5-0",
    )

    # 2. Day with 1x Challenge Top 8 (Legacy)
    t_chall_t8 = Tournament.objects.create(
        id="player_tourn_chall_t8",
        name="Legacy Challenge",
        format="legacy",
        event_type="challenge",
        date=ref - timedelta(days=12),
    )
    Deck.objects.create(
        id="player_deck_chall_t8",
        tournament=t_chall_t8,
        format="legacy",
        player=player_name,
        player_lower=player_lower,
        archetype="Delver",
        archetype_slug="delver",
        is_top8=True,
        rank=4,
        result="4th Place",
    )

    # 3. Day with 1x Challenge Winner (Pauper)
    t_chall_win = Tournament.objects.create(
        id="player_tourn_chall_win",
        name="Pauper Challenge",
        format="pauper",
        event_type="challenge",
        date=ref - timedelta(days=20),
    )
    Deck.objects.create(
        id="player_deck_chall_win",
        tournament=t_chall_win,
        format="pauper",
        player=player_name,
        player_lower=player_lower,
        archetype="Burn",
        archetype_slug="burn",
        is_top8=True,
        rank=1,
        result="1st Place",
    )

    # 4. Day with 1x Challenge Entry non-T8 (Pioneer)
    t_chall_entry = Tournament.objects.create(
        id="player_tourn_chall_entry",
        name="Pioneer Challenge",
        format="pioneer",
        event_type="challenge",
        date=ref - timedelta(days=25),
    )
    Deck.objects.create(
        id="player_deck_chall_entry",
        tournament=t_chall_entry,
        format="pioneer",
        player=player_name,
        player_lower=player_lower,
        archetype="Phoenix",
        archetype_slug="phoenix",
        is_top8=False,
        rank=15,
        result="15th Place",
    )

    response = client.get(f"/player/{player_name}/")
    assert response.status_code == 200
    assert "heatmap" in response.context
    heatmap = response.context["heatmap"]

    assert len(heatmap["weeks"]) == 27
    assert heatmap["active_days_count"] == 4
    assert heatmap["total_leagues"] == 1
    assert heatmap["total_challenges"] == 3
    assert heatmap["total_top8s"] == 2
    assert heatmap["aria_label"] == f"{player_name}'s 26-week activity heatmap"

    # Verify cell days map
    days_by_date = {}
    for w in heatmap["weeks"]:
        for d in w["days"]:
            days_by_date[d["date_str"]] = d

    # 1. League day
    d_lg = days_by_date[(ref - timedelta(days=5)).strftime("%Y-%m-%d")]
    assert d_lg["color_class"] == "activity-league-1"
    assert d_lg["url"] == "/modern/tournaments/player_tourn_league/"

    # 2. Challenge T8 day (cross-format: legacy)
    d_t8 = days_by_date[(ref - timedelta(days=12)).strftime("%Y-%m-%d")]
    assert d_t8["color_class"] == "activity-challenge-top8"
    assert d_t8["url"] == "/legacy/tournaments/player_tourn_chall_t8/"

    # 3. Challenge winner day (pauper)
    d_win = days_by_date[(ref - timedelta(days=20)).strftime("%Y-%m-%d")]
    assert d_win["color_class"] == "activity-challenge-winner"
    assert d_win["url"] == "/pauper/tournaments/player_tourn_chall_win/"

    # 4. Challenge entry day (pioneer)
    d_entry = days_by_date[(ref - timedelta(days=25)).strftime("%Y-%m-%d")]
    assert d_entry["color_class"] == "activity-challenge-entry"
    assert d_entry["url"] == "/pioneer/tournaments/player_tourn_chall_entry/"

    # Verify SVG markup in rendered HTML
    assert b"26-week activity heatmap" in response.content
    assert b'href="/legacy/tournaments/player_tourn_chall_t8/"' in response.content
    assert b"activity-challenge-winner" in response.content


@pytest.mark.django_db
def test_cards_view(client):
    cache.clear()

    # 1. 90d All (Leagues + Challenges)
    resp_90_all = client.get("/legacy/cards/?days=90")
    assert resp_90_all.status_code == 200
    assert b"Cards" in resp_90_all.content
    assert b"Challenges" in resp_90_all.content
    assert b"Leagues" in resp_90_all.content
    assert resp_90_all.context["page_obj"].paginator.per_page == 100
    assert cache.get("cards_list_v4:legacy:90:all") is not None

    # 2. 30d All
    resp_30_all = client.get("/legacy/cards/?days=30")
    assert resp_30_all.status_code == 200
    assert cache.get("cards_list_v4:legacy:30:all") is not None

    # 3. 90d Challenges only
    resp_90_chall = client.get("/legacy/cards/?type=challenge&days=90")
    assert resp_90_chall.status_code == 200
    assert cache.get("cards_list_v4:legacy:90:challenge") is not None

    # 4. 90d Leagues only
    resp_90_league = client.get("/legacy/cards/?type=league&days=90")
    assert resp_90_league.status_code == 200
    assert cache.get("cards_list_v4:legacy:90:league") is not None

    # Verify they are all distinct cached items
    k_90_all = cache.get("cards_list_v4:legacy:90:all")
    k_30_all = cache.get("cards_list_v4:legacy:30:all")
    k_90_chall = cache.get("cards_list_v4:legacy:90:challenge")
    k_90_league = cache.get("cards_list_v4:legacy:90:league")

    assert k_90_all is not None
    assert k_30_all is not None
    assert k_90_chall is not None
    assert k_90_league is not None


@pytest.mark.django_db
def test_leaderboard_view(client):
    response = client.get("/legacy/leaderboard/")
    assert response.status_code == 200
    assert b"Leaderboard" in response.content


@pytest.mark.django_db
def test_deck_detail_missing_knn_index_warning(client, settings):
    set_global_knn_index(None, loaded=False)
    settings.KNN_INDEX_PATH = "/tmp/non_existent_knn_index.npz"

    deck = Deck.objects.first()
    if deck:
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            response = client.get(f"/player/{deck.player}/deck/{deck.tournament_id}/")
            assert response.status_code == 200
            assert any("kNN index file not found" in str(w.message) for w in recorded)
            assert b"Mainboard" in response.content


@pytest.mark.django_db
def test_deck_detail_banned_card_badge(client):
    deck = Deck.objects.filter(player_lower="univerce").first()
    if deck:
        response = client.get(f"/player/{deck.player}/deck/{deck.tournament_id}/1/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Banned" in content
        assert "Historical / Not Legal Today" not in content
        assert "Banned / Illegal cards in current" not in content


@pytest.mark.django_db
def test_deck_detail_similar_decks_mana_symbols(client):
    set_global_knn_index(None, loaded=False)
    deck = Deck.objects.filter(player="Rexplosion").first() or Deck.objects.first()
    if deck:
        response = client.get(f"/player/{deck.player}/deck/{deck.tournament_id}/1/")
        assert response.status_code == 200
        format_neighbors = response.context.get("format_neighbors", [])
        cross_format_neighbors = response.context.get("cross_format_neighbors", [])
        if format_neighbors:
            assert "colors" in format_neighbors[0]
            assert "color_name" in format_neighbors[0]
            if format_neighbors[0]["colors"]:
                assert "ms ms-" in response.content.decode()
        if cross_format_neighbors:
            assert "colors" in cross_format_neighbors[0]
            assert "color_name" in cross_format_neighbors[0]


@pytest.mark.django_db
def test_deck_detail_similar_decks_uses_db_archetype_over_stale_knn_index(client):
    t = Tournament.objects.create(
        id="tourn_stale_test",
        name="Legacy Challenge",
        format="legacy",
        date=date(2026, 1, 1),
    )
    d1 = Deck.objects.create(
        id="tourn_stale_test_p1_1",
        tournament=t,
        format="legacy",
        player="Player1",
        player_lower="player1",
        archetype="Storm",
        archetype_slug="storm",
        mainboard=[{"card": "Brainstorm", "count": 4}],
        sideboard=[],
    )
    d2 = Deck.objects.create(
        id="tourn_stale_test_p2_1",
        tournament=t,
        format="legacy",
        player="Player2",
        player_lower="player2",
        archetype="Saga Storm",  # Updated in DB!
        archetype_slug="saga-storm",
        colors="BRG",
        color_name="Jund",
        mainboard=[{"card": "Brainstorm", "count": 4}],
        sideboard=[],
    )

    # Mock kNN index returning stale archetype in raw results
    mock_index = MagicMock()
    mock_index.vector_from_decklist.return_value = MagicMock()
    mock_index.query.side_effect = [
        [
            {
                "deck_id": d2.id,
                "player": "Player2",
                "archetype": "Old Stale Storm",  # Stale in index!
                "colors": "UB",
                "color_name": "Dimir",
                "format": "legacy",
                "date": date(2026, 1, 1),
                "raw_similarity": 0.99,
                "recency_weight": 1.0,
                "score": 0.99,
            }
        ],
        [],  # cross format
    ]

    set_global_knn_index(mock_index, loaded=True)
    try:
        response = client.get(f"/player/{d1.player}/deck/{t.id}/1/")
        assert response.status_code == 200
        format_neighbors = response.context.get("format_neighbors", [])
        assert len(format_neighbors) == 1
        # Must reflect the canonical database archetype, not the stale index value!
        assert format_neighbors[0]["archetype"] == "Saga Storm"
        assert format_neighbors[0]["colors"] == "BRG"
        assert format_neighbors[0]["color_name"] == "Jund"
    finally:
        set_global_knn_index(None, loaded=False)


@pytest.mark.django_db
def test_faq_view(client):
    response = client.get("/faq/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Frequently Asked Questions" in content
    assert "Scryfall" in content
    assert "https://github.com/andrewgioia/Mana" in content
    assert "MTG_decklistcache" in content
    assert "top 32 decks" in content
    assert "Last Chance Qualifiers" in content
    assert "Preliminaries" in content
    assert "Admin Panel" not in content
    assert 'id="credits-and-acknowledgments"' in content
    assert 'href="#credits-and-acknowledgments"' in content
    assert 'id="tournament-data"' in content
    assert 'id="metagame-metrics"' in content


@pytest.mark.django_db
def test_cards_list_view_external_links(client):
    response = client.get("/legacy/cards/")
    assert response.status_code == 200
    assert "cards" in response.context
    cards = response.context["cards"]
    if cards:
        first_card = cards[0]
        assert "scryfall_url" in first_card
        assert "gatherer_url" in first_card
        assert "mana_cost" in first_card
        assert "scryfall.com" in first_card["scryfall_url"]
        assert (
            "gatherer.wizards.com/Pages/Card/Details.aspx" in first_card["gatherer_url"]
        )

        content = response.content.decode()
        assert first_card["scryfall_url"] in content
        assert first_card["gatherer_url"] in content
        assert "Gatherer" in content
        assert "Scryfall" in content
        assert "cursor-help" not in content

        # Verify mana icons render when cards have a mana cost
        cards_with_cost = [c for c in cards if c.get("mana_cost")]
        if cards_with_cost:
            assert "ms ms-" in content


@pytest.mark.django_db
def test_mdfc_mana_cost_display(client):
    # 1. Test _extract_mana_cost on MDFC structure
    mdfc_item = {
        "name": "Sink into Stupor // Soporific Springs",
        "mana_cost": None,
        "card_faces": [
            {"name": "Sink into Stupor", "mana_cost": "{1}{U}{U}"},
            {"name": "Soporific Springs", "mana_cost": ""},
        ],
    }
    assert _extract_mana_cost(mdfc_item) == "{1}{U}{U}"

    tamiyo_item = {
        "name": "Tamiyo, Inquisitive Student // Tamiyo, Seasoned Scholar",
        "mana_cost": None,
        "card_faces": [
            {"name": "Tamiyo, Inquisitive Student", "mana_cost": "{U}"},
            {"name": "Tamiyo, Seasoned Scholar", "mana_cost": ""},
        ],
    }
    assert _extract_mana_cost(tamiyo_item) == "{U}"

    split_item = {
        "name": "Fire // Ice",
        "mana_cost": "{1}{R} // {1}{U}",
        "card_faces": [
            {"name": "Fire", "mana_cost": "{1}{R}"},
            {"name": "Ice", "mana_cost": "{1}{U}"},
        ],
    }
    assert _extract_mana_cost(split_item) == "{1}{R} // {1}{U}"

    dual_spell_mdfc = {
        "name": "Shaile, Dean of Radiance // Embrose, Dean of Shadow",
        "mana_cost": None,
        "card_faces": [
            {"name": "Shaile, Dean of Radiance", "mana_cost": "{1}{W}"},
            {"name": "Embrose, Dean of Shadow", "mana_cost": "{2}{B}{B}"},
        ],
    }
    assert _extract_mana_cost(dual_spell_mdfc) == "{1}{W} // {2}{B}{B}"


@pytest.mark.django_db
def test_format_overview_archetype_colors_and_card_mana_symbols(client):
    cache.clear()

    Card.objects.create(name="Force of Will", mana_cost="{3}{U}{U}")
    t = Tournament.objects.create(
        id="legacy-challenge-overview-test",
        name="Legacy Challenge Test",
        format="legacy",
        event_type="challenge",
        date=date(2024, 1, 1),
    )
    Deck.objects.create(
        id="legacy-deck-overview-test-1",
        tournament=t,
        format="legacy",
        player="PlayerOne",
        player_lower="playerone",
        archetype="Dimir Tempo",
        archetype_slug="dimir-tempo",
        colors="UB",
        color_name="Dimir",
        result="1st Place",
        is_top8=True,
        mainboard=[{"card": "Force of Will", "count": 4}],
        sideboard=[],
    )
    response = client.get("/legacy/?days=90")
    assert response.status_code == 200
    content = response.content
    assert b">Colors</th>" not in content
    # Archetype color icons
    assert b'title="Dimir"' in content
    assert b"ms-u" in content
    assert b"ms-b" in content
    # Card mana cost icons in Most Played Cards
    assert b"ms-3" in content


@pytest.mark.django_db
def test_search_index_api(client):
    t = Tournament.objects.create(
        id="tourn-search-test",
        name="Legacy Challenge",
        format="legacy",
        date=date(2024, 1, 1),
    )
    Deck.objects.create(
        id="deck-search-test",
        tournament=t,
        format="legacy",
        player="SearchHero",
        player_lower="searchhero",
        archetype="Dimir Tempo",
        archetype_slug="dimir-tempo",
        result="1st",
    )

    response = client.get("/api/search-index/")
    assert response.status_code == 200
    assert "public, max-age=3600" in response.headers.get("Cache-Control", "")

    data = response.json()
    assert "archetypes" in data
    assert "players" in data

    arch_names = [a["name"] for a in data["archetypes"]]
    assert "Dimir Tempo" in arch_names

    player_names = [p["name"] for p in data["players"]]
    assert "SearchHero" in player_names


@pytest.mark.django_db
def test_build_search_index_command(tmp_path):
    """build_search_index command generates a valid JSON file."""
    t = Tournament.objects.create(
        id="tourn-cmd-test",
        name="Vintage Challenge",
        format="vintage",
        date=date(2024, 1, 1),
    )
    Deck.objects.create(
        id="deck-cmd-test",
        tournament=t,
        format="vintage",
        player="CmdHero",
        player_lower="cmdhero",
        archetype="Oath of Druids",
        archetype_slug="oath-of-druids",
        result="1st",
    )
    dest = tmp_path / "search_index.json"
    out = StringIO()
    call_command("build_search_index", output=str(dest), stdout=out)
    assert dest.is_file()
    data = json.loads(dest.read_text())
    assert any(a["name"] == "Oath of Druids" for a in data["archetypes"])
    assert any(p["name"] == "CmdHero" for p in data["players"])


def test_search_index_serves_static_file(client, tmp_path, settings):
    """search_index view serves static file directly when available outside tests."""
    dest = tmp_path / "search_index.json"
    dest.write_text('{"archetypes":[{"name":"StaticArch"}],"players":[]}')
    settings.SEARCH_INDEX_PATH = dest
    settings.IS_TESTING = False
    response = client.get("/api/search-index/")
    assert response.status_code == 200
    assert response.json()["archetypes"][0]["name"] == "StaticArch"


@pytest.mark.django_db
def test_search_box_in_header_replaces_window_switcher(client):
    response = client.get("/legacy/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Global search input must exist
    assert 'id="global-search-input"' in content
    assert 'placeholder="Search archetypes &amp; players..."' in content

    # Header should not contain Window: timeframe switcher
    header_start = content.find("<header")
    header_end = content.find("</header>")
    assert header_start != -1 and header_end != -1
    header_html = content[header_start:header_end]
    assert "Window:" not in header_html
    assert "data-timeframe-toggle" not in header_html

    # But sidebar aside still contains the timeframe switcher
    aside_start = content.find("<aside")
    aside_end = content.find("</aside>")
    assert aside_start != -1 and aside_end != -1
    aside_html = content[aside_start:aside_end]
    assert 'data-timeframe-toggle="30"' in aside_html
    assert 'data-timeframe-toggle="90"' in aside_html


@pytest.mark.django_db
def test_active_formats_hidden_from_navigation_and_homepage(client):
    """Only active formats should appear in the left navigation and homepage format cards."""
    active_slugs = getattr(settings, "ACTIVE_FORMAT_SLUGS", settings.MODOMETA_FORMATS)
    hidden_slugs = [
        slug for slug in settings.MODOMETA_FORMATS if slug not in active_slugs
    ]

    response = client.get("/")
    assert response.status_code == 200

    # Homepage format cards
    format_slugs = [item["format_slug"] for item in response.context["format_metas"]]
    assert format_slugs == active_slugs

    # Navigation sidebar items
    nav_formats = [fmt["slug"] for fmt in response.context["MODOMETA_FORMATS"]]
    assert nav_formats == active_slugs

    # Verify HTML sidebar lists active formats and excludes hidden formats
    content = response.content.decode("utf-8")
    aside_start = content.find("<aside")
    aside_end = content.find("</aside>")
    sidebar_html = content[aside_start:aside_end]

    for slug in active_slugs:
        assert f'href="/{slug}/?days=90"' in sidebar_html

    for slug in hidden_slugs:
        assert f'href="/{slug}/?days=90"' not in sidebar_html


@pytest.mark.django_db
def test_hidden_formats_still_accessible_via_direct_url_for_search(client):
    """Formats outside active formats are accessible directly (e.g. from search results)."""
    active_slugs = getattr(settings, "ACTIVE_FORMAT_SLUGS", settings.MODOMETA_FORMATS)
    hidden_slugs = [
        slug for slug in settings.MODOMETA_FORMATS if slug not in active_slugs
    ]
    test_slug = (
        "modern"
        if "modern" in hidden_slugs
        else (hidden_slugs[0] if hidden_slugs else "modern")
    )

    # Inactive/hidden format overview should return 200 OK (not 404)
    response = client.get(f"/{test_slug}/")
    assert response.status_code == 200
    assert f"{test_slug.capitalize()} Metagame".encode() in response.content

    # The sidebar navigation on the page still only displays active formats
    nav_formats = [fmt["slug"] for fmt in response.context["MODOMETA_FORMATS"]]
    assert nav_formats == active_slugs


def test_healthz_endpoint(client):
    """Health check endpoint should return 200 OK."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.content == b"OK"


def test_robots_txt_endpoint(client):
    """Robots.txt endpoint should return 200 OK with plain text from template."""
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert b"User-agent: *" in response.content
    assert b"Crawl-delay: 2" in response.content


def test_public_cache_decorator():
    """public_cache decorator should emit public, browser max-age, and matching CDN max-age."""
    rf = RequestFactory()

    @public_cache(cdn_seconds=7200, browser_seconds=120)
    def dummy_view(request):
        return HttpResponse("ok")

    response = dummy_view(rf.get("/test-dummy/"))
    assert response.status_code == 200
    assert "public" in response.headers["Cache-Control"]
    assert "max-age=120" in response.headers["Cache-Control"]
    assert "s-maxage=7200" in response.headers["Cache-Control"]
    assert response.headers["Cloudflare-CDN-Cache-Control"] == "max-age=7200"


@pytest.mark.django_db
def test_cache_headers_emitted(client):
    """Production views should emit valid Cache-Control and Cloudflare-CDN-Cache-Control headers."""
    for path in ["/", "/faq/"]:
        resp = client.get(path)
        assert resp.status_code == 200

        cache_control = resp.headers.get("Cache-Control", "")
        cf_cache = resp.headers.get("Cloudflare-CDN-Cache-Control", "")

        assert "public" in cache_control

        s_max = re.search(r"s-maxage=(\d+)", cache_control)
        max_age = re.search(r"max-age=(\d+)", cache_control)
        cf_max = re.search(r"max-age=(\d+)", cf_cache)

        assert s_max is not None, f"s-maxage missing in Cache-Control for {path}"
        assert max_age is not None, f"max-age missing in Cache-Control for {path}"
        assert cf_max is not None, (
            f"max-age missing in Cloudflare-CDN-Cache-Control for {path}"
        )

        # Cloudflare CDN TTL should mirror origin s-maxage directive
        assert s_max.group(1) == cf_max.group(1)
        assert int(s_max.group(1)) > 0
        assert int(max_age.group(1)) > 0


@pytest.mark.django_db
def test_deck_detail_resolves_mdfc_image_uri(client):
    """Deck detail view resolves image_uri for DFC/MDFC cards listed by front face name."""
    card = Card.objects.create(
        id="test-outland-liberator-uuid",
        name="Outland Liberator // Frenzied Trapbreaker",
        normalized_name="outland liberator // frenzied trapbreaker",
        mana_cost="{1}{G}",
        cmc=2.0,
        type_line="Creature — Human Werewolf",
        image_uri="https://cards.scryfall.io/test-outland-liberator.jpg",
    )
    CardLookup.objects.create(
        lookup_name="outland liberator",
        canonical_name="Outland Liberator // Frenzied Trapbreaker",
        card=card,
        priority=10,
    )

    t = Tournament.objects.create(
        id="legacy-league-mdfc-test",
        name="Legacy League",
        format="legacy",
        event_type="league",
        date=date(2024, 7, 7),
    )
    deck = Deck.objects.create(
        id="legacy-league-mdfc-test_player_1",
        tournament=t,
        format="legacy",
        player="Moonmadness-_-",
        player_lower="moonmadness-_-",
        archetype="Maverick",
        mainboard=[{"card": "Outland Liberator", "count": 1}],
        sideboard=[],
    )

    response = client.get(f"/player/{deck.player}/deck/{t.id}/1/")
    assert response.status_code == 200

    mb_items = response.context["deck"].mainboard
    assert len(mb_items) == 1
    assert mb_items[0]["card"] == "Outland Liberator"
    assert (
        mb_items[0]["image_uri"]
        == "https://cards.scryfall.io/test-outland-liberator.jpg"
    )
    assert mb_items[0]["mana_cost"] == "{1}{G}"
    assert (
        b'data-card-image="https://cards.scryfall.io/test-outland-liberator.jpg"'
        in response.content
    )


@pytest.mark.django_db
def test_site_name_context_processor_and_title(client):
    """SITE_NAME is provided by modometa_globals and rendered in title tags."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.context["SITE_NAME"] == "MODOMeta"
    # Ensure title contains the page title and SITE_NAME
    assert (
        "<title>MTGO Metagame Overview — MODOMeta</title>"
        in response.content.decode("utf-8")
    )

    # Ensure footer uses SITE_NAME
    assert "MODOMeta is unofficial Fan Content" in response.content.decode("utf-8")


def test_no_hardcoded_internal_links_in_templates():
    """Verify that templates use Django's {% url %} rather than hardcoded root paths in hrefs."""
    template_dir = Path(__file__).resolve().parent.parent / "core" / "templates"
    pattern = re.compile(r'href="/(?!/|\s)')

    violations = []
    for html_file in template_dir.glob("*.html"):
        with open(html_file, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f, 1):
                if pattern.search(line):
                    violations.append(f"{html_file.name}:{idx}: {line.strip()}")

    assert not violations, "Found hardcoded internal links in templates:\n" + "\n".join(
        violations
    )


@pytest.mark.django_db
def test_format_overview_bump_chart_and_momentum(client):
    """Test 26-week bump chart SSR SVG generation and T8 Momentum calculation."""
    ref = get_reference_date()

    # Create tournaments across the last 26 weeks and prior 90-day window
    # Archetype A: Surging in recent 90d (3 T8s in last 90d, 0 in prior 90d) -> momentum +3
    # Archetype B: Declining (1 T8 in last 90d, 4 in prior 90d) -> momentum -3
    # Archetype C: Stable (2 T8s in last 90d, 2 in prior 90d) -> momentum 0

    t_recent_1 = Tournament.objects.create(
        id="t_recent_1",
        name="Pauper Challenge 1",
        format="pauper",
        event_type="challenge",
        date=ref - timedelta(days=5),
    )
    Deck.objects.create(
        id="d_recent_a1",
        tournament=t_recent_1,
        format="pauper",
        player="PlayerA",
        player_lower="playera",
        archetype="Kuldotha Red",
        archetype_slug="kuldotha-red",
        is_top8=True,
        rank=1,
        result="1st Place",
    )
    Deck.objects.create(
        id="d_recent_b1",
        tournament=t_recent_1,
        format="pauper",
        player="PlayerB",
        player_lower="playerb",
        archetype="Dimir Terror",
        archetype_slug="dimir-terror",
        is_top8=True,
        rank=2,
        result="2nd Place",
    )
    Deck.objects.create(
        id="d_recent_c1",
        tournament=t_recent_1,
        format="pauper",
        player="PlayerC",
        player_lower="playerc",
        archetype="Golgari Gardens",
        archetype_slug="golgari-gardens",
        is_top8=True,
        rank=3,
        result="3rd Place",
    )

    t_recent_2 = Tournament.objects.create(
        id="t_recent_2",
        name="Pauper Challenge 2",
        format="pauper",
        event_type="challenge",
        date=ref - timedelta(days=12),
    )
    Deck.objects.create(
        id="d_recent_a2",
        tournament=t_recent_2,
        format="pauper",
        player="PlayerA2",
        player_lower="playera2",
        archetype="Kuldotha Red",
        archetype_slug="kuldotha-red",
        is_top8=True,
        rank=1,
        result="1st Place",
    )
    Deck.objects.create(
        id="d_recent_c2",
        tournament=t_recent_2,
        format="pauper",
        player="PlayerC2",
        player_lower="playerc2",
        archetype="Golgari Gardens",
        archetype_slug="golgari-gardens",
        is_top8=True,
        rank=2,
        result="2nd Place",
    )

    t_recent_3 = Tournament.objects.create(
        id="t_recent_3",
        name="Pauper Challenge 3",
        format="pauper",
        event_type="challenge",
        date=ref - timedelta(days=20),
    )
    Deck.objects.create(
        id="d_recent_a3",
        tournament=t_recent_3,
        format="pauper",
        player="PlayerA3",
        player_lower="playera3",
        archetype="Kuldotha Red",
        archetype_slug="kuldotha-red",
        is_top8=True,
        rank=1,
        result="1st Place",
    )

    # Tournaments in prior 90-day window [ref - 180d, ref - 90d)
    t_prior_1 = Tournament.objects.create(
        id="t_prior_1",
        name="Pauper Challenge Prior 1",
        format="pauper",
        event_type="challenge",
        date=ref - timedelta(days=110),
    )
    for i in range(4):
        Deck.objects.create(
            id=f"d_prior_b{i}",
            tournament=t_prior_1,
            format="pauper",
            player=f"PlayerB_Prior_{i}",
            player_lower=f"playerb_prior_{i}",
            archetype="Dimir Terror",
            archetype_slug="dimir-terror",
            is_top8=True,
            rank=i + 1,
            result=f"{i + 1}th Place",
        )
    for i in range(2):
        Deck.objects.create(
            id=f"d_prior_c{i}",
            tournament=t_prior_1,
            format="pauper",
            player=f"PlayerC_Prior_{i}",
            player_lower=f"playerc_prior_{i}",
            archetype="Golgari Gardens",
            archetype_slug="golgari-gardens",
            is_top8=True,
            rank=i + 5,
            result=f"{i + 5}th Place",
        )

    # 1. Request format overview for pauper
    response = client.get("/pauper/?days=90")
    assert response.status_code == 200

    # 2. Check Bump Chart context
    assert "bump_chart" in response.context
    bump_chart = response.context["bump_chart"]
    assert bump_chart["has_data"] is True
    assert bump_chart["svg_width"] == 411
    assert bump_chart["svg_height"] == 184
    assert len(bump_chart["rank_lines"]) == 10
    assert len(bump_chart["month_labels"]) >= 1
    assert len(bump_chart["tracks"]) >= 1

    # Check SVG rendered in response
    content = response.content.decode("utf-8")
    assert 'class="bump-track"' in content
    assert 'class="bump-node"' in content
    assert 'viewBox="0 0 411 184"' in content
    assert ">#1</text>" not in content
    assert ">#5</text>" not in content
    assert 'text-anchor="end"' in content
    for m in bump_chart["month_labels"]:
        if m.get("anchor") == "end":
            assert m["x"] <= bump_chart["x_end"]
        else:
            assert m["x"] + 24 <= bump_chart["svg_width"]

    # 3. Check T8 Momentum calculations in table
    arch_dict = {a["slug"]: a for a in response.context["archetypes"]}
    assert "kuldotha-red" in arch_dict
    assert "dimir-terror" in arch_dict
    assert "golgari-gardens" in arch_dict

    # Kuldotha Red: 3 recent, 0 prior => momentum +3
    assert arch_dict["kuldotha-red"]["top8_count"] == 3
    assert arch_dict["kuldotha-red"]["prev_top8_count"] == 0
    assert arch_dict["kuldotha-red"]["t8_momentum"] == 3
    assert "+3 ▲" in content

    # Dimir Terror: 1 recent, 4 prior => momentum -3
    assert arch_dict["dimir-terror"]["top8_count"] == 1
    assert arch_dict["dimir-terror"]["prev_top8_count"] == 4
    assert arch_dict["dimir-terror"]["t8_momentum"] == -3
    assert "-3 ▼" in content

    # Golgari Gardens: 2 recent, 2 prior => momentum 0
    assert arch_dict["golgari-gardens"]["top8_count"] == 2
    assert arch_dict["golgari-gardens"]["prev_top8_count"] == 2
    assert arch_dict["golgari-gardens"]["t8_momentum"] == 0
    assert "0 ▬" in content

    # 4. Check that 30d toggle recalculates momentum against prior 30d
    resp_30 = client.get("/pauper/?days=30")
    assert resp_30.status_code == 200
    arch_dict_30 = {a["slug"]: a for a in resp_30.context["archetypes"]}
    # In prior 30d window [ref-60, ref-30), there were 0 tournaments, so prior=0 for all
    assert arch_dict_30["kuldotha-red"]["prev_top8_count"] == 0
    assert arch_dict_30["kuldotha-red"]["t8_momentum"] == 3
    # Bump chart retains 26 weeks of data regardless of ?days=30
    assert resp_30.context["bump_chart"]["has_data"] is True


@pytest.mark.django_db
def test_format_overview_unsupported_format_notification(client):
    """Unsupported formats (not in ACTIVE_FORMAT_SLUGS) display a work-in-progress banner."""
    # 1. Supported format (legacy) should NOT display notification
    resp_supported = client.get("/legacy/")
    assert resp_supported.status_code == 200
    assert resp_supported.context["is_supported_format"] is True
    assert (
        "https://github.com/davidfischer/modometa/tree/main/archetypes"
        not in resp_supported.content.decode()
    )

    # 2. Unsupported format (pauper) SHOULD display notification
    resp_unsupported = client.get("/pauper/")
    assert resp_unsupported.status_code == 200
    assert resp_unsupported.context["is_supported_format"] is False
    content = resp_unsupported.content.decode()
    assert "https://github.com/davidfischer/modometa/tree/main/archetypes" in content
    assert "Work in progress:" in content
    assert "Archetypes for Pauper are a work in progress" in content

    # 3. If settings.ACTIVE_FORMAT_SLUGS is overridden to include pauper, notification disappears
    with override_settings(ACTIVE_FORMAT_SLUGS=["pauper"]):
        resp_overridden = client.get("/pauper/")
        assert resp_overridden.status_code == 200
        assert resp_overridden.context["is_supported_format"] is True
        assert (
            "https://github.com/davidfischer/modometa/tree/main/archetypes"
            not in resp_overridden.content.decode()
        )
