"""Tests for card_tags template tag library."""

from django.template import Context
from django.template import Template

from core.templatetags.card_tags import gatherer_url_filter
from core.templatetags.card_tags import scryfall_url_filter


def test_scryfall_url_filter():
    url = scryfall_url_filter("Lightning Bolt")
    assert url == "https://scryfall.com/search?q=%21%22Lightning+Bolt%22"

    template = Template("{% load card_tags %}{{ name|scryfall_url }}")
    rendered = template.render(Context({"name": "Brainstorm"}))
    assert rendered == "https://scryfall.com/search?q=%21%22Brainstorm%22"


def test_gatherer_url_filter():
    url = gatherer_url_filter("Fire // Ice")
    assert url == "https://gatherer.wizards.com/Pages/Card/Details.aspx?name=Fire"

    template = Template("{% load card_tags %}{{ name|gatherer_url }}")
    rendered = template.render(Context({"name": "Phelia, Exuberant Shepherd"}))
    assert rendered == "https://gatherer.wizards.com/Pages/Card/Details.aspx?name=Phelia%2C+Exuberant+Shepherd"
