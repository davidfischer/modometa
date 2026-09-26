"""Tests for historical card pricing pipeline and management command."""

import gzip
import json
from datetime import date
from datetime import timedelta
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.pipeline.pricing import DAILY_PRICE_SCHEMA
from core.pipeline.pricing import build_identifier_lookup
from core.pipeline.pricing import generate_daily_parquet
from core.pipeline.pricing import generate_previous_365d_parquet
from core.pipeline.pricing import load_identifier_lookup


@pytest.fixture(autouse=True)
def prevent_network_calls(monkeypatch):
    """Ensure no test in test_pricing accidentally reaches out to the network."""

    def no_network(*args, **kwargs):
        raise RuntimeError("Unexpected network call during test execution!")

    monkeypatch.setattr(httpx.Client, "get", no_network)
    monkeypatch.setattr(httpx.Client, "stream", no_network)


@pytest.fixture
def sample_raw_identifiers(tmp_path: Path) -> Path:
    """Create a minimal AllIdentifiers.json mock file."""
    data = {
        "meta": {"date": "2026-09-24", "version": "5.3.0"},
        "data": {
            "uuid-bolt-lea": {
                "name": "Lightning Bolt",
                "setCode": "LEA",
                "number": "161",
                "finishes": ["nonfoil"],
                "identifiers": {
                    "scryfallId": "sf-bolt-lea",
                    "scryfallOracleId": "oracle-bolt",
                    "mtgoId": "1001",
                },
            },
            "uuid-bolt-m10": {
                "name": "Lightning Bolt",
                "setCode": "M10",
                "number": "146",
                "finishes": ["nonfoil", "foil"],
                "identifiers": {
                    "scryfallId": "sf-bolt-m10",
                    "scryfallOracleId": "oracle-bolt",
                    "mtgoId": "1002",
                    "mtgoFoilId": "1003",
                },
            },
            "uuid-lotus": {
                "name": "Black Lotus",
                "setCode": "LEA",
                "number": "232",
                "finishes": ["nonfoil"],
                "identifiers": {
                    "scryfallId": "sf-lotus",
                    "scryfallOracleId": "oracle-lotus",
                },
            },
        },
    }
    file_path = tmp_path / "sample_identifiers.json"
    file_path.write_text(json.dumps(data), encoding="utf-8")
    return file_path


def test_build_and_load_identifier_lookup(sample_raw_identifiers: Path, tmp_path: Path):
    """Test extracting compact identifier parquet and loading into lookup dictionary."""
    out_parquet = tmp_path / "test_identifiers.parquet"
    build_identifier_lookup(sample_raw_identifiers, out_parquet)

    assert out_parquet.exists()
    id_map = load_identifier_lookup(out_parquet)

    assert len(id_map) == 3
    assert "uuid-bolt-m10" in id_map
    m10_rec = id_map["uuid-bolt-m10"]
    assert m10_rec["scryfall_id"] == "sf-bolt-m10"
    assert m10_rec["oracle_id"] == "oracle-bolt"
    assert m10_rec["name"] == "Lightning Bolt"
    assert m10_rec["set"] == "m10"
    assert m10_rec["mtgo_id"] == 1002
    assert m10_rec["mtgo_foil_id"] == 1003


