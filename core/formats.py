"""Central format definitions and metadata for Modometa.

Single source of truth for all supported formats, display names,
autodetection specificity hierarchy, and deck construction constraints.
"""

from dataclasses import dataclass

from django.db import models


@dataclass(frozen=True)
class FormatInfo:
    """Configuration and metadata for a supported MTG format."""

    slug: str
    name: str
    specificity: int  # Higher value indicates more restrictive card pool (used in format autodetection)
    min_mainboard: int = 60
    max_sideboard: int = 15
    max_copies: int = 4
    scryfall_key: str = ""
    description: str = ""

    def __post_init__(self):
        if not self.scryfall_key:
            object.__setattr__(self, "scryfall_key", self.slug)


# Specificity hierarchy: more restrictive card pools receive higher base weight
# in autodetection (Standard > Pioneer > Pauper > Modern > Premodern > Legacy > Vintage)
FORMAT_SPECS: list[FormatInfo] = [
    FormatInfo(
        slug="standard",
        name="Standard",
        specificity=100,
        description="Rotating 60-card format spanning recent premier sets.",
    ),
    FormatInfo(
        slug="pioneer",
        name="Pioneer",
        specificity=80,
        description="Non-rotating format starting from Return to Ravnica (2012) forward.",
    ),
    FormatInfo(
        slug="pauper",
        name="Pauper",
        specificity=70,
        description="All commons across MTG history.",
    ),
    FormatInfo(
        slug="modern",
        name="Modern",
        specificity=60,
        description="Non-rotating format from Eighth Edition (2003) forward, plus Modern Horizons.",
    ),
    FormatInfo(
        slug="premodern",
        name="Premodern",
        specificity=50,
        description="Sets from Fourth Edition (1995) to Scourge (2003).",
    ),
    FormatInfo(
        slug="legacy",
        name="Legacy",
        specificity=30,
        description="Eternal format allowing cards from all sets, with a curated banned list.",
    ),
    FormatInfo(
        slug="vintage",
        name="Vintage",
        specificity=10,
        description="Eternal format allowing cards from all sets, using a restricted list instead of bans.",
    ),
]

FORMATS: dict[str, FormatInfo] = {fmt.slug: fmt for fmt in FORMAT_SPECS}


class Format(models.TextChoices):
    """Supported MTG formats in Modometa."""

    STANDARD = "standard", "Standard"
    PIONEER = "pioneer", "Pioneer"
    PAUPER = "pauper", "Pauper"
    MODERN = "modern", "Modern"
    PREMODERN = "premodern", "Premodern"
    LEGACY = "legacy", "Legacy"
    VINTAGE = "vintage", "Vintage"


# Ordered list of canonical slugs (Standard down to Vintage for specificity testing)
FORMAT_SLUGS: list[str] = list(Format.values)

# Format choices for Django model fields
FORMAT_CHOICES: list[tuple[str, str]] = list(Format.choices)

# Slug to display name mapping
FORMAT_NAMES: dict[str, str] = {fmt.slug: fmt.name for fmt in FORMAT_SPECS}

# Slug to specificity score mapping
FORMAT_SPECIFICITY: dict[str, int] = {fmt.slug: fmt.specificity for fmt in FORMAT_SPECS}


def get_format(slug: str) -> FormatInfo | None:
    """Retrieve format info by slug."""
    return FORMATS.get(slug.lower()) if slug else None


def is_valid_format(slug: str) -> bool:
    """Check if a format slug is supported."""
    return bool(slug and slug.lower() in Format.values)
