"""Views for Modometa metagame analyzer."""

import functools
from collections import Counter
from collections.abc import Iterable
from datetime import date
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count
from django.db.models import Max
from django.db.models import Q
from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.utils.cache import patch_cache_control

from core.engine.knn import get_global_knn_index
from core.formats import FORMATS
from core.models.card import Card
from core.models.card import CardLookup
from core.models.card import get_gatherer_url
from core.models.card import get_scryfall_url
from core.models.card import normalize_card_name
from core.models.deck import Deck
from core.models.tournament import Tournament


def public_cache(cdn_seconds: int = 3600, browser_seconds: int = 300):
    """Cache view output in LocMemCache and emit Cloudflare & browser caching headers.

    - Server: Caches full rendered response in LocMemCache (unless IS_TESTING is True).
    - Browser: Cache-Control: max-age=<browser_seconds>
    - CDN (Cloudflare): Cache-Control: s-maxage=<cdn_seconds> and Cloudflare-CDN-Cache-Control.
    """

    def decorator(view_func):
        @functools.wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if request.method != "GET":
                return view_func(request, *args, **kwargs)

            is_testing = getattr(settings, "IS_TESTING", False)
            cache_key = f"page_cache_v2:{request.get_full_path()}"

            if not is_testing:
                cached_response = cache.get(cache_key)
                if cached_response is not None:
                    return cached_response

            response = view_func(request, *args, **kwargs)

            if response.status_code == 200:
                patch_cache_control(
                    response,
                    public=True,
                    max_age=browser_seconds,
                    s_maxage=cdn_seconds,
                )
                response["Cloudflare-CDN-Cache-Control"] = f"max-age={cdn_seconds}"

                if not is_testing:
                    cache.set(cache_key, response, timeout=cdn_seconds)

            return response

        return _wrapped_view

    return decorator


def get_cards_map(card_names: Iterable[str]) -> dict[str, Card]:
    """Resolve a collection of card names to Card model instances.

    Tries exact name match first, then falls back to CardLookup (for DFCs,
    split cards, aliases), and finally to Card.normalized_name.
    Returns a dict mapping original card names to Card instances.
    """
    names_set = set(card_names)
    cards_map = {c.name: c for c in Card.objects.filter(name__in=names_set)}
    missing = [n for n in names_set if n not in cards_map]
    if missing:
        lookups = CardLookup.objects.filter(
            lookup_name__in=[normalize_card_name(n) for n in missing]
        ).select_related("card")
        lookup_dict = {cl.lookup_name: cl.card for cl in lookups if cl.card}
        for n in missing:
            norm = normalize_card_name(n)
            if norm in lookup_dict:
                cards_map[n] = lookup_dict[norm]

    still_missing = [n for n in names_set if n not in cards_map]
    if still_missing:
        norm_to_card = {
            c.normalized_name: c
            for c in Card.objects.filter(
                normalized_name__in=[normalize_card_name(n) for n in still_missing]
            )
        }
        for n in still_missing:
            norm = normalize_card_name(n)
            if norm in norm_to_card:
                cards_map[n] = norm_to_card[norm]

    return cards_map


def get_reference_date() -> date:
    """Get latest tournament date in database, or today if empty."""
    latest = Tournament.objects.aggregate(max_d=Max("date"))["max_d"]
    return latest or date.today()


def get_timeframe_cutoff(days: int = 90) -> tuple[date, date]:
    """Return (cutoff_date, reference_date)."""
    ref = get_reference_date()
    cutoff = ref - timedelta(days=days)
    return cutoff, ref


