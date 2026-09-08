"""Template tags and filters for card links and utilities."""

from django import template

from core.models.card import get_gatherer_url
from core.models.card import get_scryfall_url


register = template.Library()


@register.filter(name="scryfall_url")
def scryfall_url_filter(card_name_or_id: str | None) -> str:
    """Return Scryfall URL for a card name or Scryfall ID.

    Usage in templates:
        {{ card_name|scryfall_url }}
    """
    return get_scryfall_url(str(card_name_or_id) if card_name_or_id else None)


@register.filter(name="gatherer_url")
def gatherer_url_filter(card_name: str | None) -> str:
    """Return Gatherer URL for a card name.

    Usage in templates:
        {{ card_name|gatherer_url }}
    """
    return get_gatherer_url(str(card_name) if card_name else None)
