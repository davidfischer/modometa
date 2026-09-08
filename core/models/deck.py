from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

from core.formats import Format
from core.formats import is_valid_format

from .tournament import Tournament


class Deck(models.Model):
    """A player's decklist submitted in an MTGO event."""

    id = models.CharField(max_length=255, primary_key=True, verbose_name="ID")
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name="decks"
    )
    format = models.CharField(max_length=32, choices=Format.choices)
    player = models.CharField(max_length=255)
    player_lower = models.CharField(max_length=255, db_index=True)
    result = models.CharField(max_length=64)
    rank = models.IntegerField(blank=True, null=True)
    is_top8 = models.BooleanField(default=False)
    is_5_0 = models.BooleanField(default=False, verbose_name="is 5-0")

    archetype = models.CharField(max_length=128)
    archetype_slug = models.CharField(max_length=128, db_index=True)
    is_auto_classified = models.BooleanField(
        default=False,
        help_text=(
            "Indicates whether this deck was assigned an archetype via heuristic "
            "fallback (such as color + posture) because it did not match any explicit "
            "archetype rules in the format's YAML definition."
        ),
    )

    colors = models.CharField(max_length=10)  # e.g. "UB", "UR", "C"
    color_name = models.CharField(
        max_length=32
    )  # e.g. "Dimir", "Mono-Red", "Colorless"

    mainboard = models.JSONField(default=list)  # list of {"card": "...", "count": 4}
    sideboard = models.JSONField(default=list)

    anchor_uri = models.URLField(
        max_length=512, blank=True, null=True, verbose_name="Anchor URI"
    )
    is_legal_today = models.BooleanField(default=True)
    illegal_cards = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-tournament__date", "rank", "id"]
        indexes = [
            models.Index(fields=["format", "archetype_slug"]),
            models.Index(fields=["format", "archetype_slug", "tournament"]),
            models.Index(fields=["format", "tournament"]),
            models.Index(fields=["format", "is_top8"]),
            models.Index(fields=["format", "is_top8", "tournament"]),
            models.Index(fields=["format", "is_5_0"]),
            models.Index(fields=["format", "is_5_0", "tournament"]),
            models.Index(fields=["format", "colors"]),
            models.Index(fields=["player_lower", "format"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(format__in=Format.values),
                name="core_deck_format_valid",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if not is_valid_format(self.format):
            raise ValidationError({"format": f"Unsupported format '{self.format}'."})

    def __str__(self) -> str:
        return f"{self.player} - {self.archetype} ({self.tournament_id})"

    @property
    def mainboard_card_count(self) -> int:
        return sum(item.get("count", 0) for item in self.mainboard)

    @property
    def sideboard_card_count(self) -> int:
        return sum(item.get("count", 0) for item in self.sideboard)

    @property
    def decklist_text(self) -> str:
        """Serialize deck to standard text representation."""
        lines = []
        for item in self.mainboard or []:
            card = item.get("card", "")
            count = item.get("count", 1)
            if card:
                lines.append(f"{count} {card}")
        if self.sideboard:
            if lines:
                lines.append("")
            lines.append("// Sideboard")
            for item in self.sideboard:
                card = item.get("card", "")
                count = item.get("count", 1)
                if card:
                    lines.append(f"{count} {card}")
        return "\n".join(lines)

    def to_text(self) -> str:
        """Alias for decklist_text."""
        return self.decklist_text

    def get_absolute_url(self) -> str:
        """Return the frontend deck detail URL."""
        if not self.tournament_id or not self.player:
            return ""

        if getattr(self, "deck_index", None) and self.deck_index > 1:
            return reverse(
                "core:deck_detail_disambiguated",
                kwargs={
                    "player": self.player,
                    "event": self.tournament_id,
                    "deck_index": self.deck_index,
                },
            )

        if self.pk:
            player_lower = self.player_lower or self.player.strip().lower()
            sibling_ids = list(
                Deck.objects.filter(
                    tournament_id=self.tournament_id,
                    player_lower=player_lower,
                )
                .order_by("id")
                .values_list("id", flat=True)
            )
            if len(sibling_ids) > 1 and self.id in sibling_ids:
                idx = sibling_ids.index(self.id) + 1
                if idx > 1:
                    return reverse(
                        "core:deck_detail_disambiguated",
                        kwargs={
                            "player": self.player,
                            "event": self.tournament_id,
                            "deck_index": idx,
                        },
                    )

        return reverse(
            "core:deck_detail",
            kwargs={"player": self.player, "event": self.tournament_id},
        )
