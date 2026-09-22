"""Chart data builders, heatmap grids, and matchup matrix calculations."""

import re
from collections import Counter
from datetime import date
from datetime import timedelta

from django.urls import reverse

from core.models.deck import Deck
from core.models.match import Match
from core.models.tournament import Tournament
from core.views.utils import get_timeframe_cutoff


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


def build_tournament_archetype_chart(
    decks: list[Deck], is_challenge: bool = True
) -> dict | None:
    """Build archetype play rate and conversion chart data for tournament events."""
    if not decks:
        return None

    total_decks = len(decks)
    arch_map: dict[str, dict] = {}
    for d in decks:
        slug = d.archetype_slug
        if slug not in arch_map:
            arch_map[slug] = {
                "name": d.archetype,
                "slug": slug,
                "colors_list": [],
                "top8_decks": [],
                "swiss_decks": [],
            }
        arch_map[slug]["colors_list"].append((d.colors, d.color_name))
        if is_challenge and d.is_top8:
            arch_map[slug]["top8_decks"].append(d)
        else:
            arch_map[slug]["swiss_decks"].append(d)

    rows = []
    total_top8_decks = 0
    total_swiss_decks = 0

    for slug, data in arch_map.items():
        top8_decks = data["top8_decks"]
        swiss_decks = data["swiss_decks"]
        top8_count = len(top8_decks)
        swiss_count = len(swiss_decks)
        total_count = top8_count + swiss_count
        total_top8_decks += top8_count
        total_swiss_decks += swiss_count

        color_counts = Counter(data["colors_list"])
        most_common_colors, most_common_color_name = color_counts.most_common(1)[0][0]

        units = []
        for d in top8_decks:
            units.append(
                {
                    "is_top8": True,
                    "player": d.player,
                    "result": d.result,
                    "rank": d.rank,
                    "deck_index": getattr(d, "deck_index", 1),
                    "tooltip": f"{d.result}: {d.player} ({d.archetype})",
                }
            )
        for d in swiss_decks:
            units.append(
                {
                    "is_top8": False,
                    "player": d.player,
                    "result": d.result,
                    "rank": d.rank,
                    "deck_index": getattr(d, "deck_index", 1),
                    "tooltip": f"{d.result}: {d.player} ({d.archetype})",
                }
            )

        share_pct = round((total_count / total_decks) * 100, 1) if total_decks else 0.0

        rows.append(
            {
                "name": data["name"],
                "slug": data["slug"],
                "colors": most_common_colors,
                "color_name": most_common_color_name,
                "total_count": total_count,
                "top8_count": top8_count,
                "swiss_count": swiss_count,
                "share_pct": share_pct,
                "units": units,
            }
        )

    rows.sort(key=lambda r: (-r["total_count"], -r["top8_count"], r["name"].lower()))
    max_count = max((r["total_count"] for r in rows), default=0)

    return {
        "is_challenge": is_challenge,
        "total_decks": total_decks,
        "total_archetypes": len(rows),
        "total_top8_decks": total_top8_decks,
        "total_swiss_decks": total_swiss_decks,
        "max_count": max_count,
        "rows": rows,
    }


def build_challenge_archetype_chart(decks: list[Deck]) -> dict | None:
    """Build archetype play rate and Top 8 conversion chart data for challenge events."""
    return build_tournament_archetype_chart(decks, is_challenge=True)