@public_cache(cdn_seconds=3600, browser_seconds=180)
def home(request):
    """Home view: Overview of active formats with top 3-5 archetypes each."""
    days = 30 if request.GET.get("days") == "30" else 90
    cutoff, ref_date = get_timeframe_cutoff(days)

    active_slugs = getattr(settings, "ACTIVE_FORMAT_SLUGS", settings.MODOMETA_FORMATS)
    format_metas = []
    for fmt_slug in active_slugs:
        fmt_name = (
            FORMATS[fmt_slug].name if fmt_slug in FORMATS else fmt_slug.capitalize()
        )
        # Query decks in window
        decks_qs = Deck.objects.filter(
            format=fmt_slug,
            tournament__date__gte=cutoff,
            tournament__date__lte=ref_date,
        )
        total_decks = decks_qs.count()

        # Count tournaments
        tourn_qs = Tournament.objects.filter(
            format=fmt_slug, date__gte=cutoff, date__lte=ref_date
        )
        challenge_count = tourn_qs.filter(event_type="challenge").count()
        league_count = tourn_qs.filter(event_type="league").count()
        total_top8_slots = max(1, challenge_count * 8)

        # Top archetypes
        arch_counts = (
            decks_qs.values("archetype", "archetype_slug")
            .annotate(
                count=Count("id"),
                top8_count=Count("id", filter=Q(is_top8=True)),
            )
            .order_by("-top8_count", "-count")[:5]
        )

        top_slugs = [a["archetype_slug"] for a in arch_counts]
        arch_colors = (
            decks_qs.filter(archetype_slug__in=top_slugs)
            .values("archetype_slug", "colors", "color_name")
            .annotate(cnt=Count("id"))
            .order_by("archetype_slug", "-cnt")
        )
        colors_map = {}
        for ac in arch_colors:
            slug = ac["archetype_slug"]
            if slug not in colors_map:
                colors_map[slug] = (ac["colors"], ac["color_name"])

        top_archetypes = []
        for a in arch_counts:
            top8_share = round((a["top8_count"] / total_top8_slots) * 100, 1)
            colors, color_name = colors_map.get(a["archetype_slug"], ("", ""))
            top_archetypes.append(
                {
                    "name": a["archetype"],
                    "slug": a["archetype_slug"],
                    "colors": colors,
                    "color_name": color_name,
                    "count": a["count"],
                    "top8_count": a["top8_count"],
                    "top8_share": top8_share,
                }
            )

        format_metas.append(
            {
                "format_slug": fmt_slug,
                "format_name": fmt_name,
                "total_decks": total_decks,
                "challenge_count": challenge_count,
                "league_count": league_count,
                "total_top8_slots": total_top8_slots,
                "top_archetypes": top_archetypes,
            }
        )

    recent_tournaments = Tournament.objects.filter(format__in=active_slugs).order_by(
        "-date", "-id"
    )[:10]

    return render(
        request,
        "home.html",
        {
            "format_metas": format_metas,
            "recent_tournaments": recent_tournaments,
        },
    )


def get_format_card_stats(fmt_slug: str, days: int = 90, active_type: str = "") -> dict:
    """Retrieve or compute cached card statistics for a format, timeframe, and event type."""
    cache_key = f"cards_list_v4:{fmt_slug}:{days}:{active_type or 'all'}"
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    cutoff, ref_date = get_timeframe_cutoff(days)
    decks_qs = Deck.objects.filter(
        format=fmt_slug,
        tournament__date__gte=cutoff,
        tournament__date__lte=ref_date,
    )
    if active_type:
        decks_qs = decks_qs.filter(tournament__event_type=active_type)

    decks = list(decks_qs.values("mainboard", "sideboard"))
    total_decks = len(decks)
    denom = max(1, total_decks)

    mb_counts = Counter()
    sb_counts = Counter()
    total_copies = Counter()
    any_counts = Counter()

    for d in decks:
        deck_mb = set()
        for item in d.get("mainboard", []):
            name = item.get("card", "")
            cnt = item.get("count", 1)
            if name:
                deck_mb.add(name)
                total_copies[name] += cnt
        for c in deck_mb:
            mb_counts[c] += 1
            any_counts[c] += 1

        deck_sb = set()
        for item in d.get("sideboard", []):
            name = item.get("card", "")
            cnt = item.get("count", 1)
            if name:
                deck_sb.add(name)
                total_copies[name] += cnt
        for c in deck_sb:
            sb_counts[c] += 1
            if c not in deck_mb:
                any_counts[c] += 1

    sorted_cards = any_counts.most_common()
    card_names = [c[0] for c in sorted_cards]
    cards_map = get_cards_map(card_names)

    card_rows = []
    for name, cnt in sorted_cards:
        card = cards_map.get(name)
        card_rows.append(
            {
                "name": name,
                "card": card,
                "mana_cost": card.mana_cost if card and card.mana_cost else "",
                "type_line": card.type_line if card else "Card",
                "image_uri": card.image_uri if card else None,
                "scryfall_url": card.scryfall_url
                if card
                else get_scryfall_url(name=name),
                "gatherer_url": card.gatherer_url if card else get_gatherer_url(name),
                "any_count": cnt,
                "mb_count": mb_counts[name],
                "sb_count": sb_counts[name],
                "adoption_pct": round((cnt / denom) * 100, 1),
                "mb_pct": round((mb_counts[name] / denom) * 100, 1),
                "sb_pct": round((sb_counts[name] / denom) * 100, 1),
                "avg_copies": round(total_copies[name] / max(1, cnt), 1),
            }
        )

    result = {"card_rows": card_rows, "total_decks": total_decks}
    cache.set(cache_key, result, timeout=3600)
    return result


