"""Integration tests for Modometa routes and views."""

from datetime import date

import pytest
from django.test import Client

from core.models import Deck
from core.models import Tournament


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

    # Verify column header order: League Share before Challenge Share
    content = response.content
    pos_ls = content.find(b">League Share</th>")
    pos_cs = content.find(b">Challenge Share</th>")
    assert pos_ls != -1 and pos_cs != -1
    assert pos_ls < pos_cs
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
def test_player_detail_view(client):
    deck = Deck.objects.first()
    if deck:
        response = client.get(f"/player/{deck.player}/")
        assert response.status_code == 200
        assert deck.player.encode() in response.content
        assert b"League 5-0 Trophies" in response.content
        assert b"all time" in response.content
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
    deck = Deck.objects.first()
    if deck:
        response = client.get(f"/player/{deck.player}/deck/{deck.tournament_id}/")
        assert response.status_code == 200
        assert b"Mainboard" in response.content
        assert b"Similar" in response.content


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
        cc_tt = f'title="{stats["top8_count"]}/{stats["challenge_appearances"]}"'.encode()
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
        assert f'{response.context["total_decks"]} finishes last {response.context["days"]} days'.encode() in content

        # Verify Recent Tournament Finishes appears before Core Cards
        pos_finishes = content.find(b"Recent Tournament Finishes")
        pos_core = content.find(b"Core Cards")
        assert pos_finishes != -1 and pos_core != -1
        assert pos_finishes < pos_core

        # Verify Core Cards has mana_cost
        if response.context["core_cards"]:
            first_core = response.context["core_cards"][0]
            assert "mana_cost" in first_core


@pytest.mark.django_db
def test_archetype_detail_all_time_pagination_and_zero_timeframe(client):
    from core.models.tournament import Tournament

    # Create an archetype with finishes from 2024 (outside 90d window)
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
    assert "?page=2&amp;days=90" in content or "?page=2&days=90" in content


@pytest.mark.django_db
def test_cards_view(client):
    from django.core.cache import cache

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
    import warnings

    from core.engine.knn import set_global_knn_index

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
    from core.engine.knn import set_global_knn_index

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
    from unittest.mock import MagicMock
    from core.engine.knn import set_global_knn_index
    from core.models.tournament import Tournament

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
        assert "gatherer.wizards.com/Pages/Card/Details.aspx" in first_card["gatherer_url"]

        content = response.content.decode()
        assert first_card["scryfall_url"] in content
        assert first_card["gatherer_url"] in content
        assert "Gatherer" in content
        assert "Scryfall" in content

        # Verify mana icons render when cards have a mana cost
        cards_with_cost = [c for c in cards if c.get("mana_cost")]
        if cards_with_cost:
            assert "ms ms-" in content


@pytest.mark.django_db
def test_mdfc_mana_cost_display(client):
    from core.pipeline.scryfall import _extract_mana_cost

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
    from core.models import Card
    from django.core.cache import cache
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
    from core.models.deck import Deck
    from core.models.tournament import Tournament

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
    assert 'data-timeframe-toggle' not in header_html

    # But sidebar aside still contains the timeframe switcher
    aside_start = content.find("<aside")
    aside_end = content.find("</aside>")
    assert aside_start != -1 and aside_end != -1
    aside_html = content[aside_start:aside_end]
    assert 'data-timeframe-toggle="30"' in aside_html
    assert 'data-timeframe-toggle="90"' in aside_html


@pytest.mark.django_db
def test_active_formats_hidden_from_navigation_and_homepage(client):
    """Only Vintage and Legacy should appear in the left navigation and homepage format cards."""
    response = client.get("/")
    assert response.status_code == 200

    # Homepage format cards
    format_slugs = [item["format_slug"] for item in response.context["format_metas"]]
    assert format_slugs == ["vintage", "legacy"]

    # Navigation sidebar items
    nav_formats = [fmt["slug"] for fmt in response.context["MODOMETA_FORMATS"]]
    assert nav_formats == ["vintage", "legacy"]

    # Verify HTML sidebar only lists Vintage and Legacy
    content = response.content.decode("utf-8")
    aside_start = content.find("<aside")
    aside_end = content.find("</aside>")
    sidebar_html = content[aside_start:aside_end]

    assert 'href="/vintage/?days=90"' in sidebar_html
    assert 'href="/legacy/?days=90"' in sidebar_html
    assert 'href="/modern/?days=90"' not in sidebar_html
    assert 'href="/pioneer/?days=90"' not in sidebar_html
    assert 'href="/standard/?days=90"' not in sidebar_html
    assert 'href="/pauper/?days=90"' not in sidebar_html
    assert 'href="/premodern/?days=90"' not in sidebar_html


@pytest.mark.django_db
def test_hidden_formats_still_accessible_via_direct_url_for_search(client):
    """Formats outside Vintage and Legacy are accessible directly (e.g. from search results)."""
    # Modern format overview should return 200 OK (not 404)
    response = client.get("/modern/")
    assert response.status_code == 200
    assert b"Modern Metagame" in response.content

    # The sidebar navigation on the Modern page still only displays Vintage and Legacy
    nav_formats = [fmt["slug"] for fmt in response.context["MODOMETA_FORMATS"]]
    assert nav_formats == ["vintage", "legacy"]