def build_tournament_list_archetype_chart(fmt_slug: str, days: int = 30) -> dict | None:
    """Build 30-day Challenge Top 8 archetype breakdown for tournament list view and OG image."""
    cutoff, ref_date = get_timeframe_cutoff(days)
    tourns_qs = Tournament.objects.filter(
        format=fmt_slug,
        event_type="challenge",
        date__gte=cutoff,
        date__lte=ref_date,
    )
    decks = list(
        Deck.objects.filter(tournament__in=tourns_qs, is_top8=True).select_related(
            "tournament"
        )
    )
    if not decks:
        return None

    arch_map: dict[str, dict] = {}
    for d in decks:
        slug = d.archetype_slug or "unknown"
        if slug not in arch_map:
            arch_map[slug] = {
                "name": d.archetype,
                "slug": slug,
                "colors_list": [],
                "decks": [],
                "wins_count": 0,
            }
        arch_map[slug]["colors_list"].append((d.colors, d.color_name))
        arch_map[slug]["decks"].append(d)
        if d.rank == 1 or (d.result and "1st" in d.result.lower()):
            arch_map[slug]["wins_count"] += 1

    total_decks = len(decks)
    rows = []
    for slug, data in arch_map.items():
        d_list = data["decks"]
        d_list.sort(
            key=lambda x: (
                x.rank if x.rank is not None else 999,
                -(
                    x.tournament.date.toordinal()
                    if x.tournament and x.tournament.date
                    else 0
                ),
                x.player_lower,
            )
        )
        cnt = len(d_list)

        c_counts = Counter(c for c, _ in data["colors_list"] if c)
        best_colors = c_counts.most_common(1)[0][0] if c_counts else ""
        name_counts = Counter(n for _, n in data["colors_list"] if n)
        best_name = name_counts.most_common(1)[0][0] if name_counts else ""

        units = []
        for d in d_list:
            t_id = d.tournament.id if d.tournament else ""
            deck_idx = getattr(d, "deck_index", 1)
            if deck_idx > 1:
                deck_url = reverse(
                    "core:deck_detail_disambiguated",
                    kwargs={
                        "player": d.player,
                        "event": t_id,
                        "deck_index": deck_idx,
                    },
                )
            else:
                deck_url = reverse(
                    "core:deck_detail",
                    kwargs={"player": d.player, "event": t_id},
                )

            units.append(
                {
                    "rank": d.rank if (d.rank and 1 <= d.rank <= 8) else None,
                    "player": d.player,
                    "archetype_name": d.archetype,
                    "is_top8": True,
                    "is_win": (d.rank == 1 or (d.result and "1st" in d.result.lower())),
                    "tournament_id": t_id,
                    "tournament_name": d.tournament.name if d.tournament else "",
                    "tournament_date": d.tournament.date if d.tournament else None,
                    "deck_url": deck_url,
                    "id": d.id,
                }
            )

        share_pct = round((cnt / total_decks) * 100, 1) if total_decks > 0 else 0.0

        rows.append(
            {
                "name": data["name"],
                "slug": slug,
                "colors": best_colors,
                "color_name": best_name,
                "total_count": cnt,
                "wins_count": data["wins_count"],
                "share_pct": share_pct,
                "units": units,
            }
        )

    rows.sort(key=lambda r: (-r["total_count"], -r["wins_count"], r["name"].lower()))
    total_archetypes = len(rows)

    # Group archetypes with a single Top 8 and no 1st place finishes into "Others" at the bottom
    main_rows = []
    other_rows = []
    for r in rows:
        if r["total_count"] == 1 and r["wins_count"] == 0:
            other_rows.append(r)
        else:
            main_rows.append(r)

    if other_rows:
        other_units = []
        for r in other_rows:
            other_units.extend(r["units"])
        other_units.sort(
            key=lambda u: (
                u["rank"] if u["rank"] is not None else 999,
                u["player"].lower() if u["player"] else "",
            )
        )
        other_total = len(other_rows)
        other_share = (
            round((other_total / total_decks) * 100, 1) if total_decks > 0 else 0.0
        )
        main_rows.append(
            {
                "name": "Others",
                "slug": None,
                "colors": "",
                "color_name": "",
                "total_count": other_total,
                "wins_count": 0,
                "share_pct": other_share,
                "units": other_units,
                "is_others": True,
            }
        )

    return {
        "total_decks": total_decks,
        "total_tournaments": tourns_qs.count(),
        "total_archetypes": total_archetypes,
        "start_date": cutoff,
        "end_date": ref_date,
        "rows": main_rows,
    }


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


def build_format_bump_chart(fmt_slug: str, ref_date: date, step_y: int = 16) -> dict:
    """Build a 52-week Top 10 rank evolution bump chart for a format by T8 share."""
    monday_offset = ref_date.weekday()
    current_week_monday = ref_date - timedelta(days=monday_offset)
    total_weeks = 52
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
    STEP_Y = step_y
    x_start = X_OFFSET - 8
    x_end = X_OFFSET + (total_weeks - 1) * STEP_X + 8
    SVG_WIDTH = X_OFFSET + (total_weeks - 1) * STEP_X + 18  # 801
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
            "top_archetypes": [],
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

    total_t8_all = sum(overall_t8.values())
    top_archetypes = []
    for rank, (slug, count) in enumerate(overall_t8.most_common(5), start=1):
        share = round((count / total_t8_all) * 100, 1) if total_t8_all > 0 else 0.0
        name = slug_to_name.get(slug, slug)
        display_name = (name[:18] + "…") if len(name) > 19 else name
        top_archetypes.append(
            {
                "rank": rank,
                "slug": slug,
                "name": name,
                "display_name": display_name,
                "count": count,
                "count_formatted": f"{count:,}",
                "share": share,
                "color": color_map.get(slug, "#71717a"),
                "y_offset": (rank - 1) * 48,
            }
        )

    return {
        "has_data": has_data,
        "svg_width": SVG_WIDTH,
        "svg_height": SVG_HEIGHT,
        "x_start": x_start,
        "x_end": x_end,
        "month_labels": month_labels,
        "rank_lines": rank_lines,
        "tracks": tracks,
        "top_archetypes": top_archetypes,
    }