@public_cache(cdn_seconds=3600, browser_seconds=180)
def format_overview(request, format):
    """Format overview view: Top archetypes, cards, recent leagues and challenges."""
    fmt_slug = format.lower().strip()
    if fmt_slug not in settings.MODOMETA_FORMATS:
        raise Http404("Format not supported")

    days = 30 if request.GET.get("days") == "30" else 90
    cutoff, ref_date = get_timeframe_cutoff(days)

    decks_qs = Deck.objects.filter(
        format=fmt_slug,
        tournament__date__gte=cutoff,
        tournament__date__lte=ref_date,
    )
    total_decks = decks_qs.count()

    tourns_qs = Tournament.objects.filter(
        format=fmt_slug, date__gte=cutoff, date__lte=ref_date
    )
    challenge_count = tourns_qs.filter(event_type="challenge").count()
    league_count = tourns_qs.filter(event_type="league").count()
    league_5_0_count = decks_qs.filter(
        tournament__event_type="league", is_5_0=True
    ).count()
    player_count = decks_qs.values("player_lower").distinct().count()

    raw_chall_decks = decks_qs.filter(tournament__event_type="challenge").count()
    total_chall_decks = max(1, raw_chall_decks)
    raw_top8_slots = challenge_count * 8
    total_top8_slots = max(1, raw_top8_slots)
    total_5_0s = max(1, league_5_0_count)

    # Aggregate Archetypes
    arch_aggregates = (
        decks_qs.values("archetype", "archetype_slug")
        .annotate(
            total_count=Count("id"),
            top8_count=Count("id", filter=Q(is_top8=True)),
            chall_appearances=Count("id", filter=Q(tournament__event_type="challenge")),
            league_5_0_count=Count(
                "id", filter=Q(tournament__event_type="league", is_5_0=True)
            ),
        )
        .order_by("-top8_count", "-total_count")
    )

    arch_colors = (
        decks_qs.values("archetype_slug", "colors", "color_name")
        .annotate(cnt=Count("id"))
        .order_by("archetype_slug", "-cnt")
    )
    colors_map = {}
    for ac in arch_colors:
        slug = ac["archetype_slug"]
        if slug not in colors_map:
            colors_map[slug] = (ac["colors"], ac["color_name"])

    archetypes = []
    for a in arch_aggregates:
        chall_share = round((a["chall_appearances"] / total_chall_decks) * 100, 1)
        top8_share = round((a["top8_count"] / total_top8_slots) * 100, 1)
        conv_rate = (
            round((a["top8_count"] / a["chall_appearances"]) * 100, 1)
            if a["chall_appearances"] > 0
            else 0.0
        )
        league_share = round((a["league_5_0_count"] / total_5_0s) * 100, 1)
        colors, color_name = colors_map.get(a["archetype_slug"], ("", ""))

        archetypes.append(
            {
                "name": a["archetype"],
                "slug": a["archetype_slug"],
                "colors": colors,
                "color_name": color_name,
                "total_count": a["total_count"],
                "top8_count": a["top8_count"],
                "chall_appearances": a["chall_appearances"],
                "league_5_0_count": a["league_5_0_count"],
                "challenge_share": chall_share,
                "top8_share": top8_share,
                "conversion_rate": conv_rate,
                "league_share": league_share,
            }
        )

    card_stats = get_format_card_stats(fmt_slug, days=days, active_type="")
    common_cards = card_stats["card_rows"][:10]

    recent_tournaments = Tournament.objects.filter(format=fmt_slug).order_by(
        "-date", "-id"
    )[:6]

    paginator = Paginator(archetypes, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "format_overview.html",
        {
            "format_slug": fmt_slug,
            "format_name": fmt_slug.capitalize(),
            "stats": {
                "total_decks": total_decks,
                "challenge_count": challenge_count,
                "league_count": league_count,
                "league_5_0_count": league_5_0_count,
                "player_count": player_count,
                "total_chall_decks": raw_chall_decks,
                "total_top8_slots": raw_top8_slots,
                "total_5_0s": league_5_0_count,
            },
            "archetypes": page_obj.object_list,
            "page_obj": page_obj,
            "common_cards": common_cards,
            "recent_tournaments": recent_tournaments,
        },
    )


