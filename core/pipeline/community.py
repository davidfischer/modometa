"""Ingestion pipeline for Legacy Data Collection Project (LDCP) community match data."""

import json
import logging
import re
from pathlib import Path
from typing import Any

from django.db import transaction

from core.models.deck import Deck
from core.models.match import Match
from core.models.match import normalize_round_slug
from core.models.tournament import Tournament
from core.pipeline.ingest import parse_match_result


logger = logging.getLogger(__name__)


class CommunityIngestionResult:
    """Summary metrics of a community data ingestion run."""

    def __init__(self) -> None:
        self.tournaments_scanned: int = 0
        self.tournaments_ingested: int = 0
        self.tournaments_skipped_existing: int = 0
        self.tournaments_skipped_not_found: int = 0
        self.matches_saved: int = 0
        self.matches_skipped_no_deck: int = 0
        self.top8_matches_checked: int = 0
        self.warnings: list[str] = []
        self.disagreements: list[str] = []


class CommunityIngestionPipeline:
    """Processes LDCP JSON files and ingests Swiss matches into SQLite."""

    def __init__(self) -> None:
        pass

    def find_files(self, base_dir: Path) -> list[Path]:
        """Find all tournament JSON files under the directory."""
        if base_dir.is_file():
            return [base_dir]

        target_dir = base_dir
        sub_ldcp = base_dir / "datasources" / "legacy-data-collection"
        if sub_ldcp.is_dir():
            target_dir = sub_ldcp

        candidates = list(target_dir.rglob("*.json"))
        tournament_files = []
        for p in candidates:
            # Skip hidden directories, virtual environments, caches
            parts = p.parts
            if any(
                part.startswith(".") or part in ("venv", ".venv", "site-packages")
                for part in parts
            ):
                continue
            tournament_files.append(p)

        return sorted(tournament_files)

    def ingest_file(
        self,
        file_path: Path,
        force: bool = False,
    ) -> tuple[int, int, list[str], list[str]]:
        """Ingest a single LDCP tournament JSON file.

        Returns:
            tuple: (matches_saved, matches_skipped_no_deck, warnings, disagreements)
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
        except Exception as exc:
            logger.error("Failed to read JSON from %s: %s", file_path, exc)
            return 0, 0, [f"Failed to read {file_path}: {exc}"], []

        t_meta = data.get("Tournament") or {}
        event_id = (t_meta.get("Id") or file_path.stem).strip()

        tournament = Tournament.objects.filter(id=event_id).first()
        if not tournament:
            warning_msg = f"Tournament {event_id} has no official tournament data in database; skipping."
            logger.warning(warning_msg)
            return 0, 0, [warning_msg], []

        decks = list(tournament.decks.all())
        if not decks:
            warning_msg = f"Tournament {event_id} has no official deck data in database; skipping."
            logger.warning(warning_msg)
            return 0, 0, [warning_msg], []

        has_existing_ldcp = Match.objects.filter(
            tournament=tournament, source=Match.SOURCE_LDCP
        ).exists()
        if has_existing_ldcp and not force:
            logger.debug(
                "Tournament %s already has LDCP matches and force=False; skipping.",
                event_id,
            )
            return 0, 0, [], []

        player_deck_map: dict[str, Deck] = {
            d.player.strip().lower(): d for d in decks if d.player
        }

        official_matches = list(tournament.matches.filter(source=Match.SOURCE_MTGO))

        matches_to_save: list[Match] = []
        seen_match_ids: set[str] = set()
        matches_skipped_no_deck = 0
        disagreements: list[str] = []

        raw_rounds = data.get("Rounds") or []

        for round_idx, round_info in enumerate(raw_rounds):
            round_name = (
                round_info.get("RoundName") or f"Round {round_idx + 1}"
            ).strip()
            round_type = (round_info.get("RoundType") or "").strip()
            round_slug = normalize_round_slug(round_name)
            is_top8 = round_type.lower() == "top8" or round_slug in (
                "quarterfinals",
                "semifinals",
                "finals",
            )

            round_matches = round_info.get("Matches") or []

            if is_top8:
                # Validate against official Top 8 matches; do NOT double-ingest
                if official_matches:
                    for m_data in round_matches:
                        p1 = (m_data.get("Player1") or "").strip()
                        p2 = (m_data.get("Player2") or "").strip()
                        if not p1 or not p2:
                            continue
                        res = (m_data.get("Result") or "").strip()
                        p1_w, p2_w, draws = parse_match_result(res)

                        matched_official = None
                        matched_reversed = False
                        for om in official_matches:
                            op1 = om.player1.strip().lower()
                            op2 = om.player2.strip().lower()
                            if op1 == p1.lower() and op2 == p2.lower():
                                matched_official = om
                                matched_reversed = False
                                break
                            elif op1 == p2.lower() and op2 == p1.lower():
                                matched_official = om
                                matched_reversed = True
                                break

                        if not matched_official:
                            disagreement_msg = (
                                f"Top 8 pairing disagreement in {event_id} ({round_name}): "
                                f"LDCP has {p1} vs {p2} ({res}), not found in official MTGO matches."
                            )
                            logger.warning(disagreement_msg)
                            disagreements.append(disagreement_msg)
                        else:
                            if not matched_reversed:
                                expected = (
                                    matched_official.player1_wins,
                                    matched_official.player2_wins,
                                    matched_official.draws,
                                )
                            else:
                                expected = (
                                    matched_official.player2_wins,
                                    matched_official.player1_wins,
                                    matched_official.draws,
                                )
                            actual = (p1_w, p2_w, draws)
                            if expected != actual:
                                disagreement_msg = (
                                    f"Top 8 score disagreement in {event_id} ({round_name}) between {p1} and {p2}: "
                                    f"LDCP reports {p1_w}-{p2_w}-{draws}, official MTGO reports {expected[0]}-{expected[1]}-{expected[2]}."
                                )
                                logger.warning(disagreement_msg)
                                disagreements.append(disagreement_msg)
                # Skip saving Top 8 matches to avoid double-ingesting
                continue

            # Swiss round: only ingest matchups where BOTH players have a deck from mtgo.com
            for match_idx, m_data in enumerate(round_matches):
                p1 = (m_data.get("Player1") or "").strip()
                p2 = (m_data.get("Player2") or "").strip()
                if not p1 or not p2:
                    continue

                d1 = player_deck_map.get(p1.lower())
                d2 = player_deck_map.get(p2.lower())
                if not d1 or not d2:
                    matches_skipped_no_deck += 1
                    continue

                match_id = f"{event_id}_{round_slug}_{match_idx}"
                if match_id in seen_match_ids:
                    continue
                seen_match_ids.add(match_id)

                res = (m_data.get("Result") or "").strip()
                p1_w, p2_w, draws = parse_match_result(res)

                matches_to_save.append(
                    Match(
                        id=match_id,
                        tournament=tournament,
                        round_name=round_name,
                        round_slug=round_slug,
                        player1=p1,
                        player2=p2,
                        player1_deck=d1,
                        player2_deck=d2,
                        player1_wins=p1_w,
                        player2_wins=p2_w,
                        draws=draws,
                        source=Match.SOURCE_LDCP,
                    )
                )

        if matches_to_save:
            with transaction.atomic():
                if force:
                    Match.objects.filter(
                        tournament=tournament, source=Match.SOURCE_LDCP
                    ).delete()
                Match.objects.bulk_create(matches_to_save, ignore_conflicts=True)

        return len(matches_to_save), matches_skipped_no_deck, [], disagreements

    def ingest_files(
        self,
        files: list[Path],
        force: bool = False,
        limit: int | None = None,
        since: str | None = None,
    ) -> CommunityIngestionResult:
        """Ingest multiple LDCP JSON files."""
        result = CommunityIngestionResult()

        target_files = files
        if since:
            filtered = []
            for f in target_files:
                # LDCP filename format contains date: e.g. legacy-challenge-32-YYYY-MM-DD...
                # or we check date inside JSON / tournament
                stem = f.stem
                # Check for YYYY-MM-DD pattern
                match = re.search(r"\d{4}-\d{2}-\d{2}", stem)
                if match:
                    if match.group(0) >= since:
                        filtered.append(f)
                else:
                    filtered.append(f)
            target_files = filtered

        if limit:
            target_files = target_files[:limit]

        for file_path in target_files:
            result.tournaments_scanned += 1
            saved, skipped, warnings, disagreements = self.ingest_file(
                file_path, force=force
            )
            result.warnings.extend(warnings)
            result.disagreements.extend(disagreements)
            result.matches_saved += saved
            result.matches_skipped_no_deck += skipped
            if warnings:
                result.tournaments_skipped_not_found += 1
            elif saved > 0:
                result.tournaments_ingested += 1
            elif not force:
                result.tournaments_skipped_existing += 1

        return result
