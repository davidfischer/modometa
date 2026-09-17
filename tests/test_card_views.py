"""Integration tests for card detail view and card search functionality."""

from datetime import date
from datetime import timedelta
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.test import Client

from core.engine.search_index import build_search_index_data
from core.models import Card
from core.models import CardLookup
from core.models import Deck
from core.models import Tournament
from core.templatetags.mana_tags import oracle_text_format_filter
from core.views import classify_card_type
from core.views import get_cards_map


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def sample_card(db):
    card, _ = Card.objects.get_or_create(
        id="test-card-uuid-1234",
        defaults={
            "oracle_id": "test-oracle-uuid-1234",
            "name": "Lightning Bolt",
            "slug": "lightning-bolt",
            "normalized_name": "lightning bolt",
            "mana_cost": "{R}",
            "cmc": 1.0,
            "type_line": "Instant",
            "oracle_text": "Lightning Bolt deals 3 damage to any target.",
            "colors": ["R"],
            "color_identity": ["R"],
            "image_uri": "https://cards.scryfall.io/normal/test.jpg",
            "printings": [
                {
                    "id": "test-card-uuid-1234",
                    "set": "lea",
                    "set_name": "Limited Edition Alpha",
                    "collector_number": "161",
                    "released_at": "1993-08-05",
                    "rarity": "common",
                    "image_uri": "https://cards.scryfall.io/normal/test.jpg",
                    "tcgplayer_id": 1187,
                    "cardmarket_id": 5000,
                    "mtgo_id": 100,
                    "price_usd": "500.00",
                    "price_eur": "450.00",
                    "price_tix": "1.50",
                }
            ],
            "legalities": {
                "modern": "legal",
                "legacy": "legal",
                "vintage": "legal",
                "pauper": "legal",
                "standard": "not_legal",
                "commander": "legal",
            },
        },
    )
    # Populate the test OG image data URI in cache for tests rendering card OG images
    cache_key = f"card_og_img_data_uri:{card.id}"
    cache.set(
        cache_key,
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    )
    return card


@pytest.fixture
def sample_split_card(db):
    card, _ = Card.objects.get_or_create(
        id="test-card-uuid-split",
        defaults={
            "oracle_id": "test-oracle-uuid-split",
            "name": "Fire // Ice",
            "slug": "fire-ice",
            "normalized_name": "fire // ice",
            "mana_cost": "{1}{R} // {1}{U}",
            "cmc": 4.0,
            "type_line": "Instant // Instant",
            "oracle_text": "Fire deals 2 damage divided as you choose among one or two targets.\n//\nTap target permanent. Draw a card.",
            "colors": ["R", "U"],
            "color_identity": ["R", "U"],
            "image_uri": "https://cards.scryfall.io/normal/split.jpg",
            "printings": [
                {
                    "id": "test-card-uuid-split",
                    "set": "apc",
                    "set_name": "Apocalypse",
                    "collector_number": "128",
                    "released_at": "2001-06-04",
                    "rarity": "uncommon",
                    "image_uri": "https://cards.scryfall.io/normal/split.jpg",
                    "tcgplayer_id": 7921,
                    "cardmarket_id": 3173,
                    "mtgo_id": 16000,
                    "price_usd": "0.50",
                    "price_eur": "0.40",
                    "price_tix": "0.03",
                }
            ],
            "legalities": {
                "modern": "legal",
                "legacy": "legal",
                "vintage": "legal",
                "pauper": "not_legal",
                "standard": "not_legal",
            },
        },
    )
    return card


@pytest.fixture
def sample_deck_with_card(db, sample_card):
    tournament = Tournament.objects.create(
        id="test-tourney-card-1",
        format="modern",
        name="Modern Challenge 32",
        event_type="challenge",
        date="2026-03-01",
    )
    deck = Deck.objects.create(
        id="test-deck-card-1",
        tournament=tournament,
        format="modern",
        player="BoltMaster",
        player_lower="boltmaster",
        result="1st Place",
        rank=1,
        is_top8=True,
        archetype="Burn",
        archetype_slug="burn",
        colors="R",
        color_name="Mono-Red",
        mainboard=[
            {"card": "Lightning Bolt", "count": 4},
            {"card": "Mountain", "count": 20},
        ],
        sideboard=[
            {"card": "Smash to Smithereens", "count": 4},
        ],
    )
    return deck