@public_cache(cdn_seconds=3600, browser_seconds=180)
def tournament_list(request, format):
    """Tournament list view: Filterable by challenge or league."""
    fmt_slug = format.lower().strip()
    if fmt_slug not in settings.MODOMETA_FORMATS:
        raise Http404("Format not supported")

    active_type = request.GET.get("type", "").lower()
    qs = Tournament.objects.filter(format=fmt_slug)
    if active_type in ("challenge", "league", "preliminary"):
        qs = qs.filter(event_type=active_type)

    qs = qs.order_by("-date", "-id")
    paginator = Paginator(qs, 100)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "tournament_list.html",
        {
            "format_slug": fmt_slug,
            "format_name": fmt_slug.capitalize(),
            "active_type": active_type,
            "tournaments": page_obj.object_list,
            "page_obj": page_obj,
        },
    )


@public_cache(cdn_seconds=86400, browser_seconds=300)
def tournament_detail(request, format, event):
    """Tournament detail view: Displays standings and decklinks for an event."""
    tournament = get_object_or_404(Tournament, id=event)
    raw_decks = list(
        Deck.objects.filter(tournament=tournament).order_by("rank", "player")
    )

    # Disambiguate players with multiple decks
    player_counts = Counter()
    decks = []
    for d in raw_decks:
        player_counts[d.player_lower] += 1
        d.deck_index = player_counts[d.player_lower]
        decks.append(d)

    return render(
        request,
        "tournament_detail.html",
        {
            "tournament": tournament,
            "decks": decks,
        },
    )


@public_cache(cdn_seconds=3600, browser_seconds=180)
def player_detail(request, player):
    """Player detail view: Lifetime tournament history across formats."""
    player_clean = player.strip()
    player_lower = player_clean.lower()

    decks_qs = (
        Deck.objects.filter(player_lower=player_lower)
        .select_related("tournament")
        .order_by("-tournament__date")
    )
    total_decks = decks_qs.count()
    if total_decks == 0:
        raise Http404(f"No records found for player '{player}'")

    total_top8s = decks_qs.filter(is_top8=True).count()
    total_5_0s = decks_qs.filter(tournament__event_type="league", is_5_0=True).count()
    chall_appearances = decks_qs.filter(tournament__event_type="challenge").count()
    conversion_rate = (
        round((total_top8s / chall_appearances) * 100, 1)
        if chall_appearances > 0
        else 0.0
    )

    formats_played = sorted(list(set(decks_qs.values_list("format", flat=True))))

    # Add disambiguator index for player decks
    decks = []
    # Count duplicates per tournament
    event_counts = Counter()
    for d in reversed(list(decks_qs)):
        event_counts[d.tournament_id] += 1
        d.deck_index = event_counts[d.tournament_id]
        decks.insert(0, d)

    paginator = Paginator(decks, 100)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "player_detail.html",
        {
            "player_name": decks[0].player,
            "total_decks": total_decks,
            "total_top8s": total_top8s,
            "total_5_0s": total_5_0s,
            "chall_appearances": chall_appearances,
            "conversion_rate": conversion_rate,
            "formats_played": [f.capitalize() for f in formats_played],
            "decks": page_obj.object_list,
            "page_obj": page_obj,
        },
    )


