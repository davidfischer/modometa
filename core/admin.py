from datetime import date
from datetime import timedelta

from django.contrib import admin
from django.db import models
from django.utils.html import format_html

from .models import Card
from .models import CardLookup
from .models import Deck
from .models import Tournament


class TimeframeFilter(admin.SimpleListFilter):
    title = "Timeframe"
    parameter_name = "timeframe"

    def lookups(self, request, model_admin):
        return (
            ("30", "Last 30 days"),
            ("90", "Last 90 days"),
        )

    def queryset(self, request, queryset):
        val = self.value()
        if val in ("30", "90"):
            days = int(val)
            latest = Tournament.objects.aggregate(max_d=models.Max("date"))["max_d"]
            ref = latest or date.today()
            cutoff = ref - timedelta(days=days)
            date_field = (
                "tournament__date" if hasattr(queryset.model, "tournament") else "date"
            )
            return queryset.filter(
                **{f"{date_field}__gte": cutoff, f"{date_field}__lte": ref}
            )
        return queryset


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("name", "mana_cost", "cmc", "type_line", "is_land")
    search_fields = ("name", "normalized_name")
    list_filter = ("is_land", "is_basic_land")
    readonly_fields = ("external_links",)

    @admin.display(description="External Links")
    def external_links(self, obj: Card | None) -> str:
        if not obj or (not obj.pk and not obj.name):
            return "-"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">Scryfall ↗</a>'
            "&nbsp;&nbsp;|&nbsp;&nbsp;"
            '<a href="{}" target="_blank" rel="noopener noreferrer">Gatherer ↗</a>',
            obj.scryfall_url,
            obj.gatherer_url,
        )


@admin.register(CardLookup)
class CardLookupAdmin(admin.ModelAdmin):
    list_display = ("lookup_name", "canonical_name", "priority")
    search_fields = ("lookup_name", "canonical_name")
    raw_id_fields = ("card",)


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = ("name", "format", "event_type", "date", "deck_count")
    list_filter = (TimeframeFilter, "format", "event_type")
    search_fields = ("name", "id")
    ordering = ("-date", "id")
    date_hierarchy = "date"


@admin.register(Deck)
class DeckAdmin(admin.ModelAdmin):
    list_display = (
        "player",
        "archetype",
        "color_name",
        "format",
        "result",
        "is_top8",
        "is_5_0",
        "tournament",
    )
    list_filter = (
        TimeframeFilter,
        "format",
        "is_top8",
        "is_5_0",
        "is_auto_classified",
        "colors",
        "is_legal_today",
    )
    search_fields = ("player", "archetype", "tournament__name", "tournament__id")
    raw_id_fields = ("tournament",)
    ordering = ("-tournament__date", "rank", "id")
    date_hierarchy = "tournament__date"
    readonly_fields = ("decklist",)

    @admin.display(description="Decklist")
    def decklist(self, obj: Deck | None) -> str:
        if not obj or (not obj.mainboard and not obj.sideboard):
            return "-"
        return format_html(
            '<pre style="white-space: pre-wrap; font-family: ui-monospace, monospace; '
            "font-size: 13px; line-height: 1.5; max-height: 500px; overflow-y: auto; "
            "padding: 10px 14px; border-radius: 6px; border: 1px solid var(--hairline-color, #ccc); "
            'background: var(--darkened-bg, #f8f9fa); color: var(--body-fg, #333); margin: 0;">{}</pre>',
            obj.decklist_text,
        )