@pytest.mark.django_db
def test_card_get_absolute_url(sample_card):
    url = sample_card.get_absolute_url()
    assert url == "/card/lightning-bolt/"


@pytest.mark.django_db
def test_card_detail_success(client, sample_card, sample_deck_with_card):
    response = client.get(f"/card/{sample_card.slug}/")
    assert response.status_code == 200
    assert "card" in response.context
    assert response.context["card"].name == "Lightning Bolt"
    assert "printings" in response.context
    assert len(response.context["printings"]) >= 1
    assert "format_decks" in response.context
    assert "legalities_display" in response.context

    # Verify pricing is present in printings list
    assert b"Printings" in response.content
    assert b"500.00" in response.content
    assert b"1.50" in response.content

    # Verify decks are present for legal format
    modern_group = next(
        (
            fd
            for fd in response.context["format_decks"]
            if fd["format_slug"] == "modern"
        ),
        None,
    )
    assert modern_group is not None
    assert modern_group["count"] >= 1
    assert b"BoltMaster" in response.content
    assert b"Scryfall" in response.content
    assert b"Gatherer" in response.content
    assert b"GoatBots" in response.content
    assert b"TCGPlayer" in response.content


@pytest.mark.django_db
def test_card_detail_printings_limit_and_empty(client):
    # Card with empty printings has no synthesized "Original Printing"
    card_empty = Card.objects.create(
        id="card-empty-test",
        oracle_id="oracle-empty-test",
        name="Empty Card",
        slug="empty-card",
        printings=[],
    )
    resp_empty = client.get(f"/card/{card_empty.slug}/")
    assert resp_empty.status_code == 200
    assert resp_empty.context["printings"] == []
    assert b"Original Printing" not in resp_empty.content
    assert b"No printings recorded" in resp_empty.content

    # Card with 35 printings is capped at top 25
    many_printings = [
        {"id": f"p-{i}", "set": f"s{i}", "set_name": f"Set {i}", "price_usd": f"{i}.00"}
        for i in range(35)
    ]
    card_many = Card.objects.create(
        id="card-many-test",
        oracle_id="oracle-many-test",
        name="Many Printings Card",
        slug="many-printings-card",
        printings=many_printings,
    )
    resp_many = client.get(f"/card/{card_many.slug}/")
    assert resp_many.status_code == 200
    assert len(resp_many.context["printings"]) == 25
    assert resp_many.context["printings"][0]["id"] == "p-0"
    assert resp_many.context["printings"][-1]["id"] == "p-24"


@pytest.mark.django_db
def test_card_detail_printing_buy_links(client):
    card = Card.objects.create(
        id="card-buy-links-test",
        oracle_id="oracle-buy-links-test",
        name="Counterspell",
        slug="counterspell",
        printings=[
            {
                "id": "p-paper-only",
                "set": "lea",
                "set_name": "Limited Edition Alpha",
                "collector_number": "54",
                "tcgplayer_id": 1100,
                "cardmarket_id": 4200,
                "price_usd": "200.00",
            },
            {
                "id": "p-mtgo-only",
                "set": "vma",
                "set_name": "Vintage Masters",
                "collector_number": "64",
                "mtgo_id": 53000,
                "price_tix": "1.25",
            },
            {
                "id": "p-all",
                "set": "mh2",
                "set_name": "Modern Horizons 2",
                "collector_number": "267",
                "tcgplayer_id": 239000,
                "cardmarket_id": 560000,
                "mtgo_id": 90000,
                "price_usd": "1.50",
                "price_tix": "0.10",
            },
        ],
    )
    resp = client.get(f"/card/{card.slug}/")
    assert resp.status_code == 200
    printings = resp.context["printings"]
    assert len(printings) == 3

    # Paper with TCGPlayer and Cardmarket, no MTGO
    p0 = printings[0]
    assert p0["tcgplayer_url"] == "https://www.tcgplayer.com/product/1100"
    assert (
        p0["cardmarket_url"]
        == "https://www.cardmarket.com/en/Magic/Products?idProduct=4200"
    )
    assert p0["cardhoarder_url"] is None

    # MTGO only printing
    p1 = printings[1]
    assert p1["cardhoarder_url"] == "https://www.cardhoarder.com/cards/53000"
    assert p1["tcgplayer_url"] is None
    assert p1["cardmarket_url"] is None

    # All three IDs present
    p2 = printings[2]
    assert p2["tcgplayer_url"] == "https://www.tcgplayer.com/product/239000"
    assert (
        p2["cardmarket_url"]
        == "https://www.cardmarket.com/en/Magic/Products?idProduct=560000"
    )
    assert p2["cardhoarder_url"] == "https://www.cardhoarder.com/cards/90000"

    assert b"Buy on TCGPlayer" in resp.content
    assert b"Buy on Cardmarket" in resp.content
    assert b"Buy on Cardhoarder" in resp.content