def test_generate_daily_parquet(sample_raw_identifiers: Path, tmp_path: Path):
    """Test daily Parquet generation and cross-provider price joining."""
    id_parquet = tmp_path / "test_identifiers.parquet"
    build_identifier_lookup(sample_raw_identifiers, id_parquet)
    id_map = load_identifier_lookup(id_parquet)

    # GoatBots mock prices: 1001 (nonfoil LEA), 1002 (nonfoil M10), 1003 (foil M10)
    gb_data = {
        "1001": 0.50,
        "1002": 0.05,
        "1003": 0.25,
    }
    gb_file = tmp_path / "price-history-2026-09-23.txt"
    gb_file.write_text(json.dumps(gb_data), encoding="utf-8")

    # MTGJSON mock prices
    target_d = date(2026, 9, 23)
    date_str = target_d.isoformat()
    mtgjson_data = {
        "uuid-bolt-m10": {
            "paper": {
                "tcgplayer": {
                    "retail": {
                        "normal": {date_str: 1.50},
                        "foil": {date_str: 4.00},
                    }
                },
                "cardmarket": {
                    "retail": {
                        "normal": {date_str: 1.20},
                    }
                },
                "manapool": {
                    "retail": {
                        "normal": {date_str: 1.45},
                    }
                },
            },
            "mtgo": {
                "cardhoarder": {
                    "retail": {
                        "normal": {date_str: 0.04},
                        "foil": {date_str: 0.20},
                    }
                }
            },
        },
        "uuid-lotus": {
            "paper": {
                "tcgplayer": {
                    "retail": {
                        "normal": {date_str: 25000.0},
                    }
                }
            }
        },
    }

    out_daily = tmp_path / "daily.parquet"
    generate_daily_parquet(
        target_date=target_d,
        goatbots_file=gb_file,
        mtgjson_prices_data=mtgjson_data,
        id_map=id_map,
        output_path=out_daily,
    )

    assert out_daily.exists()
    table = pq.read_table(out_daily)
    assert table.schema == DAILY_PRICE_SCHEMA

    # Check rows:
    # 1. LEA Bolt (nonfoil) -> GoatBots: 0.50
    # 2. M10 Bolt (nonfoil) -> GoatBots: 0.05, Cardhoarder: 0.04, TCG: 1.50, Cardmarket: 1.20, ManaPool: 1.45
    # 3. M10 Bolt (foil) -> GoatBots: 0.25, Cardhoarder: 0.20, TCG: 4.00
    # 4. LEA Lotus (nonfoil) -> TCG: 25000.0, GoatBots: None
    rows = table.to_pylist()
    assert len(rows) == 4

    m10_nonfoil = next(
        r
        for r in rows
        if r["scryfall_id"] == "sf-bolt-m10" and r["finish"] == "nonfoil"
    )
    assert m10_nonfoil["price_goatbots_tix"] == pytest.approx(0.05, 0.001)
    assert m10_nonfoil["price_cardhoarder_tix"] == pytest.approx(0.04, 0.001)
    assert m10_nonfoil["price_tcgplayer_usd"] == pytest.approx(1.50, 0.001)
    assert m10_nonfoil["price_cardmarket_eur"] == pytest.approx(1.20, 0.001)
    assert m10_nonfoil["price_manapool_usd"] == pytest.approx(1.45, 0.001)

    m10_foil = next(
        r for r in rows if r["scryfall_id"] == "sf-bolt-m10" and r["finish"] == "foil"
    )
    assert m10_foil["price_goatbots_tix"] == pytest.approx(0.25, 0.001)
    assert m10_foil["price_cardhoarder_tix"] == pytest.approx(0.20, 0.001)
    assert m10_foil["price_tcgplayer_usd"] == pytest.approx(4.00, 0.001)
    assert m10_foil["price_cardmarket_eur"] is None
    assert m10_foil["price_manapool_usd"] is None

    lotus_row = next(r for r in rows if r["scryfall_id"] == "sf-lotus")
    assert lotus_row["price_tcgplayer_usd"] == pytest.approx(25000.0, 0.1)
    assert lotus_row["price_goatbots_tix"] is None
    assert lotus_row["price_manapool_usd"] is None