@public_cache(cdn_seconds=86400, browser_seconds=300)
def deck_detail(request, player, event, deck_index=1):
    """Deck detail view: Displays mainboard, sideboard, Scryfall previews, and kNN similarities."""
    player_lower = player.strip().lower()
    tournament = get_object_or_404(Tournament, id=event)

    decks = list(
        Deck.objects.filter(tournament=tournament, player_lower=player_lower).order_by(
            "id"
        )
    )
    if not decks:
        raise Http404("Deck not found")

    idx = max(0, min(deck_index - 1, len(decks) - 1))
    deck = decks[idx]

    # Preload image URIs for cards in deck
    card_names = {item["card"] for item in deck.mainboard} | {
        item["card"] for item in deck.sideboard
    }
    cards_map = get_cards_map(card_names)
    illegal_set = set(deck.illegal_cards or [])

    annotated_mainboard = []
    for item in deck.mainboard:
        card = cards_map.get(item["card"])
        annotated_mainboard.append(
            {
                "card": item["card"],
                "card_obj": card,
                "count": item["count"],
                "image_uri": card.image_uri if card else None,
                "mana_cost": card.mana_cost if card else None,
                "scryfall_url": card.scryfall_url
                if card
                else get_scryfall_url(name=item["card"]),
                "gatherer_url": card.gatherer_url
                if card
                else get_gatherer_url(name=item["card"]),
                "is_banned": item["card"] in illegal_set,
            }
        )

    annotated_sideboard = []
    for item in deck.sideboard:
        card = cards_map.get(item["card"])
        annotated_sideboard.append(
            {
                "card": item["card"],
                "card_obj": card,
                "count": item["count"],
                "image_uri": card.image_uri if card else None,
                "mana_cost": card.mana_cost if card else None,
                "scryfall_url": card.scryfall_url
                if card
                else get_scryfall_url(name=item["card"]),
                "gatherer_url": card.gatherer_url
                if card
                else get_gatherer_url(name=item["card"]),
                "is_banned": item["card"] in illegal_set,
            }
        )

    deck.mainboard = annotated_mainboard
    deck.sideboard = annotated_sideboard

    # Generate text for copy to clipboard
    deck_text = deck.decklist_text

    # Format & Cross-Format kNN neighbors (cached per deck to avoid recomputing 300k matrix ops)
    format_neighbors = []
    cross_format_neighbors = []
    global_index = get_global_knn_index()
    if global_index is not None:
        knn_cache_key = f"deck_knn_v2_{deck.id}"
        cached_neighbors = cache.get(knn_cache_key)
        if cached_neighbors is not None:
            format_neighbors, cross_format_neighbors = cached_neighbors
        else:
            query_vec = global_index.vector_from_decklist(
                deck.mainboard, deck.sideboard
            )
            format_raw_neighbors = global_index.query(
                query_vec,
                reference_date=tournament.date,
                top_k=5,
                exclude_deck_id=deck.id,
                format_filter=deck.format,
            )
            cross_raw_neighbors = global_index.query(
                query_vec,
                reference_date=tournament.date,
                top_k=5,
                exclude_deck_id=deck.id,
                exclude_format=deck.format,
            )

            all_raw_neighbors = list(format_raw_neighbors) + list(cross_raw_neighbors)
            neighbor_ids = [str(n["deck_id"]) for n in all_raw_neighbors]
            deck_map = {}
            if neighbor_ids:
                deck_map = {
                    d["id"]: d
                    for d in Deck.objects.filter(id__in=neighbor_ids).values(
                        "id",
                        "player",
                        "archetype",
                        "colors",
                        "color_name",
                        "tournament_id",
                    )
                }

            for n in format_raw_neighbors:
                parts = str(n["deck_id"]).rsplit("_", 1)
                d_idx = parts[-1] if len(parts) == 2 and parts[-1].isdigit() else 1
                db_deck = deck_map.get(str(n["deck_id"]))
                tourn_id = (
                    db_deck["tournament_id"]
                    if db_deck
                    else str(n["deck_id"]).split("_")[0]
                )
                player = db_deck["player"] if db_deck else n["player"]
                archetype = db_deck["archetype"] if db_deck else n["archetype"]
                colors = (
                    (db_deck["colors"] if db_deck else None) or n.get("colors") or ""
                )
                color_name = (
                    (db_deck["color_name"] if db_deck else None)
                    or n.get("color_name")
                    or ""
                )
                format_neighbors.append(
                    {
                        "player": player,
                        "archetype": archetype,
                        "colors": colors,
                        "color_name": color_name,
                        "similarity_pct": round(n["raw_similarity"] * 100, 1),
                        "date": n["date"],
                        "tournament_id": tourn_id,
                        "deck_index": d_idx,
                    }
                )

            for n in cross_raw_neighbors:
                parts = str(n["deck_id"]).rsplit("_", 1)
                d_idx = parts[-1] if len(parts) == 2 and parts[-1].isdigit() else 1
                db_deck = deck_map.get(str(n["deck_id"]))
                tourn_id = (
                    db_deck["tournament_id"]
                    if db_deck
                    else str(n["deck_id"]).split("_")[0]
                )
                player = db_deck["player"] if db_deck else n["player"]
                archetype = db_deck["archetype"] if db_deck else n["archetype"]
                colors = (
                    (db_deck["colors"] if db_deck else None) or n.get("colors") or ""
                )
                color_name = (
                    (db_deck["color_name"] if db_deck else None)
                    or n.get("color_name")
                    or ""
                )
                cross_format_neighbors.append(
                    {
                        "player": player,
                        "archetype": archetype,
                        "format": n["format"].capitalize(),
                        "colors": colors,
                        "color_name": color_name,
                        "similarity_pct": round(n["raw_similarity"] * 100, 1),
                        "date": n["date"],
                        "tournament_id": tourn_id,
                        "deck_index": d_idx,
                    }
                )
            cache.set(
                knn_cache_key,
                (format_neighbors, cross_format_neighbors),
                timeout=86400,
            )

    return render(
        request,
        "deck_detail.html",
        {
            "deck": deck,
            "tournament": tournament,
            "deck_text": deck_text,
            "format_neighbors": format_neighbors,
            "cross_format_neighbors": cross_format_neighbors,
        },
    )