def get_activity_heatmap_date_range(
    ref_date: date, weeks_prior: int = 52
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
    aria_label: str = "52-week activity heatmap",
) -> dict:
    """Build 52 weeks + partial week (53 columns x 7 days, Monday-Sunday) activity heatmap SVG grid."""
    start_date, _, total_weeks = get_activity_heatmap_date_range(
        ref_date, weeks_prior=52
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
    """Build 52 weeks + partial week (53 columns x 7 days, Monday-Sunday) activity heatmap."""
    start_date, _, _ = get_activity_heatmap_date_range(ref_date, weeks_prior=52)

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
        aria_label="Archetype 52-week activity heatmap",
    )


def build_player_heatmap(
    player_lower: str, ref_date: date, player_name: str | None = None
) -> dict:
    """Build 52 weeks + partial week (53 columns x 7 days, Monday-Sunday) activity heatmap for a player across all formats."""
    start_date, _, _ = get_activity_heatmap_date_range(ref_date, weeks_prior=52)

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
        f"{player_name}'s 52-week activity heatmap"
        if player_name
        else "Player 52-week activity heatmap"
    )

    return build_activity_heatmap_grid(
        finishes_by_date=finishes_by_date,
        ref_date=ref_date,
        default_format=None,
        aria_label=aria_label,
    )


def _get_matrix_color_class(pct: float) -> str:
    """Return Tailwind styling based on 5 win-rate tiers.

    - > 60%: Deep green
    - 55% to 60%: Green
    - 45% to 55%: Neutral
    - 40% to 45%: Red
    - < 40%: Deep red
    """
    if pct > 60.0:
        return "bg-emerald-500/25 text-emerald-300 border border-emerald-500/35"
    elif pct > 55.0:
        return "bg-emerald-500/15 text-emerald-400 border border-emerald-500/20"
    elif pct < 40.0:
        return "bg-red-500/25 text-red-300 border border-red-500/35"
    elif pct < 45.0:
        return "bg-red-500/15 text-red-400 border border-red-500/20"
    else:
        return "bg-zinc-800/40 text-zinc-300 border border-zinc-700/30"