@pytest.mark.django_db
def test_card_detail_split_card(client, sample_split_card):
    response = client.get(f"/card/{sample_split_card.slug}/")
    assert response.status_code == 200
    assert response.context["card"].name == "Fire // Ice"
    assert b"Fire // Ice" in response.content


@pytest.mark.django_db
def test_card_detail_404(client):
    response = client.get("/card/completely-nonexistent-mtg-card-9999/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_build_search_index_includes_cards(sample_card):
    data = build_search_index_data(formats=["modern"])
    assert "cards" in data
    assert any(
        c["name"] == "Lightning Bolt" and c["slug"] == "lightning-bolt"
        for c in data["cards"]
    )


@pytest.mark.django_db
def test_search_index_api_includes_cards(client, sample_card):
    response = client.get("/api/search-index/")
    assert response.status_code == 200
    data = response.json()
    assert "cards" in data
    assert any(
        c["name"] == "Lightning Bolt" and c["slug"] == "lightning-bolt"
        for c in data["cards"]
    )


@pytest.mark.django_db
def test_oracle_text_format_filter():
    raw_text = "{T}: Add {R}.\nLightning Bolt deals 3 damage."
    formatted = oracle_text_format_filter(raw_text)
    assert 'class="ms ms-tap' in formatted
    assert 'class="ms ms-r' in formatted
    assert "<p " in formatted


@pytest.mark.django_db
def test_cards_list_links_to_card_detail(client, sample_deck_with_card):
    response = client.get("/modern/cards/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "/card/lightning-bolt/" in content


@pytest.mark.django_db
def test_deck_detail_links_to_card_detail(client, sample_deck_with_card):
    d = sample_deck_with_card
    response = client.get(f"/player/{d.player}/deck/{d.tournament_id}/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "/card/lightning-bolt/" in content


@pytest.mark.django_db
def test_archetype_detail_links_to_card_detail(client, sample_deck_with_card):
    d = sample_deck_with_card
    response = client.get(f"/modern/archetype/{d.archetype_slug}/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "/card/lightning-bolt/" in content


@pytest.mark.django_db
def test_format_overview_links_to_card_detail(client, sample_deck_with_card):
    response = client.get("/modern/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "/card/lightning-bolt/" in content


@pytest.mark.django_db
def test_card_og_image(client, sample_card):
    # Cache was populated by sample_card fixture
    cache_key = f"card_og_img_data_uri:{sample_card.id}"
    assert cache.get(cache_key) is not None

    response = client.get(f"/card/{sample_card.name}/og.png")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.django_db
def test_card_og_image_fetches_and_caches_remote_image(client, sample_card):
    cache_key = f"card_og_img_data_uri:{sample_card.id}"
    cache.delete(cache_key)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "image/jpeg"}
    mock_resp.content = b"fake-jpeg-image-bytes"

    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.get.return_value = mock_resp

        response = client.get(f"/card/{sample_card.name}/og.png")
        assert response.status_code == 200
        assert response.headers["Content-Type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

        # Verify outbound call used settings.USER_AGENT
        mock_client.get.assert_called_once_with(
            sample_card.image_uri,
            headers={"User-Agent": settings.USER_AGENT},
        )

        # Verify image was populated into the cache
        cached_uri = cache.get(cache_key)
        assert cached_uri is not None
        assert cached_uri.startswith("data:image/jpeg;base64,")


@pytest.mark.django_db
def test_card_og_image_network_failure_renders_placeholder(client, sample_card):
    cache_key = f"card_og_img_data_uri:{sample_card.id}"
    cache.delete(cache_key)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.get.side_effect = Exception("Network unreachable")

        response = client.get(f"/card/{sample_card.name}/og.png")
        assert response.status_code == 200
        assert response.headers["Content-Type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.django_db
def test_card_og_image_404(client):
    response = client.get("/card/Nonexistent%20Card%209999/og.png")
    assert response.status_code == 404


@pytest.mark.django_db
def test_card_detail_og_image_tags_and_wording(client, sample_card):
    response = client.get(f"/card/{sample_card.slug}/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    # Verify OG image points to card_og_image URL
    assert (
        "/card/Lightning%20Bolt/og.png" in content
        or "/card/Lightning Bolt/og.png" in content
    )
    # Verify wording uses 'with' instead of 'containing'
    assert f"View competitive MTGO decklists with {sample_card.name}" in content
    assert "containing" not in content


@pytest.mark.django_db
def test_card_detail_and_og_exclude_commander(client, sample_card):
    # sample_card fixture has 'commander': 'legal' in legalities
    assert sample_card.legalities.get("commander") == "legal"

    # Verify card detail page excludes Commander
    response = client.get(f"/card/{sample_card.slug}/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Commander" not in content
    assert "Modern" in content
    assert "Legacy" in content
    assert "Vintage" in content

    # Verify OG image succeeds and uses supported formats
    og_response = client.get(f"/card/{sample_card.name}/og.png")
    assert og_response.status_code == 200
    assert og_response.headers["Content-Type"] == "image/png"


@pytest.mark.django_db
def test_card_og_image_top_format_and_deck_count(client, sample_card):
    # 1. No decks: total_decks is "0", top_format_display defaults to first legal format
    with patch("core.views.render_og_png") as mock_render:
        mock_render.return_value = HttpResponse(b"dummy-png", content_type="image/png")
        client.get(f"/card/{sample_card.name}/og.png")
        ctx = mock_render.call_args[0][1]
        assert ctx["total_decks"] == "0"
        # When no decks exist in the last year, top_format_display defaults to "-"
        assert ctx["top_format_display"] == "-"

    # 2. Add tournaments and decks
    t_legacy = Tournament.objects.create(
        id="tourn-leg-1",
        name="Legacy Challenge",
        format="legacy",
        date=date.today(),
        event_type="challenge",
    )
    t_modern = Tournament.objects.create(
        id="tourn-mod-1",
        name="Modern Challenge",
        format="modern",
        date=date.today(),
        event_type="challenge",
    )
    t_old = Tournament.objects.create(
        id="tourn-old-1",
        name="Old Legacy Challenge",
        format="legacy",
        date=date.today() - timedelta(days=400),
        event_type="challenge",
    )

    bolt_mb = [{"card": "Lightning Bolt", "count": 4}]

    # 3 Legacy decks in current year
    for i in range(3):
        Deck.objects.create(
            id=f"deck-leg-{i}",
            tournament=t_legacy,
            format="legacy",
            player=f"PlayerLeg{i}",
            player_lower=f"playerleg{i}",
            result="1st",
            colors="R",
            color_name="Mono-Red",
            mainboard=bolt_mb,
            sideboard=[],
        )

    # 1 Modern deck in current year
    Deck.objects.create(
        id="deck-mod-1",
        tournament=t_modern,
        format="modern",
        player="PlayerMod1",
        player_lower="playermod1",
        result="1st",
        colors="R",
        color_name="Mono-Red",
        mainboard=bolt_mb,
        sideboard=[],
    )

    # 1 Legacy deck older than 365 days (should be excluded)
    Deck.objects.create(
        id="deck-old-1",
        tournament=t_old,
        format="legacy",
        player="PlayerOld1",
        player_lower="playerold1",
        result="1st",
        colors="R",
        color_name="Mono-Red",
        mainboard=bolt_mb,
        sideboard=[],
    )

    # Legacy has 3 decks vs Modern 1 deck -> top format is Legacy, total is 4
    with patch("core.views.render_og_png") as mock_render:
        mock_render.return_value = HttpResponse(b"dummy-png", content_type="image/png")
        client.get(f"/card/{sample_card.name}/og.png")
        ctx = mock_render.call_args[0][1]
        assert ctx["total_decks"] == "4"
        assert ctx["top_format_display"] == "Legacy"

    # Add 4 more Modern decks -> Modern now has 5 decks vs Legacy 3 decks
    for i in range(2, 6):
        Deck.objects.create(
            id=f"deck-mod-{i}",
            tournament=t_modern,
            format="modern",
            player=f"PlayerMod{i}",
            player_lower=f"playermod{i}",
            result="1st",
            colors="R",
            color_name="Mono-Red",
            mainboard=bolt_mb,
            sideboard=[],
        )

    with patch("core.views.render_og_png") as mock_render:
        mock_render.return_value = HttpResponse(b"dummy-png", content_type="image/png")
        client.get(f"/card/{sample_card.name}/og.png")
        ctx = mock_render.call_args[0][1]
        assert ctx["total_decks"] == "8"
        assert ctx["top_format_display"] == "Modern"


@pytest.mark.django_db
def test_search_index_includes_subnames():
    Card.objects.create(
        id="test-brazen-borrower",
        oracle_id="test-oracle-brazen",
        name="Brazen Borrower",
        slug="brazen-borrower",
        mana_cost="{1}{U}{U}",
        type_line="Creature — Faerie Rogue",
        legalities={"modern": "legal"},
        card_faces=[
            {
                "name": "Brazen Borrower",
                "mana_cost": "{1}{U}{U}",
                "type_line": "Creature — Faerie Rogue",
            },
            {
                "name": "Petty Theft",
                "mana_cost": "{1}{U}",
                "type_line": "Instant — Adventure",
            },
        ],
    )
    data = build_search_index_data(formats=["modern"])
    bb_entry = next((c for c in data["cards"] if c["name"] == "Brazen Borrower"), None)
    assert bb_entry is not None
    assert bb_entry["slug"] == "brazen-borrower"
    assert "subnames" in bb_entry
    assert "Petty Theft" in bb_entry["subnames"]


@pytest.mark.django_db
def test_card_detail_multi_face_rendering_and_flip_button(client):
    card = Card.objects.create(
        id="test-boggart-trawler",
        oracle_id="test-oracle-boggart",
        name="Boggart Trawler",
        slug="boggart-trawler",
        mana_cost="{2}{B}",
        type_line="Creature — Goblin Assassin",
        oracle_text="When Boggart Trawler enters, exile graveyard.\n//\nBoggart Bog enters tapped.",
        image_uri="https://cards.scryfall.io/front/trawler.jpg",
        legalities={"modern": "legal"},
        card_faces=[
            {
                "name": "Boggart Trawler",
                "mana_cost": "{2}{B}",
                "type_line": "Creature — Goblin Assassin",
                "oracle_text": "When Boggart Trawler enters, exile graveyard.",
                "power": "2",
                "toughness": "3",
                "image_uri": "https://cards.scryfall.io/front/trawler.jpg",
            },
            {
                "name": "Boggart Bog",
                "mana_cost": "",
                "type_line": "Land",
                "oracle_text": "Boggart Bog enters tapped. {T}: Add {B}.",
                "power": None,
                "toughness": None,
                "image_uri": "https://cards.scryfall.io/back/bog.jpg",
            },
        ],
    )
    response = client.get(f"/card/{card.slug}/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Front face title as H1
    assert "Boggart Trawler</h1>" in content
    # Card Faces section present
    assert "Card Faces</h2>" in content
    assert "Boggart Bog</h3>" in content
    assert "Creature — Goblin Assassin" in content
    assert "Land" in content
    assert "2/3" in content
    assert "When Boggart Trawler enters, exile graveyard." in content
    assert "Boggart Bog enters tapped." in content
    # Turn Over button present because back image exists
    assert "turn-over-card-btn" in content
    assert 'data-back-src="https://cards.scryfall.io/back/bog.jpg"' in content


@pytest.mark.django_db
def test_card_detail_prepare_rendering(client):
    card = Card.objects.create(
        id="test-emeritus-woe",
        oracle_id="test-oracle-emeritus-woe",
        name="Emeritus of Woe",
        slug="emeritus-of-woe",
        mana_cost="{3}{B}",
        type_line="Creature — Vampire Warlock",
        oracle_text="This creature enters prepared.\n//\nSearch your library for a card.",
        image_uri="https://cards.scryfall.io/front/emeritus.jpg",
        legalities={"legacy": "legal"},
        card_faces=[
            {
                "name": "Emeritus of Woe",
                "mana_cost": "{3}{B}",
                "type_line": "Creature — Vampire Warlock",
                "oracle_text": "This creature enters prepared.",
                "power": "5",
                "toughness": "4",
                "image_uri": None,
            },
            {
                "name": "Demonic Tutor",
                "mana_cost": "{1}{B}",
                "type_line": "Sorcery",
                "oracle_text": "Search your library for a card.",
                "power": None,
                "toughness": None,
                "image_uri": None,
            },
        ],
    )
    response = client.get(f"/card/{card.slug}/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    assert "Emeritus of Woe</h1>" in content
    assert "Demonic Tutor</h3>" in content
    assert "5/4" in content
    assert "This creature enters prepared." in content
    assert "Search your library for a card." in content
    # No Turn Over button since prepare card is single-sided
    assert "turn-over-card-btn" not in content


@pytest.mark.django_db
def test_get_cards_map_prioritizes_playable_cards_over_art_series():
    real_brainstorm = Card.objects.create(
        id="brainstorm-real",
        oracle_id="brainstorm-oracle",
        name="Brainstorm",
        slug="brainstorm",
        normalized_name="brainstorm",
        mana_cost="{U}",
        cmc=1.0,
        type_line="Instant",
        oracle_text="Draw three cards, then put two cards from your hand on top of your library in any order.",
        legalities={"legacy": "legal", "vintage": "restricted"},
    )
    art_brainstorm = Card.objects.create(
        id="brainstorm-art",
        oracle_id="brainstorm-art-oracle",
        name="Brainstorm",
        slug="brainstorm-1",
        normalized_name="brainstorm",
        type_line="Card",
        legalities={},
    )
    CardLookup.objects.create(
        lookup_name="brainstorm",
        canonical_name="Brainstorm",
        card=real_brainstorm,
        priority=110,
    )
    CardLookup.objects.create(
        lookup_name="brainstorm-art",
        canonical_name="Brainstorm",
        card=art_brainstorm,
        priority=0,
    )

    cards_map = get_cards_map(["Brainstorm"])
    assert cards_map["Brainstorm"].id == real_brainstorm.id
    assert cards_map["Brainstorm"].type_line == "Instant"
    assert classify_card_type(cards_map["Brainstorm"]) == "instant"


@pytest.mark.django_db
def test_get_cards_map_tiebreaker_without_lookup():
    real_card = Card.objects.create(
        id="mana-drain-real",
        oracle_id="mana-drain-oracle",
        name="Mana Drain",
        slug="mana-drain",
        normalized_name="mana drain",
        type_line="Instant",
    )
    Card.objects.create(
        id="mana-drain-art",
        oracle_id="mana-drain-art-oracle",
        name="Mana Drain",
        slug="mana-drain-1",
        normalized_name="mana drain",
        type_line="Card",
    )

    cards_map = get_cards_map(["Mana Drain"])
    assert cards_map["Mana Drain"].id == real_card.id
    assert cards_map["Mana Drain"].type_line == "Instant"
    assert classify_card_type(cards_map["Mana Drain"]) == "instant"
