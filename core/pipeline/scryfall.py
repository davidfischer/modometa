"""Scryfall bulk ingestion pipeline.

Why we use 'default_cards' instead of 'oracle_cards':
------------------------------------------------------
Scryfall's 'oracle_cards' bulk data file contains strictly one card object per
Oracle ID, and its selection algorithm strongly favors the primary paper set
printing (e.g. Marvel's Spider-Man for 'Hide on the Ceiling'). This causes it to
completely omit digital-only printings such as MTGO's "Through the Omenpaths"
(set code 'om1'), where alternate MTGO card names live (e.g. 'Spectral Restitching',
'Kraza, the Swarm as One'), as well as Universes Within prints ('sld') and
various promotional/flavor names.

By using 'default_cards' (all English printings, ~110k card objects), we:
1. Capture every 'printed_name' (e.g. Through the Omenpaths names) and
   'flavor_name' (Godzilla, Dracula, Secret Lair skins), populating the
   CardLookup table so tournament decklists resolve accurately.
2. Deduplicate into the canonical Card table by oracle_id, maintaining one
   record per unique card identity.
3. Select the authentic original printing image (earliest release date,
   reprint=False) for every card to display on hover previews.
"""

import gzip
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from django.db import transaction
from tqdm import tqdm

from core.formats import FORMAT_SLUGS
from core.models.card import Card
from core.models.card import CardLookup
from core.models.card import normalize_card_name


logger = logging.getLogger(__name__)

SCRYFALL_BULK_URL = "https://api.scryfall.com/bulk-data"
USER_AGENT = "Modometa/1.0 (https://github.com/modometa/modometa)"


def fetch_default_cards_download_url() -> str:
    """Query Scryfall API for current default_cards bulk download URL."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(SCRYFALL_BULK_URL, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("data", []):
            if item.get("type") == "default_cards":
                uri = item.get("jsonl_download_uri") or item.get("download_uri")
                if uri:
                    return uri
    raise ValueError(
        "Could not find 'default_cards' download URI in Scryfall bulk data response"
    )


def fetch_oracle_cards_download_url() -> str:
    """Query Scryfall API for current oracle_cards bulk download URL (legacy fallback)."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(SCRYFALL_BULK_URL, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("data", []):
            if item.get("type") == "oracle_cards":
                uri = item.get("jsonl_download_uri") or item.get("download_uri")
                if uri:
                    return uri
    raise ValueError(
        "Could not find 'oracle_cards' download URI in Scryfall bulk data response"
    )


def download_default_cards(dest_path: Path, force: bool = False) -> Path:
    """Download default_cards bulk data if not present or forced.

    'default_cards' includes all English printings (~74MB compressed), which
    provides alternate names (Through the Omenpaths, Universes Within) and
    original printing images.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and not force:
        logger.info("Using cached Scryfall default_cards bulk file at %s", dest_path)
        return dest_path

    url = fetch_default_cards_download_url()
    # Adjust dest_path suffix if downloaded file is gzipped (.jsonl.gz)
    if url.endswith(".gz") and not str(dest_path).endswith(".gz"):
        dest_path = dest_path.with_name(dest_path.name + ".gz")
        if dest_path.exists() and not force:
            logger.info(
                "Using cached Scryfall default_cards bulk file at %s", dest_path
            )
            return dest_path

    logger.info("Downloading Scryfall Default Cards from %s ...", url)
    headers = {"User-Agent": USER_AGENT}

    with httpx.stream(
        "GET", url, headers=headers, timeout=120.0, follow_redirects=True
    ) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as fp:
            for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                fp.write(chunk)

    logger.info("Successfully downloaded Scryfall default_cards to %s", dest_path)
    return dest_path


def download_oracle_cards(dest_path: Path, force: bool = False) -> Path:
    """Download oracle_cards bulk data if not present or forced (legacy fallback)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and not force:
        logger.info("Using cached Scryfall bulk file at %s", dest_path)
        return dest_path

    url = fetch_oracle_cards_download_url()
    if url.endswith(".gz") and not str(dest_path).endswith(".gz"):
        dest_path = dest_path.with_name(dest_path.name + ".gz")
        if dest_path.exists() and not force:
            logger.info("Using cached Scryfall bulk file at %s", dest_path)
            return dest_path

    logger.info("Downloading Scryfall Oracle cards from %s ...", url)
    headers = {"User-Agent": USER_AGENT}

    with httpx.stream(
        "GET", url, headers=headers, timeout=120.0, follow_redirects=True
    ) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as fp:
            for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                fp.write(chunk)

    logger.info("Successfully downloaded Scryfall cards to %s", dest_path)
    return dest_path


