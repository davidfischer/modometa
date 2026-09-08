from datetime import date

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from core.admin import CardAdmin
from core.admin import DeckAdmin
from core.admin import TimeframeFilter
from core.admin import TournamentAdmin
from core.decklist import parse_text_decklist
from core.models import Card
from core.models import Deck
from core.models import Tournament
from core.models import get_gatherer_url
from core.models import get_scryfall_url


def test_get_scryfall_url():
    # By explicit card_id
    url_id = get_scryfall_url(card_id="55707746-da6e-46e5-a5ca-7ac843fdc38e")
    assert url_id == "https://scryfall.com/card/55707746-da6e-46e5-a5ca-7ac843fdc38e"

    # By card name
    url_name = get_scryfall_url("Lightning Bolt")
    assert url_name == "https://scryfall.com/search?q=%21%22Lightning+Bolt%22"

    # Positional UUID string
    url_uuid = get_scryfall_url("55707746-da6e-46e5-a5ca-7ac843fdc38e")
    assert url_uuid == "https://scryfall.com/card/55707746-da6e-46e5-a5ca-7ac843fdc38e"

    # None / empty
    assert get_scryfall_url() == "https://scryfall.com/"


def test_get_gatherer_url():
    # Standard card
    url = get_gatherer_url("Phelia, Exuberant Shepherd")
    assert url == "https://gatherer.wizards.com/Pages/Card/Details.aspx?name=Phelia%2C+Exuberant+Shepherd"

    # Split card uses the front face name
    url_split = get_gatherer_url("Fire // Ice")
    assert url_split == "https://gatherer.wizards.com/Pages/Card/Details.aspx?name=Fire"

    # Adventure card uses the front face name
    url_adv = get_gatherer_url("Brazen Borrower // Petty Theft")
    assert url_adv == "https://gatherer.wizards.com/Pages/Card/Details.aspx?name=Brazen+Borrower"

    # Empty / none
    assert get_gatherer_url("") == "https://gatherer.wizards.com/"
    assert get_gatherer_url(None) == "https://gatherer.wizards.com/"


def test_card_model_helpers():
    card = Card(
        id="55707746-da6e-46e5-a5ca-7ac843fdc38e",
        oracle_id="b71f005a-2eec-4a92-959a-5f50fa540026",
        name="Phelia, Exuberant Shepherd",
    )
    assert card.scryfall_url == "https://scryfall.com/card/55707746-da6e-46e5-a5ca-7ac843fdc38e"
    assert card.get_scryfall_url() == "https://scryfall.com/card/55707746-da6e-46e5-a5ca-7ac843fdc38e"
    assert card.gatherer_url == "https://gatherer.wizards.com/Pages/Card/Details.aspx?name=Phelia%2C+Exuberant+Shepherd"
    assert card.get_gatherer_url() == "https://gatherer.wizards.com/Pages/Card/Details.aspx?name=Phelia%2C+Exuberant+Shepherd"


def test_card_verbose_names():
    assert Card._meta.get_field("id").verbose_name == "ID"
    assert Card._meta.get_field("oracle_id").verbose_name == "Oracle ID"
    assert Card._meta.get_field("cmc").verbose_name == "CMC"
    assert Card._meta.get_field("image_uri").verbose_name == "Image URI"


def test_card_admin_external_links():
    site = AdminSite()
    admin = CardAdmin(Card, site)

    # Check that external_links is in readonly_fields and at the end of get_fields
    assert "external_links" in admin.readonly_fields
    fields = admin.get_fields(None)
    assert fields[-1] == "external_links"

    card = Card(
        id="55707746-da6e-46e5-a5ca-7ac843fdc38e",
        oracle_id="b71f005a-2eec-4a92-959a-5f50fa540026",
        name="Phelia, Exuberant Shepherd",
    )
    html = str(admin.external_links(card))
    assert 'href="https://scryfall.com/card/55707746-da6e-46e5-a5ca-7ac843fdc38e"' in html
    assert 'href="https://gatherer.wizards.com/Pages/Card/Details.aspx?name=Phelia%2C+Exuberant+Shepherd"' in html
    assert "Scryfall" in html
    assert "Gatherer" in html
    assert 'target="_blank"' in html

    # Empty card returns "-"
    assert admin.external_links(None) == "-"
    assert admin.external_links(Card()) == "-"