def test_generate_rolling_365d_parquet(tmp_path: Path):
    """Test consolidating trailing daily parquets while excluding older records."""
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir(parents=True)

    today = date.today()
    recent_date = today - timedelta(days=10)
    old_date = today - timedelta(days=400)

    # Minimal schema table
    def _create_minimal_parquet(target_date: date, path: Path):
        table = pa.Table.from_arrays(
            [
                pa.array([target_date.isoformat()]).cast(pa.date32()),
                pa.array(["sf-1"]),
                pa.array(["orc-1"]),
                pa.array([101], type=pa.int32()),
                pa.array(["Card One"]),
                pa.array(["set1"]),
                pa.array(["1"]),
                pa.array(["nonfoil"]),
                pa.array([1.0], type=pa.float32()),
                pa.array([1.0], type=pa.float32()),
                pa.array([1.0], type=pa.float32()),
                pa.array([1.0], type=pa.float32()),
                pa.array([1.0], type=pa.float32()),
                pa.array([1.0], type=pa.float32()),
            ],
            schema=DAILY_PRICE_SCHEMA,
        )
        pq.write_table(table, path)

    _create_minimal_parquet(
        recent_date, daily_dir / f"{recent_date.isoformat()}.parquet"
    )
    _create_minimal_parquet(old_date, daily_dir / f"{old_date.isoformat()}.parquet")

    previous_365d_out = tmp_path / "prices_previous_365d.parquet"
    generate_previous_365d_parquet(daily_dir, previous_365d_out, days=365)

    assert previous_365d_out.exists()
    table = pq.read_table(previous_365d_out)
    rows = table.to_pylist()
    assert len(rows) == 1
    assert rows[0]["date"] == recent_date


def test_sync_prices_management_command(
    sample_raw_identifiers: Path, tmp_path: Path, monkeypatch
):
    """Test executing sync_prices CLI with local mock data."""
    id_parquet = tmp_path / "identifiers.parquet"
    output_dir = tmp_path / "prices"

    target_d = date(2026, 9, 23)
    gb_dir = tmp_path / "goatbots"
    gb_dir.mkdir()
    (gb_dir / f"price-history-{target_d.isoformat()}.txt").write_text(
        json.dumps({"1001": 0.50}), encoding="utf-8"
    )

    # Mock download_mtgjson_prices to write a tiny gzip file instead of network request
    def mock_download_mtgjson(dest_path: Path, force=False, full_history=False):
        data = {
            "meta": {"date": target_d.isoformat()},
            "data": {
                "uuid-bolt-lea": {
                    "paper": {
                        "tcgplayer": {
                            "retail": {
                                "normal": {target_d.isoformat(): 1.50},
                            }
                        }
                    }
                }
            },
        }
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(dest_path, "wt", encoding="utf-8") as f:
            json.dump(data, f)
        return dest_path

    monkeypatch.setattr(
        "core.management.commands.sync_prices.download_mtgjson_prices",
        mock_download_mtgjson,
    )

    call_command(
        "sync_prices",
        date=target_d.isoformat(),
        days=1,
        output_dir=str(output_dir),
        goatbots_dir=str(gb_dir),
        mtgjson_dir=str(tmp_path / "mtgjson"),
        identifiers_path=str(id_parquet),
        build_identifiers=True,
        raw_identifiers_file=str(sample_raw_identifiers),
        no_previous_365d=False,
    )

    expected_daily = output_dir / "daily" / "2026" / f"{target_d.isoformat()}.parquet"
    assert expected_daily.exists()

    expected_rollup = output_dir / "prices_previous_365d.parquet"
    assert expected_rollup.exists()


def test_sync_prices_days_cap_validation():
    """Test that sync_prices rejects --days > 90 and --days < 1."""
    with pytest.raises(CommandError, match="cannot exceed 90"):
        call_command("sync_prices", days=91)

    with pytest.raises(CommandError, match="must be at least 1"):
        call_command("sync_prices", days=0)


def test_sync_prices_future_date_rejected():
    """Test that sync_prices rejects future target dates."""
    future_date = date.today() + timedelta(days=2)
    with pytest.raises(CommandError, match="in the future"):
        call_command("sync_prices", date=future_date.isoformat())


def test_sync_prices_invalid_date_format():
    """Test that sync_prices rejects unparseable date strings."""
    with pytest.raises(CommandError, match="Invalid date format"):
        call_command("sync_prices", date="invalid-date")
