"""Historical card pricing ingestion pipeline for MODOMeta.

Aggregates daily price data from GoatBots (MTGO TIX) and MTGJSON (Paper USD/EUR
and Digital TIX) into columnar Parquet files stored in Cloudflare R2 and locally.
"""

import gzip
import io
import json
import logging
import lzma
import tempfile
import zipfile
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from core.utils import get_user_agent


logger = logging.getLogger(__name__)

USER_AGENT = get_user_agent()
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
}

# GoatBots daily price archives and year-to-date downloads:
# Documentation / download page: https://www.goatbots.com/download-prices
GOATBOTS_DAILY_URL = "https://www.goatbots.com/download/prices/price-history.zip"
GOATBOTS_YEAR_URL = "https://www.goatbots.com/download/prices/price-history-{year}.zip"

# MTGJSON bulk data files & documentation:
# Documentation / download page: https://mtgjson.com/downloads/all-files/
# AllPricesToday: prices recorded across paper/digital providers for the current/previous day.
MTGJSON_PRICES_TODAY_URL = "https://mtgjson.com/api/v5/AllPricesToday.json.gz"
# AllPrices: trailing 90 days of price history across paper and digital providers.
MTGJSON_PRICES_ALL_URL = "https://mtgjson.com/api/v5/AllPrices.json.gz"
# AllIdentifiers: unique MTGJSON UUIDs mapped to Scryfall, MTGO IDs, card names, and finishes.
MTGJSON_IDENTIFIERS_XZ_URL = "https://mtgjson.com/api/v5/AllIdentifiers.json.xz"

DEFAULT_IDENTIFIERS_PATH = Path("data/mtgjson_identifiers.parquet")

DAILY_PRICE_SCHEMA = pa.schema(
    [
        ("date", pa.date32()),
        ("scryfall_id", pa.string()),
        ("oracle_id", pa.string()),
        ("mtgo_id", pa.int32()),
        ("name", pa.string()),
        ("set", pa.string()),
        ("collector_number", pa.string()),
        ("finish", pa.string()),
        ("price_goatbots_tix", pa.float32()),
        ("price_cardhoarder_tix", pa.float32()),
        ("price_tcgplayer_usd", pa.float32()),
        ("price_cardmarket_eur", pa.float32()),
        ("price_cardkingdom_usd", pa.float32()),
        ("price_manapool_usd", pa.float32()),
    ]
)


def download_goatbots_year_archive(dest_dir: Path, year: int) -> Path:
    """Download GoatBots yearly price history archive and extract daily files."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    url = GOATBOTS_YEAR_URL.format(year=year)
    logger.info("Downloading GoatBots %d yearly price archive from %s ...", year, url)

    with tempfile.TemporaryDirectory() as tmp_dir:
        temp_zip = Path(tmp_dir) / f"price-history-{year}.zip"
        with httpx.Client(
            timeout=300.0, follow_redirects=True, headers=DEFAULT_HEADERS
        ) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(temp_zip, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)

        with zipfile.ZipFile(temp_zip) as zf:
            zf.extractall(dest_dir)

    logger.info("Extracted GoatBots %d prices into %s", year, dest_dir)
    return dest_dir


def download_goatbots_daily(dest_dir: Path, target_date: date | None = None) -> Path:
    """Download or locate GoatBots daily prices file.

    If target_date is specified:
      1. Checks if price-history-YYYY-MM-DD.txt exists locally.
      2. If target_date is in the past (> 1 day ago), downloads the yearly archive.
      3. Otherwise, downloads the daily price-history.zip.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    if target_date:
        year_str = str(target_date.year)
        candidates = [
            dest_dir / f"price-history-{target_date.isoformat()}.txt",
            dest_dir
            / f"price-history-{year_str}"
            / f"price-history-{target_date.isoformat()}.txt",
            dest_dir / "price-history" / f"price-history-{target_date.isoformat()}.txt",
        ]
        for candidate in candidates:
            if candidate.exists():
                logger.info("Found local GoatBots price file at %s", candidate)
                return candidate

        # If date is older than yesterday, it will only exist in the yearly archive
        if target_date < (date.today() - timedelta(days=1)):
            logger.info(
                "Date %s is in the past. Downloading %d yearly archive from GoatBots...",
                target_date,
                target_date.year,
            )
            download_goatbots_year_archive(dest_dir, target_date.year)
            for candidate in candidates:
                if candidate.exists():
                    logger.info("Found extracted GoatBots price file at %s", candidate)
                    return candidate

            raise FileNotFoundError(
                f"GoatBots prices for {target_date} are not available in the {target_date.year} yearly archive."
            )

    logger.info(
        "Downloading latest GoatBots price-history.zip from %s ...", GOATBOTS_DAILY_URL
    )
    with httpx.Client(
        timeout=60.0, follow_redirects=True, headers=DEFAULT_HEADERS
    ) as client:
        resp = client.get(GOATBOTS_DAILY_URL)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
            if not names:
                raise ValueError("GoatBots price-history.zip is empty")
            extracted_filename = names[0]
            extracted_path = dest_dir / extracted_filename
            zf.extract(extracted_filename, dest_dir)
            logger.info("Extracted GoatBots daily prices to %s", extracted_path)

            if (
                target_date
                and extracted_filename != f"price-history-{target_date.isoformat()}.txt"
            ):
                raise FileNotFoundError(
                    f"GoatBots prices for {target_date} are not available in latest download "
                    f"(archive contains '{extracted_filename}')."
                )
            return extracted_path


