"""MTGO Tournament Match model."""

import re

from django.db import models
from django.utils.text import slugify

from .deck import Deck
from .tournament import Tournament


def normalize_round_slug(round_name: str) -> str:
    """Normalize a round name to a slug (e.g. 'Quarterfinals' -> 'quarterfinals', 'Round 1' -> 'round_01')."""
    if not round_name:
        return "round"
    clean = round_name.strip()
    match = re.match(r"(?i)^round\s*(\d+)$", clean)
    if match:
        num = int(match.group(1))
        return f"round_{num:02d}"
    return slugify(clean).replace("-", "_")


class Match(models.Model):
    """A match between two players in an MTGO tournament."""

    id = models.CharField(max_length=255, primary_key=True, verbose_name="ID")
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name="matches"
    )
    round_name = models.CharField(max_length=64)
    round_slug = models.CharField(max_length=64, db_index=True)

    player1 = models.CharField(max_length=255)
    player2 = models.CharField(max_length=255)
    player1_deck = models.ForeignKey(
        Deck,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matches_as_p1",
    )
    player2_deck = models.ForeignKey(
        Deck,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matches_as_p2",
    )

    player1_wins = models.IntegerField(default=0)
    player2_wins = models.IntegerField(default=0)
    draws = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-tournament__date", "round_slug", "id"]
        indexes = [
            models.Index(fields=["tournament", "round_slug"]),
        ]
        verbose_name = "Match"
        verbose_name_plural = "Matches"

    def __str__(self) -> str:
        return f"{self.tournament_id}: {self.player1} ({self.player1_wins}) vs {self.player2} ({self.player2_wins})"

    @property
    def total_games(self) -> int:
        return self.player1_wins + self.player2_wins + self.draws