def test_deck_verbose_names_and_help_text():
    assert Deck._meta.get_field("id").verbose_name == "ID"
    assert Deck._meta.get_field("is_5_0").verbose_name == "is 5-0"
    assert Deck._meta.get_field("anchor_uri").verbose_name == "Anchor URI"
    help_text = Deck._meta.get_field("is_auto_classified").help_text
    assert "heuristic" in help_text.lower()
    assert "yaml" in help_text.lower()

    assert Tournament._meta.get_field("id").verbose_name == "ID"
    assert Tournament._meta.get_field("uri").verbose_name == "URI"



def test_admin_ordering_and_hierarchy():
    site = AdminSite()
    deck_admin = DeckAdmin(Deck, site)
    tourn_admin = TournamentAdmin(Tournament, site)

    assert deck_admin.ordering == ("-tournament__date", "rank", "id")
    assert deck_admin.date_hierarchy == "tournament__date"

    assert tourn_admin.ordering == ("-date", "id")
    assert tourn_admin.date_hierarchy == "date"
    assert TimeframeFilter in deck_admin.list_filter
    assert TimeframeFilter in tourn_admin.list_filter


@pytest.mark.django_db
def test_admin_timeframe_filter():
    site = AdminSite()
    deck_admin = DeckAdmin(Deck, site)
    tourn_admin = TournamentAdmin(Tournament, site)
    factory = RequestFactory()

    tf_filter = TimeframeFilter(factory.get("/admin/core/deck/"), {}, Deck, deck_admin)
    assert tf_filter.lookups(None, deck_admin) == (
        ("30", "Last 30 days"),
        ("90", "Last 90 days"),
    )

    # Filtering Deck by 30 days
    req_30 = factory.get("/admin/core/deck/?timeframe=30")
    filter_30 = TimeframeFilter(req_30, {"timeframe": "30"}, Deck, deck_admin)
    qs_30 = filter_30.queryset(req_30, Deck.objects.all())
    assert qs_30 is not None

    # Filtering Tournament by 90 days
    req_90 = factory.get("/admin/core/tournament/?timeframe=90")
    filter_90 = TimeframeFilter(req_90, {"timeframe": "90"}, Tournament, tourn_admin)
    qs_90 = filter_90.queryset(req_90, Tournament.objects.all())
    assert qs_90 is not None

    # None / empty returns unfiltered queryset
    req_none = factory.get("/admin/core/deck/")
    filter_none = TimeframeFilter(req_none, {}, Deck, deck_admin)
    assert filter_none.queryset(req_none, Deck.objects.all()).count() == Deck.objects.count()


def test_deck_decklist_text_and_admin_display():
    deck = Deck(
        id="test-deck-1",
        player="Ark4n",
        archetype="Dimir Tempo",
        format="legacy",
        mainboard=[
            {"card": "Brainstorm", "count": 4},
            {"card": "Force of Will", "count": 4},
        ],
        sideboard=[
            {"card": "Surgical Extraction", "count": 2},
        ],
    )

    expected_text = (
        "4 Brainstorm\n"
        "4 Force of Will\n\n"
        "// Sideboard\n"
        "2 Surgical Extraction"
    )
    assert deck.decklist_text == expected_text
    assert deck.to_text() == expected_text

    # Verify round-trip parsing
    mb, sb = parse_text_decklist(deck.decklist_text)
    assert mb == [{"card": "Brainstorm", "count": 4}, {"card": "Force of Will", "count": 4}]
    assert sb == [{"card": "Surgical Extraction", "count": 2}]

    # Test DeckAdmin readonly field
    site = AdminSite()
    admin = DeckAdmin(Deck, site)
    assert "decklist" in admin.readonly_fields
    fields = admin.get_fields(None)
    assert fields[-1] == "decklist"

    rendered = str(admin.decklist(deck))
    assert "<pre" in rendered
    assert "4 Brainstorm" in rendered
    assert "4 Force of Will" in rendered
    assert "// Sideboard" in rendered
    assert "2 Surgical Extraction" in rendered

    # Empty deck returns "-"
    empty_deck = Deck()
    assert admin.decklist(None) == "-"
    assert admin.decklist(empty_deck) == "-"