def download_mtgjson_prices(
    dest_path: Path, force: bool = False, full_history: bool = False
) -> Path:
    """Download MTGJSON prices file (AllPricesToday.json.gz or AllPrices.json.gz)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and not force:
        logger.info("Using cached MTGJSON price file at %s", dest_path)
        return dest_path

    url = MTGJSON_PRICES_ALL_URL if full_history else MTGJSON_PRICES_TODAY_URL
    logger.info("Downloading MTGJSON prices from %s to %s ...", url, dest_path)

    with httpx.Client(
        timeout=180.0, follow_redirects=True, headers=DEFAULT_HEADERS
    ) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)

    logger.info("Successfully downloaded MTGJSON prices to %s", dest_path)
    return dest_path


def build_identifier_lookup(raw_identifiers_path: Path, output_parquet: Path) -> Path:
    """Build a compact identifier mapping Parquet table from AllIdentifiers.json (.xz/.gz/.json)."""
    logger.info("Parsing MTGJSON AllIdentifiers from %s ...", raw_identifiers_path)
    output_parquet.parent.mkdir(parents=True, exist_ok=True)

    if str(raw_identifiers_path).endswith(".xz"):
        open_fn = lzma.open
    elif str(raw_identifiers_path).endswith(".gz"):
        open_fn = gzip.open
    else:
        open_fn = open

    with open_fn(raw_identifiers_path, "rt", encoding="utf-8") as f:
        data = json.load(f).get("data", {})

    uuids = []
    scryfall_ids = []
    oracle_ids = []
    names = []
    sets = []
    numbers = []
    finishes = []
    mtgo_ids = []
    mtgo_foil_ids = []

    for uid, item in data.items():
        ident = item.get("identifiers") or {}
        scryfall_id = ident.get("scryfallId")
        if not scryfall_id:
            continue
        uuids.append(uid)
        scryfall_ids.append(scryfall_id)
        oracle_ids.append(ident.get("scryfallOracleId") or "")
        names.append(item.get("name") or "")
        sets.append((item.get("setCode") or "").lower())
        numbers.append(item.get("number") or "")
        finishes.append(json.dumps(item.get("finishes") or ["nonfoil"]))

        mid = ident.get("mtgoId")
        mfid = ident.get("mtgoFoilId")
        mtgo_ids.append(int(mid) if mid and str(mid).isdigit() else None)
        mtgo_foil_ids.append(int(mfid) if mfid and str(mfid).isdigit() else None)

    table = pa.Table.from_arrays(
        [
            pa.array(uuids, type=pa.string()),
            pa.array(scryfall_ids, type=pa.string()),
            pa.array(oracle_ids, type=pa.string()),
            pa.array(names, type=pa.string()),
            pa.array(sets, type=pa.string()),
            pa.array(numbers, type=pa.string()),
            pa.array(finishes, type=pa.string()),
            pa.array(mtgo_ids, type=pa.int32()),
            pa.array(mtgo_foil_ids, type=pa.int32()),
        ],
        names=[
            "mtgjson_uuid",
            "scryfall_id",
            "oracle_id",
            "name",
            "set",
            "collector_number",
            "finishes",
            "mtgo_id",
            "mtgo_foil_id",
        ],
    )

    pq.write_table(table, output_parquet, compression="zstd", compression_level=7)
    logger.info("Wrote %d identifier rows to %s", len(table), output_parquet)
    return output_parquet


def download_and_build_identifiers(
    dest_parquet: Path = DEFAULT_IDENTIFIERS_PATH,
    url: str = MTGJSON_IDENTIFIERS_XZ_URL,
) -> Path:
    """Download MTGJSON AllIdentifiers.json.xz and build compact identifier Parquet table."""
    logger.info("Downloading AllIdentifiers from %s to build %s ...", url, dest_parquet)
    dest_parquet.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_dir:
        temp_xz = Path(tmp_dir) / "AllIdentifiers.json.xz"
        with httpx.Client(
            timeout=180.0, follow_redirects=True, headers=DEFAULT_HEADERS
        ) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(temp_xz, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
        logger.info(
            "Downloaded %s (%d MB). Parsing and compiling Parquet...",
            temp_xz,
            temp_xz.stat().st_size // (1024 * 1024),
        )
        build_identifier_lookup(temp_xz, dest_parquet)
    return dest_parquet


def load_identifier_lookup(
    parquet_path: Path = DEFAULT_IDENTIFIERS_PATH,
    auto_download: bool = False,
) -> dict[str, dict[str, Any]]:
    """Load cached identifier mapping into memory as a lookup dictionary."""
    if not parquet_path.exists():
        if auto_download:
            logger.info(
                "Identifier mapping not found at %s. Downloading and generating from MTGJSON...",
                parquet_path,
            )
            download_and_build_identifiers(parquet_path)
        else:
            raise FileNotFoundError(
                f"Identifier mapping not found at {parquet_path}. "
                f"Run 'sync_prices --build-identifiers' or download AllIdentifiers."
            )

    table = pq.read_table(parquet_path)
    pydict = table.to_pydict()
    id_map = {}
    for i in range(len(table)):
        uid = pydict["mtgjson_uuid"][i]
        id_map[uid] = {
            "scryfall_id": pydict["scryfall_id"][i],
            "oracle_id": pydict["oracle_id"][i],
            "name": pydict["name"][i],
            "set": pydict["set"][i],
            "collector_number": pydict["collector_number"][i],
            "mtgo_id": pydict["mtgo_id"][i],
            "mtgo_foil_id": pydict["mtgo_foil_id"][i],
        }
    return id_map


def generate_daily_parquet(
    target_date: date,
    goatbots_file: Path,
    mtgjson_prices_data: dict[str, Any],
    id_map: dict[str, dict[str, Any]],
    output_path: Path,
) -> Path:
    """Generate a single sorted, compressed daily Parquet file.

    Matches cards across GoatBots (MTGO TIX) and MTGJSON (Paper USD/EUR & Cardhoarder TIX).
    """
    logger.info(
        "Generating daily price Parquet for %s using %s ...",
        target_date,
        goatbots_file,
    )

    with open(goatbots_file, "r", encoding="utf-8") as f:
        gb_prices = json.load(f)

    date_str = target_date.isoformat()
    dates, scryfall_ids, oracle_ids, mtgo_ids = [], [], [], []
    names, sets, numbers, finishes = [], [], [], []
    gb_tix_col, ch_tix_col, tcg_usd_col, mkt_eur_col, ck_usd_col, mp_usd_col = (
        [],
        [],
        [],
        [],
        [],
        [],
    )

    for uid, card in id_map.items():
        pdata = mtgjson_prices_data.get(uid) or {}
        paper = pdata.get("paper") or {}
        mtgo = pdata.get("mtgo") or {}

        tcg = paper.get("tcgplayer", {}).get("retail") or {}
        mkt = paper.get("cardmarket", {}).get("retail") or {}
        ck = paper.get("cardkingdom", {}).get("retail") or {}
        mp = paper.get("manapool", {}).get("retail") or {}
        ch = mtgo.get("cardhoarder", {}).get("retail") or {}

        for finish in ("nonfoil", "foil", "etched"):
            mj_finish = "normal" if finish == "nonfoil" else finish

            # GoatBots lookup (foil: 0 matches mtgo_id, foil: 1 matches mtgo_foil_id)
            gb_tix = None
            if finish == "nonfoil" and card["mtgo_id"]:
                gb_tix = gb_prices.get(str(card["mtgo_id"]))
            elif finish == "foil" and card["mtgo_foil_id"]:
                gb_tix = gb_prices.get(str(card["mtgo_foil_id"]))

            # MTGJSON price extraction (target date with fallback within 1 day)
            def _get_price(provider_dict: dict[str, Any]) -> float | None:
                if mj_finish not in provider_dict:
                    return None
                finish_dict = provider_dict[mj_finish]
                if not finish_dict:
                    return None
                val = finish_dict.get(date_str)
                if val is not None:
                    return float(val)
                # Fallback to latest date only if within 1 calendar day of target_date
                # (accounts for UTC vs CET timezone offset between MTGJSON and GoatBots daily builds)
                latest_date_str = list(finish_dict.keys())[-1]
                try:
                    latest_d = datetime.strptime(latest_date_str, "%Y-%m-%d").date()
                    if abs((latest_d - target_date).days) <= 1:
                        v = finish_dict[latest_date_str]
                        return float(v) if v is not None else None
                except ValueError, TypeError:
                    pass
                return None

            tcg_usd = _get_price(tcg)
            mkt_eur = _get_price(mkt)
            ck_usd = _get_price(ck)
            mp_usd = _get_price(mp)
            ch_tix = _get_price(ch)

            if any(
                x is not None
                for x in (gb_tix, ch_tix, tcg_usd, mkt_eur, ck_usd, mp_usd)
            ):
                dates.append(date_str)
                scryfall_ids.append(card["scryfall_id"])
                oracle_ids.append(card["oracle_id"])
                mtgo_ids.append(
                    card["mtgo_id"] if finish == "nonfoil" else card["mtgo_foil_id"]
                )
                names.append(card["name"])
                sets.append(card["set"])
                numbers.append(card["collector_number"])
                finishes.append(finish)
                gb_tix_col.append(float(gb_tix) if gb_tix is not None else None)
                ch_tix_col.append(ch_tix)
                tcg_usd_col.append(tcg_usd)
                mkt_eur_col.append(mkt_eur)
                ck_usd_col.append(ck_usd)
                mp_usd_col.append(mp_usd)

    if not dates:
        raise ValueError(
            f"No price data found across GoatBots or MTGJSON for date {target_date}."
        )

    table = pa.Table.from_arrays(
        [
            pa.array(dates).cast(pa.date32()),
            pa.array(scryfall_ids, type=pa.string()),
            pa.array(oracle_ids, type=pa.string()),
            pa.array(mtgo_ids, type=pa.int32()),
            pa.array(names, type=pa.string()),
            pa.array(sets, type=pa.string()),
            pa.array(numbers, type=pa.string()),
            pa.array(finishes, type=pa.string()),
            pa.array(gb_tix_col, type=pa.float32()),
            pa.array(ch_tix_col, type=pa.float32()),
            pa.array(tcg_usd_col, type=pa.float32()),
            pa.array(mkt_eur_col, type=pa.float32()),
            pa.array(ck_usd_col, type=pa.float32()),
            pa.array(mp_usd_col, type=pa.float32()),
        ],
        schema=DAILY_PRICE_SCHEMA,
    )

    # Sort row groups by oracle_id, finish, date
    indices = pc.sort_indices(
        table,
        sort_keys=[("oracle_id", "ascending"), ("finish", "ascending")],
    )
    sorted_table = table.take(indices)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        sorted_table,
        output_path,
        compression="zstd",
        compression_level=7,
        use_dictionary=["set", "finish"],
    )

    logger.info(
        "Successfully wrote %d daily price rows to %s (%d KB)",
        len(sorted_table),
        output_path,
        output_path.stat().st_size // 1024,
    )
    return output_path


def generate_previous_365d_parquet(
    daily_dir: Path, output_file: Path, days: int = 365
) -> Path:
    """Concatenate and sort the previous 365 days of daily Parquet files into a single artifact."""
    cutoff_date = date.today() - timedelta(days=days)
    logger.info(
        "Compiling previous %d-day Parquet from %s (cutoff >= %s) ...",
        days,
        daily_dir,
        cutoff_date,
    )

    dataset = ds.dataset(str(daily_dir), format="parquet")
    filter_expr = pc.field("date") >= pa.scalar(cutoff_date, type=pa.date32())
    filtered_table = dataset.to_table(filter=filter_expr)

    if len(filtered_table) == 0:
        logger.warning(
            "No records found in %s within the last %d days", daily_dir, days
        )
        return output_file

    indices = pc.sort_indices(
        filtered_table,
        sort_keys=[
            ("oracle_id", "ascending"),
            ("finish", "ascending"),
            ("date", "ascending"),
        ],
    )
    sorted_table = filtered_table.take(indices)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        sorted_table,
        output_file,
        compression="zstd",
        compression_level=7,
        use_dictionary=["set", "finish"],
    )

    logger.info(
        "Successfully compiled previous %d-day Parquet to %s: %d rows (%d MB)",
        days,
        output_file,
        len(sorted_table),
        output_file.stat().st_size // (1024 * 1024),
    )
    return output_file


# Alias for backward compatibility
generate_rolling_365d_parquet = generate_previous_365d_parquet
