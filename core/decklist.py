"""Utility for parsing text decklists into structured card entries."""

import re
from pathlib import Path


def parse_text_decklist(
    source: str | list[str] | Path,
) -> tuple[list[dict], list[dict]]:
    """Parse text decklist lines into mainboard and sideboard items.

    Args:
        source: Multi-line string, list of line strings, or a Path to a deck file.

    Returns:
        tuple of (mainboard, sideboard), each a list of dicts: {"card": str, "count": int}.
    """
    if isinstance(source, Path):
        lines = source.read_text(encoding="utf-8").splitlines()
    elif isinstance(source, str):
        # Check if source is an existing file path string
        p = Path(source)
        if p.is_file():
            lines = p.read_text(encoding="utf-8").splitlines()
        else:
            lines = source.splitlines()
    else:
        lines = list(source)

    mainboard: list[dict] = []
    sideboard: list[dict] = []
    is_sideboard = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        lower = line.lower()

        # Sideboard section headers
        if (
            lower.startswith("sideboard")
            or lower == "sb:"
            or lower.startswith("// sideboard")
            or lower.startswith("//sideboard")
        ):
            is_sideboard = True
            continue

        # Ignore comments
        if line.startswith("//") or line.startswith("#"):
            continue

        is_sideboard_entry = is_sideboard
        if lower.startswith("sb:"):
            is_sideboard_entry = True
            line = line[3:].strip()

        # Match count and card name: e.g. "4 Brainstorm" or "1 Black Lotus"
        match = re.match(r"^(\d+)x?\s+(.*)$", line)
        if match:
            cnt = int(match.group(1))
            c_name = match.group(2).strip()
        else:
            cnt = 1
            c_name = line

        # Strip set codes in parentheses e.g. "Brainstorm (EMA) 40" or "Brainstorm [EMA]"
        c_name = re.sub(r"\s*[\(\[][A-Z0-9_]+[\)\]]\s*(\d+)?.*$", "", c_name).strip()

        if not c_name:
            continue

        entry = {"card": c_name, "count": cnt}
        if is_sideboard_entry:
            sideboard.append(entry)
        else:
            mainboard.append(entry)

    return mainboard, sideboard
