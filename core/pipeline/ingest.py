"""MTGO Tournament and Decklist ingestion pipeline."""

import glob
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils.text import slugify
from tqdm import tqdm

from core.formats import Format
from core.formats import is_valid_format
from core.models.card import Card
from core.models.deck import Deck
from core.models.tournament import Tournament
from core.rules.engine import ArchetypeEngine
from core.rules.legality import LegalityEngine


logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = list(Format.values)


def parse_date(date_str: Any) -> datetime.date | None:
    """Parse date string into a datetime.date object."""
    if not date_str:
        return None
    s = str(date_str).strip()
    if not s:
        return None
    if "T" in s:
        s = s.split("T")[0]
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _consolidate_deck_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Consolidate deck entries with duplicate card names by summing their counts."""
    counts: dict[str, int] = {}
    for item in entries:
        card = item["card"]
        counts[card] = counts.get(card, 0) + int(item.get("count", 1))
    return [{"card": card, "count": cnt} for card, cnt in counts.items()]


def deduce_format_and_type(
    filename: str, tournament_meta: dict[str, Any]
) -> tuple[str | None, str]:
    """Deduce canonical format slug and event type from filename and metadata."""
    fname_lower = filename.lower()

    # Reject non-constructed / limited / unsupported formats explicitly
    if any(
        k in fname_lower
        for k in (
            "draft",
            "sealed",
            "limited",
            "cube",
            "contraption",
            "commander",
            "brawl",
        )
    ):
        return None, "unsupported"

    # 1. Match Format
    fmt_slug = None
    for slug in SUPPORTED_FORMATS:
        if fname_lower.startswith(slug):
            fmt_slug = slug
            break

    if not fmt_slug:
        meta_fmt = str(
            tournament_meta.get("Formats") or tournament_meta.get("Name") or ""
        ).lower()
        if any(
            k in meta_fmt
            for k in (
                "draft",
                "sealed",
                "limited",
                "cube",
                "contraption",
                "commander",
                "brawl",
            )
        ):
            return None, "unsupported"
        for slug in SUPPORTED_FORMATS:
            if slug in meta_fmt:
                fmt_slug = slug
                break

    if not fmt_slug or not is_valid_format(fmt_slug):
        return None, "unsupported"

    # 2. Match Event Type
    if "challenge" in fname_lower:
        event_type = "challenge"
    elif "league" in fname_lower:
        event_type = "league"
    elif "preliminary" in fname_lower:
        event_type = "preliminary"
    elif "showcase" in fname_lower:
        event_type = "challenge"  # Showcase challenges
    elif "qualifier" in fname_lower:
        event_type = "challenge"
    else:
        event_type = "other"

    return fmt_slug, event_type


def parse_result_and_rank(result_str: str) -> tuple[int | None, bool, bool]:
    """Parse Result string (e.g. '1st Place', '5-0') into (rank, is_top8, is_5_0)."""
    if not result_str:
        return None, False, False

    s = result_str.strip().lower()
    is_5_0 = "5-0" in s

    # Check place: e.g. "1st place", "8th place", "32nd place"
    match = re.search(r"(\d+)(?:st|nd|rd|th)\s+place", s)
    if match:
        rank = int(match.group(1))
        is_top8 = 1 <= rank <= 8
        return rank, is_top8, is_5_0

    if "top 8" in s or "top8" in s:
        return 8, True, is_5_0

    return None, False, is_5_0


class IngestionPipeline:
    """Processes tournament JSON files and bulk saves to SQLite."""

    def __init__(self, archetypes_dir: Path | None = None):
        self.archetype_engine = ArchetypeEngine(archetypes_dir)
        self.legality_engine = LegalityEngine()
        self.rejected_unsupported_count = 0
        # Preload card colors for fast color deduction
        self._card_colors: dict[str, list[str]] = dict(
            Card.objects.exclude(colors=[]).values_list("normalized_name", "colors")
        )

    def find_tournament_files(
        self,
        base_dir: Path,
        format_filter: str | None = None,
        since_date: datetime.date | None = None,
    ) -> list[Path]:
        """Find tournament JSON files matching criteria."""
        base_dir = Path(base_dir)
        pattern = str(base_dir / "**" / "*.json")
        all_files = glob.glob(pattern, recursive=True)
        matched = []

        for p_str in all_files:
            p = Path(p_str)
            fname = p.name.lower()
            if format_filter and not fname.startswith(format_filter.lower()):
                continue

            if since_date:
                match = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
                if match:
                    try:
                        file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
                        if file_date < since_date:
                            continue
                    except ValueError:
                        pass

            matched.append(p)

        matched.sort()
        return matched

    def _flush_batch(
        self,
        tournaments: list[Tournament],
        decks: list[Deck],
        replace_existing: bool = False,
    ) -> None:
        """Commit batch of tournaments and decks inside a transaction."""
        with transaction.atomic():
            if replace_existing and tournaments:
                tourn_ids = [t.id for t in tournaments]
                Tournament.objects.filter(id__in=tourn_ids).delete()
            if tournaments:
                Tournament.objects.bulk_create(tournaments, ignore_conflicts=True)
            if decks:
                Deck.objects.bulk_create(decks, ignore_conflicts=True)

    @property
    def unmatched_cards(self) -> list[str]:
        """List of all card names that could not be resolved in Scryfall."""
        return sorted(list(self.legality_engine.unmatched_cards))

    def ingest_files(
        self,
        files: list[Path],
        batch_size: int = 1500,
        limit: int | None = None,
        force: bool = False,
        show_unmatched: bool = False,
        replace_existing: bool = False,
        since_date: datetime.date | None = None,
    ) -> tuple[int, int]:
        """Parse and ingest tournament files into database."""
        if limit:
            files = files[:limit]

        if force:
            replace_existing = True

        if not force and not replace_existing:
            existing_ids = set(Tournament.objects.values_list("id", flat=True))
            unprocessed = [f for f in files if f.stem not in existing_ids]
            skipped = len(files) - len(unprocessed)
            if skipped:
                logger.info(
                    "Skipping %d already ingested tournaments (pass --force to re-process).",
                    skipped,
                )
            files = unprocessed

        if not files:
            logger.info("No new tournament files to ingest.")
            return 0, 0

        tournaments_to_save: list[Tournament] = []
        decks_to_save: list[Deck] = []
        seen_tournaments: set[str] = set()
        seen_deck_ids: set[str] = set()
        reported_unmatched: set[str] = set()
        total_tournaments_saved = 0
        total_decks_saved = 0

        logger.info("Parsing %d tournament files...", len(files))

        pbar = tqdm(
            files,
            desc="Parsing MTGO tournaments",
            unit="tourney",
            mininterval=0.2,
        )
        for fpath in pbar:
            fname = fpath.name
            fname_padded = (fname if len(fname) <= 50 else fname[:47] + "...").ljust(50)
            pbar.set_description(
                f"Parsing MTGO tournaments: {fname_padded}", refresh=False
            )
            try:
                with open(fpath, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
            except Exception as e:
                logger.debug("Failed to read %s: %s", fpath, e)
                continue

            tourn_meta = data.get("Tournament") or {}
            filename = fpath.name
            event_id = fpath.stem

            fmt_slug, event_type = deduce_format_and_type(filename, tourn_meta)
            if not fmt_slug or fmt_slug not in SUPPORTED_FORMATS:
                self.rejected_unsupported_count += 1
                logger.debug("Rejected tournament %s: unsupported format", filename)
                continue

            event_date = parse_date(tourn_meta.get("Date"))
            if not event_date:
                continue

            if since_date and event_date < since_date:
                continue

            tourn_name = tourn_meta.get("Name") or event_id
            tourn_uri = tourn_meta.get("Uri")
            raw_decks = data.get("Decks") or []

            if event_id not in seen_tournaments:
                seen_tournaments.add(event_id)
                tournaments_to_save.append(
                    Tournament(
                        id=event_id,
                        name=tourn_name,
                        format=fmt_slug,
                        event_type=event_type,
                        date=event_date,
                        uri=tourn_uri,
                        deck_count=len(raw_decks),
                    )
                )

            # Player index tracker for multiple decks in same dump
            player_counts: dict[str, int] = {}

            for deck_data in raw_decks:
                player = deck_data.get("Player")
                if not player:
                    continue

                player_clean = player.strip()
                player_lower = player_clean.lower()
                player_counts[player_lower] = player_counts.get(player_lower, 0) + 1
                deck_index = player_counts[player_lower]

                deck_id = f"{event_id}_{slugify(player_lower)}_{deck_index}"
                if deck_id in seen_deck_ids:
                    continue
                seen_deck_ids.add(deck_id)

                result = deck_data.get("Result") or ""
                rank, is_top8, is_5_0 = parse_result_and_rank(result)
                anchor_uri = deck_data.get("AnchorUri")

                # Format mainboard and sideboard with canonical card names
                mainboard_raw = []
                for card_entry in deck_data.get("Mainboard") or []:
                    c_name = card_entry.get("CardName") or card_entry.get("name")
                    c_cnt = card_entry.get("Count") or card_entry.get("count") or 1
                    if c_name:
                        resolved = self.legality_engine.resolve_card(c_name)
                        canonical = resolved.name if resolved else c_name.strip()
                        mainboard_raw.append({"card": canonical, "count": int(c_cnt)})

                sideboard_raw = []
                for card_entry in deck_data.get("Sideboard") or []:
                    c_name = card_entry.get("CardName") or card_entry.get("name")
                    c_cnt = card_entry.get("Count") or card_entry.get("count") or 1
                    if c_name:
                        resolved = self.legality_engine.resolve_card(c_name)
                        canonical = resolved.name if resolved else c_name.strip()
                        sideboard_raw.append({"card": canonical, "count": int(c_cnt)})

                # Consolidate copies of the same canonical card
                mainboard = _consolidate_deck_entries(mainboard_raw)
                sideboard = _consolidate_deck_entries(sideboard_raw)

                # Classify Archetype and Colors
                (
                    arch_name,
                    arch_slug,
                    colors_code,
                    color_name,
                    is_fallback,
                    _,
                ) = self.archetype_engine.classify(
                    mainboard,
                    fmt_slug,
                    card_colors_map=self._card_colors,
                    sideboard_cards=sideboard,
                )

                # Validate legality today
                is_legal, _, illegal_cards = self.legality_engine.validate_deck(
                    mainboard, sideboard, fmt_slug
                )

                if show_unmatched:
                    raw_entries = (deck_data.get("Mainboard") or []) + (
                        deck_data.get("Sideboard") or []
                    )
                    for card_entry in raw_entries:
                        raw_name = (
                            card_entry.get("CardName") or card_entry.get("name") or ""
                        ).strip()
                        if (
                            raw_name in self.legality_engine.unmatched_cards
                            and raw_name not in reported_unmatched
                        ):
                            reported_unmatched.add(raw_name)
                            pbar.write(
                                f"  [Unmatched card] '{raw_name}' (first seen in {filename})"
                            )

                decks_to_save.append(
                    Deck(
                        id=deck_id,
                        tournament_id=event_id,
                        format=fmt_slug,
                        player=player_clean,
                        player_lower=player_lower,
                        result=result,
                        rank=rank,
                        is_top8=is_top8,
                        is_5_0=is_5_0,
                        archetype=arch_name,
                        archetype_slug=arch_slug,
                        is_auto_classified=is_fallback,
                        colors=colors_code,
                        color_name=color_name,
                        mainboard=mainboard,
                        sideboard=sideboard,
                        anchor_uri=anchor_uri,
                        is_legal_today=is_legal,
                        illegal_cards=illegal_cards,
                    )
                )

            # Flush in batches to keep RAM usage constant and save incrementally
            if len(decks_to_save) >= batch_size:
                self._flush_batch(
                    tournaments_to_save,
                    decks_to_save,
                    replace_existing=replace_existing,
                )
                total_tournaments_saved += len(tournaments_to_save)
                total_decks_saved += len(decks_to_save)
                tournaments_to_save.clear()
                decks_to_save.clear()

        # Final flush for remaining items
        if tournaments_to_save or decks_to_save:
            self._flush_batch(
                tournaments_to_save,
                decks_to_save,
                replace_existing=replace_existing,
            )
            total_tournaments_saved += len(tournaments_to_save)
            total_decks_saved += len(decks_to_save)
            tournaments_to_save.clear()
            decks_to_save.clear()

        logger.info(
            "Ingestion complete. Saved %d tournaments and %d decks.",
            total_tournaments_saved,
            total_decks_saved,
        )
        return total_tournaments_saved, total_decks_saved
