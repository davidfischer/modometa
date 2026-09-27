from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

from core.formats import Format
from core.formats import is_valid_format


class Tournament(models.Model):
    """MTGO Tournament event."""

    id = models.CharField(
        max_length=255, primary_key=True, verbose_name="ID"
    )  # Filename stem
    name = models.CharField(max_length=255)
    format = models.CharField(max_length=32, choices=Format.choices)
    event_type = models.CharField(max_length=32)  # challenge, league, etc.
    date = models.DateField(db_index=True)
    uri = models.URLField(max_length=512, blank=True, null=True, verbose_name="URI")
    deck_count = models.IntegerField(default=0)
    player_count = models.IntegerField(blank=True, null=True)
    # Standings from the official tournament results
    # Wins/Losses include top8 results
    standings = models.JSONField(default=list, blank=True)
    # A Google Sheet ID typically for the Legacy Data Collection Project
    # or the Vintage MTGO Community
    community_sheet_id = models.CharField(
        max_length=128, blank=True, null=True, verbose_name="Community Sheet ID"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "id"]
        indexes = [
            models.Index(fields=["format", "-date"]),
            models.Index(fields=["event_type", "-date"]),
            models.Index(fields=["format", "event_type", "-date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(format__in=Format.values),
                name="core_tournament_format_valid",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if not is_valid_format(self.format):
            raise ValidationError({"format": f"Unsupported format '{self.format}'."})

    def __str__(self) -> str:
        return f"{self.name} ({self.date})"

    def get_absolute_url(self) -> str:
        """Return the frontend tournament detail URL."""
        if not self.format or not self.id:
            return ""
        return reverse(
            "core:tournament_detail",
            kwargs={"format": self.format, "event": self.id},
        )

    @property
    def community_sheet_url(self) -> str | None:
        """Return the Google Sheets edit URL for the community data sheet."""
        if not self.community_sheet_id:
            return None
        return f"https://docs.google.com/spreadsheets/d/{self.community_sheet_id}/edit"

    @property
    def community_source_name(self) -> str | None:
        """Return the name of the community project providing match data."""
        if not self.community_sheet_id:
            return None
        if self.format == "legacy":
            return "Legacy Data Collection Project"
        if self.format == "vintage":
            return "Vintage MTGO Community"
        return "Community Data"