@pytest.mark.django_db
def test_tournament_get_absolute_url_and_admin(admin_client):
    tourn = Tournament.objects.create(
        id="modern-preliminary-2024-01-01",
        name="Modern Preliminary",
        format="modern",
        date=date(2024, 1, 1),
    )
    expected_url = "/modern/tournaments/modern-preliminary-2024-01-01/"
    assert tourn.get_absolute_url() == expected_url

    # Missing format or id returns empty string
    assert Tournament().get_absolute_url() == ""
    assert Tournament(format="modern").get_absolute_url() == ""

    # Django's built-in "View on site" returns the /admin/r/<ct>/<id>/ redirect URL
    site = AdminSite()
    admin = TournamentAdmin(Tournament, site)
    view_on_site_url = admin.get_view_on_site_url(tourn)
    assert view_on_site_url is not None
    assert "/admin/r/" in view_on_site_url
    resp = admin_client.get(view_on_site_url)
    assert resp.status_code == 302
    assert resp.url == f"http://testserver{expected_url}"


@pytest.mark.django_db
def test_deck_get_absolute_url_and_admin(admin_client):
    tourn = Tournament.objects.create(
        id="legacy-challenge-2024-05-10",
        name="Legacy Challenge",
        format="legacy",
        date=date(2024, 5, 10),
    )

    deck1 = Deck.objects.create(
        id="legacy-challenge-2024-05-10_ark4n_1",
        tournament=tourn,
        format="legacy",
        player="Ark4n",
        player_lower="ark4n",
        archetype="Dimir Tempo",
        archetype_slug="dimir-tempo",
    )

    # Single deck for player
    assert deck1.get_absolute_url() == "/player/Ark4n/deck/legacy-challenge-2024-05-10/"

    # Second deck for same player in same event
    deck2 = Deck.objects.create(
        id="legacy-challenge-2024-05-10_ark4n_2",
        tournament=tourn,
        format="legacy",
        player="Ark4n",
        player_lower="ark4n",
        archetype="Grixis Control",
        archetype_slug="grixis-control",
    )

    # Now deck1 is index 1 (default url) and deck2 is index 2 (disambiguated url)
    assert deck1.get_absolute_url() == "/player/Ark4n/deck/legacy-challenge-2024-05-10/"
    assert deck2.get_absolute_url() == "/player/Ark4n/deck/legacy-challenge-2024-05-10/2/"

    # Attribute deck_index overrides
    deck1.deck_index = 3
    assert deck1.get_absolute_url() == "/player/Ark4n/deck/legacy-challenge-2024-05-10/3/"

    # Unsaved or empty deck
    assert Deck().get_absolute_url() == ""

    # Django's built-in "View on site" returns the /admin/r/<ct>/<id>/ redirect URL
    deck1.deck_index = 1
    site = AdminSite()
    admin = DeckAdmin(Deck, site)
    view_on_site_url = admin.get_view_on_site_url(deck1)
    assert view_on_site_url is not None
    assert "/admin/r/" in view_on_site_url
    resp = admin_client.get(view_on_site_url)
    assert resp.status_code == 302
    assert resp.url == "http://testserver/player/Ark4n/deck/legacy-challenge-2024-05-10/"



