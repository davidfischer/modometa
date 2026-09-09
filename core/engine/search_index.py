"""Engine for generating the precomputed search index."""

from collections.abc import Iterable

from django.db.models import Count

from core.models.deck import Deck


def build_search_index_data(formats: Iterable[str] | None = None) -> dict:
    """Build the dictionary of archetypes and players for search autocomplete.

    If formats is specified, restricts decks to those formats.
    Otherwise, builds across all formats in the database.
    """
    arch_qs = Deck.objects.all()
    player_qs = Deck.objects.all()

    if formats:
        arch_qs = arch_qs.filter(format__in=formats)
        player_qs = player_qs.filter(format__in=formats)

    arch_agg = (
        arch_qs.order_by()
        .values("format", "archetype", "archetype_slug")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    archetypes = [
        {
            "name": a["archetype"],
            "slug": a["archetype_slug"],
            "format": a["format"],
            "count": a["count"],
        }
        for a in arch_agg
        if a["archetype"] and a["archetype_slug"]
    ]

    player_agg = (
        player_qs.order_by()
        .values("player")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    players = [
        {"name": p["player"], "count": p["count"]} for p in player_agg if p["player"]
    ]

    return {
        "archetypes": archetypes,
        "players": players,
    }