@public_cache(cdn_seconds=3600, browser_seconds=180)
def archetype_detail(request, format, archetype):
    """Archetype detail view: 30d/90d stats, core cards, recent event finishes."""
    fmt_slug = format.lower().strip()
    arch_slug = archetype.lower().strip()
    if fmt_slug not in settings.MODOMETA_FORMATS:
        raise Http404("Format not supported")

    days = 30 if request.GET.get("days") == "30" else 90
    cutoff, ref_date = get_timeframe_cutoff(days)

    decks_qs = Deck.objects.filter(
        format=fmt_slug,
        archetype_slug=arch_slug,
        tournament__date__gte=cutoff,
        tournament__date__lte=ref_date,
    ).select_related("tournament")

    total_decks = decks_qs.count()
    if total_decks == 0:
        # Check if archetype exists historically
        hist = Deck.objects.filter(format=fmt_slug, archetype_slug=arch_slug).first()
        if not hist:
            raise Http404(
                f"Archetype '{archetype}' not found in {fmt_slug.capitalize()}"
            )
        sample_deck = hist
    else:
        sample_deck = decks_qs.first()

    # Format-wide totals for share calculations
    fmt_decks = Deck.objects.filter(
        format=fmt_slug, tournament__date__gte=cutoff, tournament__date__lte=ref_date
    )
    total_chall_decks = max(
        1, fmt_decks.filter(tournament__event_type="challenge").count()
    )
    chall_count = Tournament.objects.filter(
        format=fmt_slug, event_type="challenge", date__gte=cutoff, date__lte=ref_date
    ).count()
    total_top8_slots = max(1, chall_count * 8)
    total_5_0s = max(
        1, fmt_decks.filter(tournament__event_type="league", is_5_0=True).count()
    )
    stats_agg = decks_qs.aggregate(
        top8_count=Count("id", filter=Q(is_top8=True)),
        chall_appearances=Count("id", filter=Q(tournament__event_type="challenge")),
        league_5_0_count=Count(
            "id", filter=Q(tournament__event_type="league", is_5_0=True)
        ),
    )
    top8_count = stats_agg["top8_count"]
    chall_appearances = stats_agg["chall_appearances"]
    league_5_0_count = stats_agg["league_5_0_count"]

    top8_share = round((top8_count / total_top8_slots) * 100, 1)
    chall_share = round((chall_appearances / total_chall_decks) * 100, 1)
    conv_rate = (
        round((top8_count / chall_appearances) * 100, 1)
        if chall_appearances > 0
        else 0.0
    )
    league_share = round((league_5_0_count / total_5_0s) * 100, 1)

    # Core cards calculation
    card_counts = Counter()
    card_total_copies = Counter()
    for d in decks_qs.values("mainboard"):
        d_cards = set()
        for item in d.get("mainboard", []):
            name = item.get("card", "")
            cnt = item.get("count", 1)
            if name:
                d_cards.add(name)
                card_total_copies[name] += cnt
        for c in d_cards:
            card_counts[c] += 1

    core_cards = []
    top_cards = card_counts.most_common(12)
    cards_map = get_cards_map([c[0] for c in top_cards])

    for name, cnt in top_cards:
        card = cards_map.get(name)
        adopt_pct = round((cnt / max(1, total_decks)) * 100, 1)
        avg_cp = round(card_total_copies[name] / max(1, cnt), 1)
        core_cards.append(
            {
                "name": name,
                "card": card,
                "mana_cost": card.mana_cost if card and card.mana_cost else "",
                "adoption_pct": adopt_pct,
                "avg_copies": avg_cp,
                "image_uri": card.image_uri if card else None,
                "scryfall_url": card.scryfall_url
                if card
                else get_scryfall_url(name=name),
                "gatherer_url": card.gatherer_url
                if card
                else get_gatherer_url(name=name),
            }
        )

    all_finishes_qs = (
        Deck.objects.filter(
            format=fmt_slug,
            archetype_slug=arch_slug,
        )
        .select_related("tournament")
        .order_by("-tournament__date", "rank", "id")
    )
    paginator = Paginator(all_finishes_qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    for f in page_obj.object_list:
        parts = f.id.rsplit("_", 1)
        f.deck_index = int(parts[-1]) if len(parts) == 2 and parts[-1].isdigit() else 1

    most_common_color = (
        decks_qs.values("colors", "color_name")
        .annotate(cnt=Count("id"))
        .order_by("-cnt")
        .first()
    )
    colors = (
        most_common_color["colors"]
        if most_common_color
        else (sample_deck.colors if sample_deck else "")
    )
    color_name = (
        most_common_color["color_name"]
        if most_common_color
        else (sample_deck.color_name if sample_deck else "")
    )

    return render(
        request,
        "archetype_detail.html",
        {
            "archetype_name": sample_deck.archetype,
            "format_name": fmt_slug.capitalize(),
            "format_slug": fmt_slug,
            "colors": colors,
            "color_name": color_name,
            "total_decks": total_decks,
            "days": days,
            "stats": {
                "total_decks": total_decks,
                "top8_count": top8_count,
                "top8_share": top8_share,
                "total_top8_slots": total_top8_slots,
                "challenge_appearances": chall_appearances,
                "challenge_share": chall_share,
                "total_chall_decks": total_chall_decks,
                "conversion_rate": conv_rate,
                "league_5_0_count": league_5_0_count,
                "league_share": league_share,
                "total_5_0s": total_5_0s,
            },
            "core_cards": core_cards,
            "finishes": page_obj.object_list,
            "page_obj": page_obj,
        },
    )


@public_cache(cdn_seconds=3600, browser_seconds=180)
def cards_list(request, format):
    """Cards view: Paginated list of most commonly played cards in format."""
    fmt_slug = format.lower().strip()
    if fmt_slug not in settings.MODOMETA_FORMATS:
        raise Http404("Format not supported")

    days = 30 if request.GET.get("days") == "30" else 90
    active_type = request.GET.get("type", "").lower().strip()
    if active_type not in ("challenge", "league"):
        active_type = ""

    card_stats = get_format_card_stats(fmt_slug, days=days, active_type=active_type)
    card_rows = card_stats["card_rows"]
    total_decks = card_stats["total_decks"]

    paginator = Paginator(card_rows, 100)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "cards.html",
        {
            "format_slug": fmt_slug,
            "format_name": fmt_slug.capitalize(),
            "active_type": active_type,
            "total_decks": total_decks,
            "cards": page_obj.object_list,
            "page_obj": page_obj,
        },
    )