def get_archetype_matrix_data(
    format_slug: str,
    cutoff_date: date,
    ref_date: date,
    limit: int = 20,
) -> dict:
    """Fetch matches and compute head-to-head records and overall stats for top archetypes.

    Returns a dict containing:
        - has_data: bool
        - total_matches: int (count of valid matches in the timeframe)
        - sorted_slugs: list of archetype slugs sorted alphabetically
        - archetype_names: dict mapping slug to display name
        - matrix_stats: pairwise match and game records
        - overall_stats: aggregate records per archetype
    """
    tourns_qs = Tournament.objects.filter(
        format=format_slug,
        date__gte=cutoff_date,
        date__lte=ref_date,
    )

    matches = (
        Match.objects.filter(tournament__in=tourns_qs)
        .select_related("player1_deck", "player2_deck")
        .defer(
            "player1_deck__mainboard",
            "player1_deck__sideboard",
            "player1_deck__illegal_cards",
            "player2_deck__mainboard",
            "player2_deck__sideboard",
            "player2_deck__illegal_cards",
        )
    )

    archetype_match_counts = Counter()
    archetype_names = {}
    valid_matches = []
    has_ldcp_data = False

    for m in matches:
        if m.source == Match.SOURCE_LDCP:
            has_ldcp_data = True
        d1 = m.player1_deck
        d2 = m.player2_deck
        if not d1 or not d2:
            continue
        s1 = d1.archetype_slug
        s2 = d2.archetype_slug
        if not s1 or not s2:
            continue
        archetype_match_counts[s1] += 1
        archetype_match_counts[s2] += 1
        archetype_names[s1] = d1.archetype
        archetype_names[s2] = d2.archetype
        valid_matches.append((s1, s2, m.player1_wins, m.player2_wins, m.draws))

    if not valid_matches or not archetype_match_counts:
        return {
            "has_data": False,
            "has_ldcp_data": has_ldcp_data,
            "total_matches": 0,
            "sorted_slugs": [],
            "archetype_names": {},
            "matrix_stats": {},
            "overall_stats": {},
        }

    # Select top archetypes by match appearances in Top 8 playoffs
    top_slugs = sorted(
        archetype_match_counts.keys(),
        key=lambda s: (-archetype_match_counts[s], archetype_names.get(s, "").lower()),
    )[:limit]

    # Sort selected archetypes alphabetically by display name
    sorted_slugs = sorted(
        top_slugs,
        key=lambda s: archetype_names.get(s, s).lower(),
    )
    top_slugs_set = set(sorted_slugs)

    # Accumulate pairwise head-to-head match & game stats
    matrix_stats = {
        s1: {
            s2: {
                "matches_won": 0,
                "matches_lost": 0,
                "matches_drawn": 0,
                "total_matches": 0,
                "games_won": 0,
                "games_lost": 0,
                "games_drawn": 0,
                "total_games": 0,
            }
            for s2 in sorted_slugs
        }
        for s1 in sorted_slugs
    }

    # Overall totals per archetype across all matches in the timeframe
    overall_stats = {
        s: {
            "matches_won": 0,
            "matches_lost": 0,
            "matches_drawn": 0,
            "total_matches": 0,
            "games_won": 0,
            "games_lost": 0,
            "total_games": 0,
        }
        for s in sorted_slugs
    }

    for s1, s2, p1_w, p2_w, draws in valid_matches:
        if p1_w > p2_w:
            m1_won, m2_won = 1, 0
            m1_lost, m2_lost = 0, 1
            m_draw = 0
        elif p2_w > p1_w:
            m1_won, m2_won = 0, 1
            m1_lost, m2_lost = 1, 0
            m_draw = 0
        else:
            m1_won, m2_won = 0, 0
            m1_lost, m2_lost = 0, 0
            m_draw = 1

        # Track pairwise stats if both are in top archetypes
        if s1 in top_slugs_set and s2 in top_slugs_set:
            if s1 == s2:
                # Mirror match
                matrix_stats[s1][s2]["total_matches"] += 1
                matrix_stats[s1][s2]["total_games"] += p1_w + p2_w + draws
            else:
                st1 = matrix_stats[s1][s2]
                st1["matches_won"] += m1_won
                st1["matches_lost"] += m1_lost
                st1["matches_drawn"] += m_draw
                st1["total_matches"] += 1
                st1["games_won"] += p1_w
                st1["games_lost"] += p2_w
                st1["games_drawn"] += draws
                st1["total_games"] += p1_w + p2_w + draws

                st2 = matrix_stats[s2][s1]
                st2["matches_won"] += m2_won
                st2["matches_lost"] += m2_lost
                st2["matches_drawn"] += m_draw
                st2["total_matches"] += 1
                st2["games_won"] += p2_w
                st2["games_lost"] += p1_w
                st2["games_drawn"] += draws
                st2["total_games"] += p1_w + p2_w + draws

        # Update overall stats
        if s1 in top_slugs_set:
            ov1 = overall_stats[s1]
            ov1["matches_won"] += m1_won
            ov1["matches_lost"] += m1_lost
            ov1["matches_drawn"] += m_draw
            ov1["total_matches"] += 1
            ov1["games_won"] += p1_w
            ov1["games_lost"] += p2_w
            ov1["total_games"] += p1_w + p2_w

        if s2 in top_slugs_set:
            ov2 = overall_stats[s2]
            ov2["matches_won"] += m2_won
            ov2["matches_lost"] += m2_lost
            ov2["matches_drawn"] += m_draw
            ov2["total_matches"] += 1
            ov2["games_won"] += p2_w
            ov2["games_lost"] += p1_w
            ov2["total_games"] += p2_w + p1_w

    return {
        "has_data": True,
        "has_ldcp_data": has_ldcp_data,
        "total_matches": len(valid_matches),
        "sorted_slugs": sorted_slugs,
        "archetype_names": archetype_names,
        "matrix_stats": matrix_stats,
        "overall_stats": overall_stats,
    }
