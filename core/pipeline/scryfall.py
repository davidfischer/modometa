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
import re
from datetime import datetime
from datetime import timedelta
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


SPECIAL_FRAME_EFFECTS = {
    "extendedart",
    "showcase",
    "inverted",
    "fullart",
    "etched",
    "shatteredglass",
    "thick",
    "spellslinger",
}

NON_STANDARD_LAYOUTS = {
    "art_series",
    "token",
    "double_faced_token",
    "emblem",
    "planar",
    "scheme",
    "vanguard",
    "reversible_card",
}

WORST_PRINT_RANK = (
    "9999-99-99",
    1,
    1,
    (1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 999999),
)


def _calculate_print_rank(item: dict[str, Any]) -> tuple:
    """Calculate printing priority rank for card images.

    Preferences:
    - Earliest release date first.
    - If the earliest printing is Alpha (lea), prefer Beta (leb) instead.
    - If there are multiple printings in the earliest set, prefer the 'regular'
      version over promos (prerelease, promo pack), full-art, showcase,
      extended art, or borderless variants.
    - Non-reprints over reprints.
    - Paper over digital-only.
    - Normal playable card layouts over tokens/art series.
    - Lower numeric collector number over high variant numbers/suffixes.
    """
    set_code = item.get("set", "").lower()
    released_at = item.get("released_at") or "9999-99-99"

    # 1. Alpha vs Beta: map Alpha to right after Beta so Beta wins
    if set_code == "lea":
        effective_date = "1993-10-05"
        is_reprint = 0
    elif set_code == "leb":
        effective_date = "1993-10-04"
        is_reprint = 0
    else:
        effective_date = released_at
        is_reprint = 1 if item.get("reprint", True) else 0

    is_digital = 1 if item.get("digital", False) else 0

    # 2. Promo / Prerelease penalty:
    is_promo = (
        1
        if (
            item.get("promo", False)
            or item.get("set_type") == "promo"
            or (set_code.startswith("p") and len(set_code) in (4, 5))
        )
        else 0
    )

    # Prerelease promos often have an announcement/prerelease date slightly
    # before the main retail set (within ~60 days). Shift them so the main
    # set printing is evaluated as earlier or on equal footing.
    if is_promo and set_code.startswith("p") and len(set_code) in (4, 5):
        try:
            d = datetime.strptime(effective_date, "%Y-%m-%d") + timedelta(days=60)
            effective_date = d.strftime("%Y-%m-%d")
        except ValueError, TypeError:
            pass

    # 3. Layout penalty (exclude/demote art_series, tokens, etc.)
    layout = item.get("layout", "")
    layout_penalty = 1 if layout in NON_STANDARD_LAYOUTS else 0

    # 4. Variant / full-art / borderless / boosterfun penalties
    is_full_art = 1 if item.get("full_art", False) else 0
    is_textless = 1 if item.get("textless", False) else 0
    is_borderless = 1 if item.get("border_color") == "borderless" else 0

    promo_types = item.get("promo_types") or []
    has_boosterfun = (
        1
        if any(
            pt
            in (
                "boosterfun",
                "bundle",
                "prerelease",
                "promopack",
                "textured",
                "serialized",
                "concept",
                "poster",
            )
            for pt in promo_types
        )
        else 0
    )

    frame_effects = item.get("frame_effects") or []
    has_special_frame = (
        1 if any(fe in SPECIAL_FRAME_EFFECTS for fe in frame_effects) else 0
    )

    not_booster = 0 if item.get("booster", True) else 1
    is_variation = 1 if item.get("variation", False) else 0

    # 5. Collector number: regular versions have pure numeric numbers within the standard set run
    raw_cn = str(item.get("collector_number", ""))
    match = re.match(r"^(\d+)", raw_cn)
    if match:
        cn_num = int(match.group(1))
        has_alpha_suffix = 1 if len(match.group(1)) < len(raw_cn) else 0
    else:
        cn_num = 999999
        has_alpha_suffix = 1

    regularity_penalty = (
        layout_penalty,
        is_promo,
        has_boosterfun,
        is_borderless,
        is_full_art,
        has_special_frame,
        is_textless,
        not_booster,
        is_variation,
        has_alpha_suffix,
        cn_num,
    )

    return (effective_date, is_reprint, is_digital, regularity_penalty)


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
            print_rank = _calculate_print_rank(item)

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
                    "best_rank": print_rank if img else WORST_PRINT_RANK,
                }
            else:
                rec = cards_by_oracle[oracle_id]
                # If this printing has an image and is preferred over the current best, update
                if img and (rec["image_uri"] is None or print_rank < rec["best_rank"]):
                    rec["id"] = card_id
                    rec["image_uri"] = img
                    rec["best_rank"] = print_rank

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
                        "oracle_id": oracle_id,
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
            card_id=cards_by_oracle[d["oracle_id"]]["id"],
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