@public_cache(cdn_seconds=3600, browser_seconds=180)
def leaderboard(request, format):
    """Leaderboard view: Top 100 competitors in format over timeframe."""
    fmt_slug = format.lower().strip()
    if fmt_slug not in settings.MODOMETA_FORMATS:
        raise Http404("Format not supported")

    days = 30 if request.GET.get("days") == "30" else 90
    cutoff, ref_date = get_timeframe_cutoff(days)

    decks_qs = Deck.objects.filter(
        format=fmt_slug,
        tournament__date__gte=cutoff,
        tournament__date__lte=ref_date,
    )

    player_stats = (
        decks_qs.values("player", "player_lower")
        .annotate(
            total_finishes=Count("id"),
            top8_count=Count("id", filter=Q(is_top8=True)),
            chall_appearances=Count("id", filter=Q(tournament__event_type="challenge")),
            league_count=Count(
                "id", filter=Q(tournament__event_type="league", is_5_0=True)
            ),
        )
        .order_by("-top8_count", "-league_count", "-total_finishes")[:100]
    )

    players = []
    for p in player_stats:
        top8s = p["top8_count"]
        challs = p["chall_appearances"]
        conv_rate = round((top8s / challs) * 100, 1) if challs > 0 else 0.0

        # Most played archetype
        fav_arch = (
            decks_qs.filter(player_lower=p["player_lower"])
            .values("archetype", "archetype_slug")
            .annotate(cnt=Count("id"))
            .order_by("-cnt")
            .first()
        )

        players.append(
            {
                "player": p["player"],
                "top8_count": top8s,
                "challenge_appearances": challs,
                "conversion_rate": conv_rate,
                "league_count": p["league_count"],
                "top_archetype": fav_arch["archetype"] if fav_arch else None,
                "top_archetype_slug": fav_arch["archetype_slug"] if fav_arch else None,
            }
        )

    return render(
        request,
        "leaderboard.html",
        {
            "format_slug": fmt_slug,
            "format_name": fmt_slug.capitalize(),
            "players": players,
        },
    )


@public_cache(cdn_seconds=86400, browser_seconds=3600)
def faq(request):
    """FAQ view explaining data sources, tournament coverage, and acknowledgments."""
    return render(request, "faq.html")


@public_cache(cdn_seconds=86400, browser_seconds=3600)
def search_index(request):
    """Return cached JSON search index containing all archetypes and unique players."""
    cache_key = "search_index_v1"
    data = cache.get(cache_key)
    if data is None:
        arch_qs = (
            Deck.objects.order_by()
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
            for a in arch_qs
            if a["archetype"] and a["archetype_slug"]
        ]

        player_qs = (
            Deck.objects.order_by()
            .values("player")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        players = [
            {"name": p["player"], "count": p["count"]} for p in player_qs if p["player"]
        ]

        data = {
            "archetypes": archetypes,
            "players": players,
        }
        cache.set(cache_key, data, timeout=3600 * 24)

    response = JsonResponse(data)
    response["Cache-Control"] = "public, max-age=3600"
    return response
