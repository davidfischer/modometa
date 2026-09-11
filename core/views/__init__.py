"""Views for Modometa metagame analyzer."""

import functools
import re
from collections import Counter
from collections.abc import Iterable
from datetime import date
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count
from django.db.models import Max
from django.db.models import Min
from django.db.models import Q
from django.http import Http404
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.utils.cache import patch_cache_control

from core.engine.knn import get_global_knn_index
from core.engine.search_index import build_search_index_data
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
            if request.method != "GET" or settings.DEBUG:
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


def get_dataset_min_date() -> date | None:
    """Get the earliest date of leagues or challenges in the dataset, cached for 4 hours."""
    is_testing = getattr(settings, "IS_TESTING", False)
    key = "dataset_min_date_v1"
    min_date = None if is_testing else cache.get(key)
    if min_date is None:
        min_date = Tournament.objects.filter(
            event_type__in=["league", "challenge"]
        ).aggregate(min_d=Min("date"))["min_d"]
        if not min_date:
            min_date = Tournament.objects.aggregate(min_d=Min("date"))["min_d"]
        if min_date and not is_testing:
            cache.set(key, min_date, timeout=4 * 3600)
    return min_date


def get_dataset_start_year() -> int | None:
    """Get the earliest year of leagues or challenges in the dataset."""
    min_date = get_dataset_min_date()
    return min_date.year if min_date else None


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

    # Calculate previous period for T8 Momentum (e.g. recent 90d vs previous 90d)
    prev_cutoff = cutoff - timedelta(days=days)
    prev_t8_qs = (
        Deck.objects.filter(
            format=fmt_slug,
            tournament__date__gte=prev_cutoff,
            tournament__date__lt=cutoff,
            is_top8=True,
        )
        .values("archetype_slug")
        .annotate(prev_t8_count=Count("id"))
    )
    prev_t8_map = {row["archetype_slug"]: row["prev_t8_count"] for row in prev_t8_qs}

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
        prev_top8 = prev_t8_map.get(a["archetype_slug"], 0)
        t8_momentum = a["top8_count"] - prev_top8

        archetypes.append(
            {
                "name": a["archetype"],
                "slug": a["archetype_slug"],
                "colors": colors,
                "color_name": color_name,
                "total_count": a["total_count"],
                "top8_count": a["top8_count"],
                "prev_top8_count": prev_top8,
                "t8_momentum": t8_momentum,
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

    bump_chart = build_format_bump_chart(fmt_slug, ref_date)

    paginator = Paginator(archetypes, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    active_slugs = getattr(settings, "ACTIVE_FORMAT_SLUGS", settings.MODOMETA_FORMATS)
    is_supported_format = fmt_slug in active_slugs

    return render(
        request,
        "format_overview.html",
        {
            "format_slug": fmt_slug,
            "format_name": fmt_slug.capitalize(),
            "is_supported_format": is_supported_format,
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
            "bump_chart": bump_chart,
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


def tournament_deck_sort_key(
    deck: Deck,
) -> tuple[float | int, tuple[int, int], str, str]:
    """Sort key for decks in a tournament: rank -> match record -> player -> id."""
    rank = deck.rank if deck.rank is not None else float("inf")
    result_str = (deck.result or "").strip()
    match = re.match(r"^(\d+)-(\d+)(?:-\d+)?$", result_str)
    if match:
        wins = int(match.group(1))
        losses = int(match.group(2))
        record = (-wins, losses)
    else:
        record = (0, 0)
    return (rank, record, deck.player_lower, deck.id)


@public_cache(cdn_seconds=86400, browser_seconds=300)
def tournament_detail(request, format, event):
    """Tournament detail view: Displays standings and decklinks for an event."""
    tournament = get_object_or_404(Tournament, id=event)
    raw_decks = list(Deck.objects.filter(tournament=tournament))
    raw_decks.sort(key=tournament_deck_sort_key)

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
        .defer("mainboard", "sideboard", "illegal_cards")
        .order_by("-tournament__date")
    )
    all_decks = list(decks_qs)
    total_decks = len(all_decks)
    if total_decks == 0:
        raise Http404(f"No records found for player '{player}'")

    total_top8s = sum(1 for d in all_decks if d.is_top8)
    total_5_0s = sum(
        1
        for d in all_decks
        if d.is_5_0 and getattr(d.tournament, "event_type", "") == "league"
    )
    chall_appearances = sum(
        1 for d in all_decks if getattr(d.tournament, "event_type", "") == "challenge"
    )
    conversion_rate = (
        round((total_top8s / chall_appearances) * 100, 1)
        if chall_appearances > 0
        else 0.0
    )

    formats_played = sorted(list({d.format for d in all_decks}))

    # Add disambiguator index for player decks (chronological numbering per event)
    event_counts = Counter()
    for d in reversed(all_decks):
        event_counts[d.tournament_id] += 1
        d.deck_index = event_counts[d.tournament_id]

    paginator = Paginator(all_decks, 100)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    ref_date = get_reference_date()
    player_name = all_decks[0].player
    heatmap = build_player_heatmap(
        player_lower=player_lower,
        ref_date=ref_date,
        player_name=player_name,
    )

    return render(
        request,
        "player_detail.html",
        {
            "player_name": player_name,
            "total_decks": total_decks,
            "total_top8s": total_top8s,
            "total_5_0s": total_5_0s,
            "chall_appearances": chall_appearances,
            "conversion_rate": conversion_rate,
            "formats_played": [f.capitalize() for f in formats_played],
            "decks": page_obj.object_list,
            "page_obj": page_obj,
            "heatmap": heatmap,
            "start_year": get_dataset_start_year(),
        },
    )


CARD_TYPE_CATEGORIES: list[tuple[str, str, str]] = [
    ("creature", "Creatures", "creature"),
    ("planeswalker", "Planeswalkers", "planeswalker"),
    ("instant", "Instants", "instant"),
    ("sorcery", "Sorceries", "sorcery"),
    ("artifact", "Artifacts", "artifact"),
    ("enchantment", "Enchantments", "enchantment"),
    ("battle", "Battles", "battle"),
    ("land", "Lands", "land"),
    ("other", "Other", "multiple"),
]


def classify_card_type(card: Card | None) -> str:
    """Classify a card into a type category key.

    Precedence:
    1. If it's a MDFC, use the front face for the type.
    2. If it's a land, always put in 'land' (e.g. Dryad Arbor is a land creature -> land).
    3. If not land, put in 'creature' if it's a creature (e.g. Overlord of the Balemurk -> creature).
    4. Otherwise choose one of the remaining categories:
       planeswalker, instant, sorcery, artifact, enchantment, battle.
    """
    if not card or not card.type_line:
        return "other"

    front_face = card.type_line.split(" // ")[0].strip().lower()
    main_type = front_face.split("—")[0].split("-")[0]
    words = set(main_type.split())

    if "land" in words:
        return "land"
    if "creature" in words:
        return "creature"
    if "planeswalker" in words:
        return "planeswalker"
    if "instant" in words:
        return "instant"
    if "sorcery" in words:
        return "sorcery"
    if "artifact" in words:
        return "artifact"
    if "enchantment" in words:
        return "enchantment"
    if "battle" in words:
        return "battle"
    return "other"


def build_deck_mainboard_sections(annotated_mainboard: list[dict]) -> list[dict]:
    """Group and sort mainboard cards into sections by card type.

    Within each type section, cards are sorted by converted mana cost followed by name.
    """
    buckets: dict[str, list[dict]] = {k: [] for k, _, _ in CARD_TYPE_CATEGORIES}
    for item in annotated_mainboard:
        card = item.get("card_obj")
        cat = classify_card_type(card)
        buckets[cat].append(item)

    sections = []
    for cat_key, cat_name, icon in CARD_TYPE_CATEGORIES:
        cards = buckets.get(cat_key, [])
        if not cards:
            continue
        cards.sort(key=lambda x: (x.get("cmc", 0.0), x.get("card", "")))
        count = sum(x.get("count", 0) for x in cards)
        sections.append(
            {
                "key": cat_key,
                "name": cat_name,
                "icon": icon,
                "count": count,
                "cards": cards,
            }
        )
    return sections


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
                "cmc": card.cmc if card else 0.0,
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
                "cmc": card.cmc if card else 0.0,
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
    annotated_sideboard.sort(key=lambda x: (x.get("cmc", 0.0), x.get("card", "")))

    mainboard_sections = build_deck_mainboard_sections(annotated_mainboard)
    deck.mainboard = annotated_mainboard
    deck.sideboard = annotated_sideboard
    deck.mainboard_sections = mainboard_sections

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
            "mainboard_sections": mainboard_sections,
            "format_neighbors": format_neighbors,
            "cross_format_neighbors": cross_format_neighbors,
        },
    )


ARCHETYPE_COLOR_AFFINITIES: dict[frozenset[str], list[str]] = {
    # Mono colors
    frozenset(["R"]): ["#ef4444", "#dc2626", "#f87171", "#ea580c"],  # Red
    frozenset(["U"]): ["#0284c7", "#0ea5e9", "#2563eb", "#38bdf8"],  # Blue
    frozenset(["G"]): ["#10b981", "#16a34a", "#22c55e", "#059669"],  # Green
    frozenset(["B"]): [
        "#8b5cf6",
        "#7c3aed",
        "#a855f7",
        "#6366f1",
    ],  # Black (purple/violet)
    frozenset(["W"]): [
        "#f59e0b",
        "#eab308",
        "#d97706",
        "#fbbf24",
    ],  # White (amber/gold)
    frozenset(["C"]): ["#a1a1aa", "#94a3b8", "#71717a", "#64748b"],  # Colorless (slate)
    frozenset([]): ["#a1a1aa", "#94a3b8", "#71717a"],
    # Guilds (2-color)
    frozenset(["R", "W"]): [
        "#f97316",
        "#fb923c",
        "#ea580c",
        "#f43f5e",
    ],  # Boros: Orange / Coral
    frozenset(["U", "R"]): [
        "#06b6d4",
        "#0891b2",
        "#d946ef",
        "#0284c7",
    ],  # Izzet: Cyan / Magenta
    frozenset(["U", "B"]): [
        "#6366f1",
        "#4f46e5",
        "#818cf8",
        "#3b82f6",
    ],  # Dimir: Indigo / Deep Blue
    frozenset(["B", "G"]): [
        "#15803d",
        "#166534",
        "#4d7c0f",
        "#84cc16",
    ],  # Golgari: Olive / Forest
    frozenset(["R", "G"]): [
        "#ea580c",
        "#d97706",
        "#c2410c",
        "#f97316",
    ],  # Gruul: Rust / Red-Orange
    frozenset(["W", "U"]): [
        "#0ea5e9",
        "#38bdf8",
        "#0284c7",
        "#60a5fa",
    ],  # Azorius: Sky Blue
    frozenset(["W", "B"]): [
        "#a855f7",
        "#94a3b8",
        "#7c3aed",
        "#c084fc",
    ],  # Orzhov: Lavender / Plum
    frozenset(["B", "R"]): [
        "#e11d48",
        "#be123c",
        "#9f1239",
        "#f43f5e",
    ],  # Rakdos: Crimson
    frozenset(["G", "W"]): [
        "#84cc16",
        "#65a30d",
        "#4ade80",
        "#22c55e",
    ],  # Selesnya: Lime
    frozenset(["G", "U"]): ["#14b8a6", "#0d9488", "#06b6d4", "#2dd4bf"],  # Simic: Teal
    # Shards & Clans (3-color)
    frozenset(["W", "U", "R"]): [
        "#f43f5e",
        "#fb7185",
        "#0ea5e9",
        "#f97316",
    ],  # Jeskai: Rose / Coral
    frozenset(["W", "U", "B"]): [
        "#818cf8",
        "#6366f1",
        "#0ea5e9",
        "#8b5cf6",
    ],  # Esper: Periwinkle
    frozenset(["U", "B", "R"]): [
        "#d946ef",
        "#c026d3",
        "#7c3aed",
        "#be123c",
    ],  # Grixis: Magenta
    frozenset(["B", "R", "G"]): [
        "#c2410c",
        "#b45309",
        "#ea580c",
        "#15803d",
    ],  # Jund: Burnt Orange
    frozenset(["G", "W", "U"]): [
        "#34d399",
        "#10b981",
        "#14b8a6",
        "#84cc16",
    ],  # Bant: Seafoam
    frozenset(["W", "B", "G"]): [
        "#65a30d",
        "#84cc16",
        "#15803d",
        "#a855f7",
    ],  # Abzan: Olive Drab
    frozenset(["U", "R", "G"]): [
        "#06b6d4",
        "#0891b2",
        "#14b8a6",
        "#10b981",
    ],  # Temur: Cyan / Teal
    frozenset(["W", "B", "R"]): [
        "#e11d48",
        "#be123c",
        "#f97316",
        "#8b5cf6",
    ],  # Mardu: Crimson / Orange
    frozenset(["U", "B", "G"]): [
        "#0d9488",
        "#14b8a6",
        "#10b981",
        "#6366f1",
    ],  # Sultai: Dark Teal
    frozenset(["R", "G", "W"]): [
        "#f97316",
        "#ea580c",
        "#84cc16",
        "#f59e0b",
    ],  # Naya: Warm Orange
    # 4 & 5 color
    frozenset(["W", "U", "B", "R", "G"]): [
        "#f59e0b",
        "#eab308",
        "#d97706",
        "#a855f7",
    ],  # 5-Color: Gold
}

FALLBACK_PALETTE: list[str] = [
    "#10b981",
    "#0ea5e9",
    "#f59e0b",
    "#f43f5e",
    "#8b5cf6",
    "#06b6d4",
    "#f97316",
    "#84cc16",
    "#d946ef",
    "#6366f1",
    "#ec4899",
    "#14b8a6",
    "#e11d48",
    "#ea580c",
    "#a855f7",
    "#94a3b8",
    "#0284c7",
    "#16a34a",
    "#fb923c",
    "#818cf8",
    "#eab308",
    "#7c3aed",
]


def assign_archetype_colors(
    top_slugs: list[str], slug_colors: dict[str, str]
) -> dict[str, str]:
    """Assign visually distinct chart colors correlated to deck MTG color identities."""
    used_colors: set[str] = set()
    color_map: dict[str, str] = {}

    for slug in top_slugs:
        raw_colors = slug_colors.get(slug, "")
        colors_set = frozenset(c.upper() for c in raw_colors if c.upper() in "WUBRGC")
        candidates = ARCHETYPE_COLOR_AFFINITIES.get(colors_set, [])
        chosen = None

        # 1. Try exact color combination candidates
        for cand in candidates:
            if cand not in used_colors:
                chosen = cand
                break

        # 2. Try component colors if exact combo candidates are all taken
        if not chosen and colors_set:
            for single_c in sorted(colors_set):
                for cand in ARCHETYPE_COLOR_AFFINITIES.get(frozenset([single_c]), []):
                    if cand not in used_colors:
                        chosen = cand
                        break
                if chosen:
                    break

        # 3. Fall back to unused colors from general palette
        if not chosen:
            for cand in FALLBACK_PALETTE:
                if cand not in used_colors:
                    chosen = cand
                    break

        chosen = chosen or "#71717a"
        used_colors.add(chosen)
        color_map[slug] = chosen

    return color_map


def build_format_bump_chart(fmt_slug: str, ref_date: date) -> dict:
    """Build a 26-week Top 10 rank evolution bump chart for a format by T8 share."""
    monday_offset = ref_date.weekday()
    current_week_monday = ref_date - timedelta(days=monday_offset)
    total_weeks = 26
    start_monday = current_week_monday - timedelta(weeks=total_weeks - 1)

    t8_decks = list(
        Deck.objects.filter(
            format=fmt_slug,
            tournament__date__gte=start_monday,
            tournament__date__lte=ref_date,
            is_top8=True,
        ).values("archetype", "archetype_slug", "tournament__date", "colors")
    )

    X_OFFSET = 18
    STEP_X = 15
    Y_OFFSET = 24
    STEP_Y = 16
    x_start = X_OFFSET - 8
    x_end = X_OFFSET + (total_weeks - 1) * STEP_X + 8
    SVG_WIDTH = X_OFFSET + (total_weeks - 1) * STEP_X + 18  # 411
    SVG_HEIGHT = Y_OFFSET + 9 * STEP_Y + 16  # 184

    month_labels = []
    last_labeled_month = None
    last_labeled_col = -99

    weeks = []
    weekly_counts: list[Counter] = [Counter() for _ in range(total_weeks)]
    weekly_totals = [0] * total_weeks
    slug_to_name: dict[str, str] = {}

    for i in range(total_weeks):
        col_monday = start_monday + timedelta(weeks=i)
        col_x = X_OFFSET + i * STEP_X

        # Month label logic: first column or 1st of month
        first_of_month_in_week = None
        for day_idx in range(7):
            cur_d = col_monday + timedelta(days=day_idx)
            if cur_d.day == 1:
                first_of_month_in_week = cur_d
                break

        if i == 0:
            month_labels.append({"name": col_monday.strftime("%b"), "x": col_x})
            last_labeled_month = col_monday.month
            last_labeled_col = 0
        elif first_of_month_in_week:
            if (
                first_of_month_in_week.month != last_labeled_month
                and (i - last_labeled_col) >= 2
            ):
                month_name = first_of_month_in_week.strftime("%b")
                # Prevent right-edge clipping if label is near the chart boundary
                if col_x + 24 > x_end:
                    month_labels.append(
                        {"name": month_name, "x": x_end, "anchor": "end"}
                    )
                else:
                    month_labels.append({"name": month_name, "x": col_x})
                last_labeled_month = first_of_month_in_week.month
                last_labeled_col = i

        col_sunday = col_monday + timedelta(days=6)
        if col_monday.month == col_sunday.month:
            week_label = (
                f"{col_monday.strftime('%b')} {col_monday.day}-{col_sunday.day}"
            )
        else:
            week_label = (
                f"{col_monday.strftime('%b')} {col_monday.day}-"
                f"{col_sunday.strftime('%b')} {col_sunday.day}"
            )

        weeks.append(
            {
                "index": i,
                "monday": col_monday,
                "label": week_label,
                "x": col_x,
            }
        )

    arch_colors: dict[str, Counter] = {}
    for d in t8_decks:
        d_date = d["tournament__date"]
        w_idx = (d_date - start_monday).days // 7
        if 0 <= w_idx < total_weeks:
            slug = d["archetype_slug"]
            weekly_counts[w_idx][slug] += 1
            weekly_totals[w_idx] += 1
            if slug not in slug_to_name:
                slug_to_name[slug] = d["archetype"]
            c_val = d.get("colors") or ""
            if c_val:
                if slug not in arch_colors:
                    arch_colors[slug] = Counter()
                arch_colors[slug][c_val] += 1

    has_data = any(t > 0 for t in weekly_totals)

    rank_lines = [{"rank": r, "y": Y_OFFSET + (r - 1) * STEP_Y} for r in range(1, 11)]

    if not has_data:
        return {
            "has_data": False,
            "svg_width": SVG_WIDTH,
            "svg_height": SVG_HEIGHT,
            "x_start": x_start,
            "x_end": x_end,
            "month_labels": month_labels,
            "rank_lines": rank_lines,
            "tracks": [],
        }

    # Count overall T8s across the 26 weeks
    overall_t8 = Counter()
    for w_idx in range(total_weeks):
        overall_t8.update(weekly_counts[w_idx])

    top_slugs = [slug for slug, _ in overall_t8.most_common(20)]
    slug_primary_colors = {
        slug: counter.most_common(1)[0][0]
        for slug, counter in arch_colors.items()
        if counter
    }
    color_map = assign_archetype_colors(top_slugs, slug_primary_colors)

    # Determine weekly top 10 rankings by T8 count/share
    weekly_ranks: dict[int, dict[str, tuple[int, int, float]]] = {}
    for w_idx in range(total_weeks):
        counts = weekly_counts[w_idx]
        total_w = max(1, weekly_totals[w_idx])
        sorted_archs = sorted(
            counts.keys(),
            key=lambda s: (-counts[s], -overall_t8[s], s),
        )
        ranks_dict = {}
        for r_idx, slug in enumerate(sorted_archs[:10]):
            cnt = counts[slug]
            share = round((cnt / total_w) * 100, 1)
            ranks_dict[slug] = (r_idx + 1, cnt, share)
        weekly_ranks[w_idx] = ranks_dict

    all_ranked_slugs = set()
    for w_idx in range(total_weeks):
        all_ranked_slugs.update(weekly_ranks[w_idx].keys())

    sorted_slugs = sorted(
        all_ranked_slugs,
        key=lambda s: (-overall_t8[s], s),
    )

    tracks = []
    for slug in sorted_slugs:
        color = color_map.get(slug, "#71717a")
        is_featured = slug in color_map

        points = []
        for w_idx in range(total_weeks):
            if slug in weekly_ranks[w_idx]:
                r, cnt, share = weekly_ranks[w_idx][slug]
                pt_x = X_OFFSET + w_idx * STEP_X
                pt_y = Y_OFFSET + (r - 1) * STEP_Y
                points.append(
                    {
                        "week_idx": w_idx,
                        "x": pt_x,
                        "y": pt_y,
                        "rank": r,
                        "count": cnt,
                        "share": share,
                        "week_label": weeks[w_idx]["label"],
                        "url": reverse(
                            "core:archetype_detail",
                            kwargs={"format": fmt_slug, "archetype": slug},
                        ),
                    }
                )

        if not points:
            continue

        # Group consecutive weeks into continuous path segments
        path_segments = []
        cur_segment = [points[0]]
        for pt in points[1:]:
            prev_pt = cur_segment[-1]
            if pt["week_idx"] == prev_pt["week_idx"] + 1:
                cur_segment.append(pt)
            else:
                path_segments.append(cur_segment)
                cur_segment = [pt]
        path_segments.append(cur_segment)

        d_parts = []
        for seg in path_segments:
            if len(seg) == 1:
                continue
            d_parts.append(f"M {seg[0]['x']} {seg[0]['y']}")
            for k in range(len(seg) - 1):
                p1 = seg[k]
                p2 = seg[k + 1]
                dx = p2["x"] - p1["x"]
                cp1_x = round(p1["x"] + dx / 2, 1)
                cp1_y = p1["y"]
                cp2_x = round(p2["x"] - dx / 2, 1)
                cp2_y = p2["y"]
                d_parts.append(
                    f"C {cp1_x} {cp1_y}, {cp2_x} {cp2_y}, {p2['x']} {p2['y']}"
                )

        path_d = " ".join(d_parts)

        tracks.append(
            {
                "slug": slug,
                "name": slug_to_name.get(slug, slug),
                "color": color,
                "is_featured": is_featured,
                "path_d": path_d,
                "points": points,
            }
        )

    # Sort so non-featured are drawn first, featured tracks on top
    tracks.sort(key=lambda t: (1 if t["is_featured"] else 0, overall_t8[t["slug"]]))

    return {
        "has_data": has_data,
        "svg_width": SVG_WIDTH,
        "svg_height": SVG_HEIGHT,
        "x_start": x_start,
        "x_end": x_end,
        "month_labels": month_labels,
        "rank_lines": rank_lines,
        "tracks": tracks,
    }


def get_activity_heatmap_date_range(
    ref_date: date, weeks_prior: int = 26
) -> tuple[date, date, int]:
    """Calculate start date and total number of weeks (weeks_prior + partial/current week)."""
    monday_offset = ref_date.weekday()
    current_week_monday = ref_date - timedelta(days=monday_offset)
    total_weeks = weeks_prior + 1
    start_date = current_week_monday - timedelta(weeks=weeks_prior)
    return start_date, ref_date, total_weeks


def build_activity_heatmap_grid(
    finishes_by_date: dict[date, list[dict]],
    ref_date: date,
    default_format: str | None = None,
    aria_label: str = "26-week activity heatmap",
) -> dict:
    """Build 26 weeks + partial week (27 columns x 7 days, Monday-Sunday) activity heatmap SVG grid."""
    start_date, _, total_weeks = get_activity_heatmap_date_range(
        ref_date, weeks_prior=26
    )

    CELL_SIZE = 10
    CELL_GAP = 3
    STEP = CELL_SIZE + CELL_GAP  # 13
    X_OFFSET = 36
    Y_OFFSET = 20
    total_svg_width = X_OFFSET + total_weeks * STEP + 24
    total_svg_height = Y_OFFSET + 7 * STEP + 6

    weeks = []
    month_labels = []
    last_labeled_month = None
    last_labeled_col = -99

    active_days_count = 0
    total_leagues = 0
    total_challenges = 0
    total_top8s = 0

    for col_idx in range(total_weeks):
        col_monday = start_date + timedelta(weeks=col_idx)
        days = []
        col_x = X_OFFSET + col_idx * STEP

        # Month label logic: label col 0, or whenever month changes
        first_of_month_in_week = None
        for day_idx in range(7):
            cur_d = col_monday + timedelta(days=day_idx)
            if cur_d.day == 1:
                first_of_month_in_week = cur_d
                break

        if col_idx == 0:
            month_name = col_monday.strftime("%b")
            month_labels.append({"name": month_name, "x": col_x})
            last_labeled_month = col_monday.month
            last_labeled_col = 0
        elif first_of_month_in_week:
            if (
                first_of_month_in_week.month != last_labeled_month
                and (col_idx - last_labeled_col) >= 2
            ):
                month_name = first_of_month_in_week.strftime("%b")
                # Prevent right-edge clipping if label is near the chart boundary
                if col_x + 24 > total_svg_width - 10:
                    month_labels.append(
                        {
                            "name": month_name,
                            "x": total_svg_width - 10,
                            "anchor": "end",
                        }
                    )
                else:
                    month_labels.append({"name": month_name, "x": col_x})
                last_labeled_month = first_of_month_in_week.month
                last_labeled_col = col_idx

        for row_idx in range(7):
            cur_date = col_monday + timedelta(days=row_idx)
            cur_y = Y_OFFSET + row_idx * STEP
            is_future = cur_date > ref_date

            if is_future:
                days.append(
                    {
                        "date": cur_date,
                        "date_str": cur_date.strftime("%Y-%m-%d"),
                        "x": col_x,
                        "y": cur_y,
                        "is_future": True,
                        "color_class": "activity-future",
                        "tooltip": "",
                        "url": "",
                    }
                )
                continue

            day_finishes = finishes_by_date.get(cur_date, [])
            l_count = 0
            c_top8_count = 0
            c_other_count = 0
            c_winner_count = 0

            for f in day_finishes:
                etype = (f.get("tournament__event_type") or "").lower()
                is_t8 = bool(f.get("is_top8"))
                is_50 = bool(f.get("is_5_0"))
                rank = f.get("rank")
                res = (f.get("result") or "").lower()

                if etype == "league" or is_50:
                    l_count += 1
                elif etype == "challenge":
                    if is_t8:
                        c_top8_count += 1
                        if rank == 1 or "1st" in res:
                            c_winner_count += 1
                    else:
                        c_other_count += 1

            total_leagues += l_count
            total_challenges += c_top8_count + c_other_count
            total_top8s += c_top8_count

            if day_finishes:
                active_days_count += 1

            # Priority: Challenge Top 8 > Challenge Entry > League 5-0 > Inactive
            if c_top8_count > 0:
                if c_winner_count > 0 or c_top8_count >= 2:
                    color_class = "activity-challenge-winner"
                else:
                    color_class = "activity-challenge-top8"
            elif c_other_count > 0:
                color_class = "activity-challenge-entry"
            elif l_count > 0:
                if l_count >= 3:
                    color_class = "activity-league-3"
                elif l_count == 2:
                    color_class = "activity-league-2"
                else:
                    color_class = "activity-league-1"
            else:
                color_class = "activity-level-0"

            # Construct tooltip: e.g. "2x Challenge Top 8s, 3x League 5-0s"
            tooltip_parts = []
            if c_top8_count > 0:
                t8_str = f"{c_top8_count}x Challenge Top 8" + (
                    "s" if c_top8_count > 1 else ""
                )
                tooltip_parts.append(t8_str)
            if c_other_count > 0:
                entry_str = (
                    f"{c_other_count}x Challenge entry"
                    if c_other_count == 1
                    else f"{c_other_count}x Challenge entries"
                )
                tooltip_parts.append(entry_str)
            if l_count > 0:
                lg_str = f"{l_count}x League 5-0" + ("s" if l_count > 1 else "")
                tooltip_parts.append(lg_str)

            date_label = f"{cur_date.strftime('%b')} {cur_date.day}"
            if tooltip_parts:
                tooltip = f"{date_label}: {', '.join(tooltip_parts)}"
            else:
                tooltip = f"{date_label}: No tournament finishes"

            target_tourn_id = None
            target_format = None
            if day_finishes:
                challenge_finishes = [
                    f
                    for f in day_finishes
                    if (f.get("tournament__event_type") or "").lower() == "challenge"
                ]
                if challenge_finishes:
                    t8_challenges = [f for f in challenge_finishes if f.get("is_top8")]
                    chosen = (
                        t8_challenges[0] if t8_challenges else challenge_finishes[0]
                    )
                    target_tourn_id = chosen.get("tournament_id")
                    target_format = (
                        chosen.get("format") or default_format or ""
                    ).lower()
                else:
                    league_finishes = [
                        f
                        for f in day_finishes
                        if (f.get("tournament__event_type") or "").lower() == "league"
                        or f.get("is_5_0")
                    ]
                    chosen = league_finishes[0] if league_finishes else day_finishes[0]
                    target_tourn_id = chosen.get("tournament_id")
                    target_format = (
                        chosen.get("format") or default_format or ""
                    ).lower()

            day_url = ""
            if target_tourn_id and target_format:
                try:
                    day_url = reverse(
                        "core:tournament_detail",
                        kwargs={"format": target_format, "event": target_tourn_id},
                    )
                except Exception:
                    day_url = ""

            days.append(
                {
                    "date": cur_date,
                    "date_str": cur_date.strftime("%Y-%m-%d"),
                    "x": col_x,
                    "y": cur_y,
                    "is_future": False,
                    "color_class": color_class,
                    "tooltip": tooltip,
                    "url": day_url,
                    "leagues_count": l_count,
                    "challenges_count": c_top8_count + c_other_count,
                    "top8_count": c_top8_count,
                }
            )

        weeks.append(
            {
                "col_idx": col_idx,
                "col_x": col_x,
                "days": days,
            }
        )

    day_labels = [
        {"name": "Mon", "y": Y_OFFSET + 0 * STEP + 8},
        {"name": "Wed", "y": Y_OFFSET + 2 * STEP + 8},
        {"name": "Fri", "y": Y_OFFSET + 4 * STEP + 8},
        {"name": "Sun", "y": Y_OFFSET + 6 * STEP + 8},
    ]

    return {
        "weeks": weeks,
        "month_labels": month_labels,
        "day_labels": day_labels,
        "active_days_count": active_days_count,
        "total_leagues": total_leagues,
        "total_challenges": total_challenges,
        "total_top8s": total_top8s,
        "svg_width": total_svg_width,
        "svg_height": total_svg_height,
        "cell_size": CELL_SIZE,
        "aria_label": aria_label,
    }


def build_archetype_heatmap(
    format_slug: str, archetype_slug: str, ref_date: date
) -> dict:
    """Build 26 weeks + partial week (27 columns x 7 days, Monday-Sunday) activity heatmap."""
    start_date, _, _ = get_activity_heatmap_date_range(ref_date, weeks_prior=26)

    # Query all appearances for archetype within [start_date, ref_date]
    decks_qs = Deck.objects.filter(
        format=format_slug,
        archetype_slug=archetype_slug,
        tournament__date__gte=start_date,
        tournament__date__lte=ref_date,
    ).values(
        "tournament__date",
        "tournament__event_type",
        "tournament__name",
        "tournament_id",
        "format",
        "is_top8",
        "is_5_0",
        "rank",
        "result",
    )

    finishes_by_date: dict[date, list[dict]] = {}
    for d in decks_qs:
        d_date = d["tournament__date"]
        if d_date not in finishes_by_date:
            finishes_by_date[d_date] = []
        finishes_by_date[d_date].append(d)

    return build_activity_heatmap_grid(
        finishes_by_date=finishes_by_date,
        ref_date=ref_date,
        default_format=format_slug,
        aria_label="Archetype 26-week activity heatmap",
    )


def build_player_heatmap(
    player_lower: str, ref_date: date, player_name: str | None = None
) -> dict:
    """Build 26 weeks + partial week (27 columns x 7 days, Monday-Sunday) activity heatmap for a player across all formats."""
    start_date, _, _ = get_activity_heatmap_date_range(ref_date, weeks_prior=26)

    # Query all appearances for player within [start_date, ref_date]
    decks_qs = Deck.objects.filter(
        player_lower=player_lower,
        tournament__date__gte=start_date,
        tournament__date__lte=ref_date,
    ).values(
        "tournament__date",
        "tournament__event_type",
        "tournament__name",
        "tournament_id",
        "format",
        "is_top8",
        "is_5_0",
        "rank",
        "result",
    )

    finishes_by_date: dict[date, list[dict]] = {}
    for d in decks_qs:
        d_date = d["tournament__date"]
        if d_date not in finishes_by_date:
            finishes_by_date[d_date] = []
        finishes_by_date[d_date].append(d)

    aria_label = (
        f"{player_name}'s 26-week activity heatmap"
        if player_name
        else "Player 26-week activity heatmap"
    )

    return build_activity_heatmap_grid(
        finishes_by_date=finishes_by_date,
        ref_date=ref_date,
        default_format=None,
        aria_label=aria_label,
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

    # Format-wide totals for share calculations (cached per format/timeframe to avoid repeated full-table queries)
    fmt_totals_key = f"format_totals_v1:{fmt_slug}:{days}"
    fmt_totals = cache.get(fmt_totals_key)
    if fmt_totals is None:
        fmt_decks = Deck.objects.filter(
            format=fmt_slug,
            tournament__date__gte=cutoff,
            tournament__date__lte=ref_date,
        )
        total_chall_decks = max(
            1, fmt_decks.filter(tournament__event_type="challenge").count()
        )
        chall_count = Tournament.objects.filter(
            format=fmt_slug,
            event_type="challenge",
            date__gte=cutoff,
            date__lte=ref_date,
        ).count()
        total_top8_slots = max(1, chall_count * 8)
        total_5_0s = max(
            1, fmt_decks.filter(tournament__event_type="league", is_5_0=True).count()
        )
        fmt_totals = (total_chall_decks, total_top8_slots, total_5_0s)
        cache.set(fmt_totals_key, fmt_totals, timeout=3600)
    else:
        total_chall_decks, total_top8_slots, total_5_0s = fmt_totals
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

    # Core cards & archetype colors calculation
    card_counts = Counter()
    card_total_copies = Counter()
    color_counts = Counter()
    for d in decks_qs.values("mainboard", "colors", "color_name"):
        d_cards = set()
        for item in d.get("mainboard", []):
            name = item.get("card", "")
            cnt = item.get("count", 1)
            if name:
                d_cards.add(name)
                card_total_copies[name] += cnt
        for c in d_cards:
            card_counts[c] += 1
        if d.get("colors") or d.get("color_name"):
            color_counts[(d.get("colors", ""), d.get("color_name", ""))] += 1

    most_common_color = color_counts.most_common(1)
    if most_common_color:
        colors, color_name = most_common_color[0][0]
    else:
        colors = sample_deck.colors if sample_deck else ""
        color_name = sample_deck.color_name if sample_deck else ""

    card_stats = []
    for name, cnt in card_counts.items():
        adopt_pct = round((cnt / max(1, total_decks)) * 100, 1)
        avg_cp = round(card_total_copies[name] / max(1, cnt), 1)
        card_stats.append((adopt_pct, avg_cp, cnt, name))

    # Sort primarily by adoption_pct (descending), secondarily by avg_copies (descending), then name (ascending)
    card_stats.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3]))
    top_cards = card_stats[:12]
    cards_map = get_cards_map([c[3] for c in top_cards])

    core_cards = []
    for adopt_pct, avg_cp, _cnt, name in top_cards:
        card = cards_map.get(name)
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
        .defer("mainboard", "sideboard", "illegal_cards")
        .order_by("-tournament__date", "rank", "id")
    )
    paginator = Paginator(all_finishes_qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    for f in page_obj.object_list:
        parts = f.id.rsplit("_", 1)
        f.deck_index = int(parts[-1]) if len(parts) == 2 and parts[-1].isdigit() else 1

    heatmap = build_archetype_heatmap(fmt_slug, arch_slug, ref_date)

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
            "heatmap": heatmap,
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

    player_stats = list(
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

    # Batch query top archetypes for all top players to eliminate N+1 queries
    top_lowers = [p["player_lower"] for p in player_stats]
    fav_arch_map = {}
    if top_lowers:
        arch_counts_qs = (
            decks_qs.filter(player_lower__in=top_lowers)
            .values("player_lower", "archetype", "archetype_slug")
            .annotate(cnt=Count("id"))
            .order_by("player_lower", "-cnt")
        )
        for row in arch_counts_qs:
            pl = row["player_lower"]
            if pl not in fav_arch_map:
                fav_arch_map[pl] = (row["archetype"], row["archetype_slug"])

    players = []
    for p in player_stats:
        top8s = p["top8_count"]
        challs = p["chall_appearances"]
        conv_rate = round((top8s / challs) * 100, 1) if challs > 0 else 0.0

        fav_arch_tuple = fav_arch_map.get(p["player_lower"])
        fav_arch_name = fav_arch_tuple[0] if fav_arch_tuple else None
        fav_arch_slug = fav_arch_tuple[1] if fav_arch_tuple else None

        players.append(
            {
                "player": p["player"],
                "top8_count": top8s,
                "challenge_appearances": challs,
                "conversion_rate": conv_rate,
                "league_count": p["league_count"],
                "top_archetype": fav_arch_name,
                "top_archetype_slug": fav_arch_slug,
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
    """Return cached JSON search index from disk, in-memory cache, or database fallback."""
    is_testing = getattr(settings, "IS_TESTING", False)
    search_index_path = getattr(
        settings,
        "SEARCH_INDEX_PATH",
        settings.BASE_DIR / "data" / "search_index.json",
    )

    if not is_testing and search_index_path.is_file():
        return HttpResponse(
            search_index_path.read_bytes(), content_type="application/json"
        )

    cache_key = "search_index_v1"
    data = cache.get(cache_key)
    if data is None:
        active_slugs = getattr(
            settings, "ACTIVE_FORMAT_SLUGS", settings.MODOMETA_FORMATS
        )
        data = build_search_index_data(formats=active_slugs)
        cache.set(cache_key, data, timeout=86400)

    return JsonResponse(data)