def _extract_image_uri(item: dict[str, Any]) -> str | None:
    """Extract standard image URI from item or front face."""
    if "image_uris" in item and item["image_uris"]:
        return item["image_uris"].get("normal") or item["image_uris"].get("small")
    if "card_faces" in item and item["card_faces"]:
        face0 = item["card_faces"][0]
        if "image_uris" in face0 and face0["image_uris"]:
            return face0["image_uris"].get("normal") or face0["image_uris"].get("small")
    return None


def _extract_mana_cost(item: dict[str, Any]) -> str | None:
    """Extract mana cost from item or card faces (for MDFCs and transform cards)."""
    cost = item.get("mana_cost")
    if cost:
        return cost
    if "card_faces" in item and item["card_faces"]:
        faces_costs = [
            f.get("mana_cost", "").strip()
            for f in item["card_faces"]
            if f.get("mana_cost") and f.get("mana_cost").strip()
        ]
        if faces_costs:
            return " // ".join(faces_costs)
    return cost


def ingest_scryfall_cards(json_path: Path, batch_size: int = 2000) -> tuple[int, int]:
    """Ingest Scryfall cards and aliases into Card and CardLookup tables.

    Accepts either 'default_cards' or 'oracle_cards'.
    When processing all printings:
    - Deduplicates into Card by oracle_id, selecting the original printing image.
    - Populates CardLookup with canonical names, split faces, printed_names
      (e.g. Through the Omenpaths), and flavor_names (e.g. Godzilla/Secret Lair skins).
    """
    logger.info("Loading Scryfall data from %s ...", json_path)

    # Detect compression via suffix or magic bytes
    is_gz = str(json_path).endswith(".gz")
    if not is_gz and json_path.exists():
        try:
            with open(json_path, "rb") as test_f:
                is_gz = test_f.read(2) == b"\x1f\x8b"
        except Exception:
            is_gz = False
    open_fn = gzip.open if is_gz else open

    # Map oracle_id -> dictionary of canonical card info + best (original) image tracking
    cards_by_oracle: dict[str, dict[str, Any]] = {}
    lookup_map: dict[str, dict[str, Any]] = {}

    with open_fn(json_path, "rt", encoding="utf-8") as fp:
        for line in tqdm(fp, desc="Processing Scryfall cards"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("[") or line.endswith("]"):
                line = line.lstrip("[").rstrip("]").rstrip(",")
                if not line:
                    continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.debug("Skipping unparseable Scryfall JSON line: %s", exc)
                continue

            card_id = item.get("id")
            oracle_id = item.get("oracle_id") or card_id
            name = item.get("name", "")
            if not card_id or not name:
                continue

            # Extract image URI
            img = _extract_image_uri(item)

            # Original printing ranking:
            # Earliest release date first; non-reprints preferred over reprints;
            # paper preferred over digital-only for vintage/legacy sets if dates tie.
            released_at = item.get("released_at") or "9999-99-99"
            reprint = item.get("reprint", True)
            is_digital = item.get("digital", False)
            # Tuple sorting: (released_date, 1 if reprint else 0, 1 if digital else 0)
            print_rank = (released_at, 1 if reprint else 0, 1 if is_digital else 0)

            norm_name = normalize_card_name(name)
            type_line = item.get("type_line", "")
            is_land = "Land" in type_line
            is_basic = "Basic" in type_line and is_land

            # Calculate legality bonus for priority
            is_legal_any = any(
                v in ("legal", "restricted")
                for k, v in item.get("legalities", {}).items()
                if k in FORMAT_SLUGS
            )
            base_priority = 100 if is_legal_any else 10
            if type_line in ("Card // Card", "Card", ""):
                base_priority = 0

            # 1. Track / deduplicate canonical Card by oracle_id
            if oracle_id not in cards_by_oracle:
                cards_by_oracle[oracle_id] = {
                    "id": card_id,
                    "oracle_id": oracle_id,
                    "name": name,
                    "normalized_name": norm_name,
                    "mana_cost": _extract_mana_cost(item),
                    "cmc": float(item.get("cmc", 0.0)),
                    "type_line": type_line,
                    "oracle_text": item.get("oracle_text"),
                    "colors": item.get("colors", []),
                    "color_identity": item.get("color_identity", []),
                    "image_uri": img,
                    "legalities": item.get("legalities", {}),
                    "is_land": is_land,
                    "is_basic_land": is_basic,
                    "best_rank": print_rank if img else ("9999-99-99", 1, 1),
                }
            else:
                rec = cards_by_oracle[oracle_id]
                # If this printing has an image and is an earlier (or original) print, update image
                if img and (rec["image_uri"] is None or print_rank < rec["best_rank"]):
                    rec["image_uri"] = img
                    rec["best_rank"] = print_rank

            canonical_id = cards_by_oracle[oracle_id]["id"]
            canonical_name = cards_by_oracle[oracle_id]["name"]

            # 2. Extract all aliases from this printing
            # Canonical name alias
            aliases: list[tuple[str, str, int]] = [
                (norm_name, canonical_name, base_priority + 10)
            ]

            # Printed name (Through the Omenpaths / Universes Within)
            if item.get("printed_name"):
                aliases.append(
                    (
                        normalize_card_name(item["printed_name"]),
                        canonical_name,
                        base_priority + 15,
                    )
                )

            # Flavor name (Godzilla / Dracula / Marvel / Secret Lair skins)
            if item.get("flavor_name"):
                aliases.append(
                    (
                        normalize_card_name(item["flavor_name"]),
                        canonical_name,
                        base_priority + 15,
                    )
                )

            # Split card faces: e.g. "Fire // Ice" -> "fire" and "ice"
            if " // " in name:
                faces = [f.strip() for f in name.split("//")]
                if faces:
                    aliases.append(
                        (
                            normalize_card_name(faces[0]),
                            canonical_name,
                            base_priority + 5,
                        )
                    )
                    for other_face in faces[1:]:
                        aliases.append(
                            (
                                normalize_card_name(other_face),
                                canonical_name,
                                base_priority,
                            )
                        )

            if "card_faces" in item:
                for i, face in enumerate(item["card_faces"]):
                    face_name = face.get("name", "")
                    if face_name:
                        prio = base_priority + (5 if i == 0 else 0)
                        aliases.append(
                            (normalize_card_name(face_name), canonical_name, prio)
                        )
                    if face.get("printed_name"):
                        aliases.append(
                            (
                                normalize_card_name(face["printed_name"]),
                                canonical_name,
                                base_priority + 15,
                            )
                        )
                    if face.get("flavor_name"):
                        aliases.append(
                            (
                                normalize_card_name(face["flavor_name"]),
                                canonical_name,
                                base_priority + 15,
                            )
                        )

            for l_norm, c_name, prio in aliases:
                if not l_norm:
                    continue
                if l_norm not in lookup_map or prio > lookup_map[l_norm]["priority"]:
                    lookup_map[l_norm] = {
                        "lookup_name": l_norm,
                        "canonical_name": c_name,
                        "card_id": canonical_id,
                        "priority": prio,
                    }

    # Build model instances
    card_objects = [
        Card(
            id=d["id"],
            oracle_id=d["oracle_id"],
            name=d["name"],
            normalized_name=d["normalized_name"],
            mana_cost=d["mana_cost"],
            cmc=d["cmc"],
            type_line=d["type_line"],
            oracle_text=d["oracle_text"],
            colors=d["colors"],
            color_identity=d["color_identity"],
            image_uri=d["image_uri"],
            legalities=d["legalities"],
            is_land=d["is_land"],
            is_basic_land=d["is_basic_land"],
        )
        for d in cards_by_oracle.values()
    ]

    lookup_objects = [
        CardLookup(
            lookup_name=d["lookup_name"],
            canonical_name=d["canonical_name"],
            card_id=d["card_id"],
            priority=d["priority"],
        )
        for d in lookup_map.values()
    ]

    logger.info(
        "Saving %d Cards (with original print art) in batches of %d...",
        len(card_objects),
        batch_size,
    )
    with transaction.atomic():
        CardLookup.objects.all().delete()
        Card.objects.all().delete()

        for i in range(0, len(card_objects), batch_size):
            Card.objects.bulk_create(
                card_objects[i : i + batch_size], ignore_conflicts=True
            )

        logger.info("Saving %d Lookups to database...", len(lookup_objects))
        for i in range(0, len(lookup_objects), batch_size):
            CardLookup.objects.bulk_create(
                lookup_objects[i : i + batch_size], ignore_conflicts=True
            )

    logger.info(
        "Successfully ingested %d unique cards and %d lookups.",
        len(card_objects),
        len(lookup_objects),
    )
    return len(card_objects), len(lookup_objects)
