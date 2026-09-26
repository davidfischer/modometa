"""Management command to synchronize historical card pricing from GoatBots and MTGJSON."""

import gzip
import json
import logging
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.pipeline.pricing import DEFAULT_IDENTIFIERS_PATH
from core.pipeline.pricing import build_identifier_lookup
from core.pipeline.pricing import download_and_build_identifiers
from core.pipeline.pricing import download_goatbots_daily
from core.pipeline.pricing import download_mtgjson_prices
from core.pipeline.pricing import generate_daily_parquet
from core.pipeline.pricing import generate_previous_365d_parquet
from core.pipeline.pricing import load_identifier_lookup


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Synchronize daily card pricing from GoatBots and MTGJSON into columnar "
        "Parquet files."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default="yesterday",
            help="Target date in YYYY-MM-DD format (default: 'yesterday')",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=1,
            help=(
                "Number of days to process ending on --date (default: 1; max: 90). "
                "Capped at 90 days as MTGJSON AllPrices only provides 90 days of history."
            ),
        )
        parser.add_argument(
            "--output-dir",
            type=str,
            default="data/prices",
            help="Base directory to save daily Parquet files (default: 'data/prices')",
        )
        parser.add_argument(
            "--goatbots-dir",
            type=str,
            default="data/goatbots",
            help="Directory to search for or download GoatBots price history files",
        )
        parser.add_argument(
            "--mtgjson-dir",
            type=str,
            default="data",
            help="Directory to search for or download MTGJSON price files (default: 'data')",
        )
        parser.add_argument(
            "--identifiers-path",
            type=str,
            default=str(DEFAULT_IDENTIFIERS_PATH),
            help="Path to mtgjson_identifiers.parquet lookup table",
        )
        parser.add_argument(
            "--build-identifiers",
            action="store_true",
            help="Force rebuilding mtgjson_identifiers.parquet lookup table",
        )
        parser.add_argument(
            "--raw-identifiers-file",
            type=str,
            help="Path to AllIdentifiers.json (.xz/.gz/.json) to build identifiers table",
        )
        parser.add_argument(
            "--no-previous-365d",
            "--no-rolling",
            dest="no_previous_365d",
            action="store_true",
            help="Skip generating prices_previous_365d.parquet serving artifact",
        )
        parser.add_argument(
            "--force-mtgjson",
            action="store_true",
            help="Force re-downloading MTGJSON price files even if cached locally",
        )

    def handle(self, *args, **options):
        # 1. Parse target date and range
        date_arg = options["date"].strip().lower()
        try:
            if date_arg == "yesterday":
                end_date = date.today() - timedelta(days=1)
            elif date_arg == "today":
                # This should fail as today's data won't be available until tomorrow
                end_date = date.today()
            else:
                end_date = datetime.strptime(date_arg, "%Y-%m-%d").date()
        except ValueError as err:
            raise CommandError(
                f"Invalid date format '{options['date']}': {err}"
            ) from err

        if end_date > date.today():
            raise CommandError(
                f"Target date {end_date} is in the future (today is {date.today()}). "
                "Cannot sync prices for future dates."
            )

        days_count = options["days"]
        if days_count < 1:
            raise CommandError("--days must be at least 1.")
        if days_count > 90:
            raise CommandError(
                f"--days cannot exceed 90 (requested {days_count}). "
                "MTGJSON AllPrices only provides the trailing 90 days of price history."
            )

        start_date = end_date - timedelta(days=days_count - 1)

        base_dir = Path(settings.BASE_DIR)
        output_dir = Path(options["output_dir"])
        if not output_dir.is_absolute():
            output_dir = base_dir / output_dir

        goatbots_dir = Path(options["goatbots_dir"])
        if not goatbots_dir.is_absolute():
            goatbots_dir = base_dir / goatbots_dir

        mtgjson_dir = Path(options["mtgjson_dir"])
        if not mtgjson_dir.is_absolute():
            mtgjson_dir = base_dir / mtgjson_dir

        identifiers_path = Path(options["identifiers_path"])
        if not identifiers_path.is_absolute():
            identifiers_path = base_dir / identifiers_path

        # 2. Ensure identifiers mapping exists
        if options["build_identifiers"] or not identifiers_path.exists():
            raw_id_file = (
                Path(options["raw_identifiers_file"])
                if options.get("raw_identifiers_file")
                else None
            )
            if not raw_id_file or not raw_id_file.exists():
                # Check default cache paths
                candidates = [
                    base_dir / "data" / "AllIdentifiers.json.xz",
                    base_dir / "data" / "AllIdentifiers.json.gz",
                    base_dir / "data" / "AllIdentifiers.json",
                ]
                for c in candidates:
                    if c.exists():
                        raw_id_file = c
                        break

            if raw_id_file and raw_id_file.exists():
                self.stdout.write(
                    f"Building identifier lookup table from {raw_id_file}..."
                )
                build_identifier_lookup(raw_id_file, identifiers_path)
            else:
                self.stdout.write(
                    "Downloading AllIdentifiers from MTGJSON and building identifier table..."
                )
                download_and_build_identifiers(identifiers_path)

        self.stdout.write(f"Loading identifier lookup table from {identifiers_path}...")
        id_map = load_identifier_lookup(identifiers_path)
        self.stdout.write(f"Loaded {len(id_map)} card identifiers.")

        self.stdout.write(
            f"Processing prices from {start_date} to {end_date} ({days_count} day(s))..."
        )

        # 3. Load MTGJSON price data
        # If processing > 1 day, or if start_date is older than yesterday,
        # we must use AllPrices.json.gz (AllPricesToday only contains today/yesterday)
        is_full_history = (days_count > 1) or (
            start_date < (date.today() - timedelta(days=1))
        )
        mj_filename = (
            "AllPrices.json.gz" if is_full_history else "AllPricesToday.json.gz"
        )
        mj_cache_path = mtgjson_dir / mj_filename

        self.stdout.write(f"Checking/downloading MTGJSON price data ({mj_filename})...")
        download_mtgjson_prices(
            mj_cache_path,
            force=options["force_mtgjson"],
            full_history=is_full_history,
        )

        self.stdout.write(f"Reading MTGJSON price data from {mj_cache_path}...")
        with gzip.open(mj_cache_path, "rt", encoding="utf-8") as f:
            mj_json = json.load(f)
            mtgjson_meta = mj_json.get("meta", {})
            mtgjson_prices_data = mj_json.get("data", {})

        # If we loaded AllPricesToday but its date does not cover start_date (e.g. cached file is stale),
        # automatically fall back to AllPrices.json.gz
        if not is_full_history:
            meta_date_str = mtgjson_meta.get("date")
            if meta_date_str:
                try:
                    meta_d = datetime.strptime(meta_date_str, "%Y-%m-%d").date()
                    if abs((meta_d - start_date).days) > 1:
                        self.stdout.write(
                            f"Cached AllPricesToday date ({meta_d}) does not match target date ({start_date}). "
                            "Switching to AllPrices.json.gz for historical prices..."
                        )
                        is_full_history = True
                        mj_cache_path = mtgjson_dir / "AllPrices.json.gz"
                        download_mtgjson_prices(
                            mj_cache_path,
                            force=options["force_mtgjson"],
                            full_history=True,
                        )
                        with gzip.open(mj_cache_path, "rt", encoding="utf-8") as f:
                            mj_json = json.load(f)
                            mtgjson_prices_data = mj_json.get("data", {})
                except ValueError, TypeError:
                    pass

        # 4. Generate daily Parquet files
        current_d = start_date
        daily_files = []

        while current_d <= end_date:
            year_str = str(current_d.year)
            daily_out = (
                output_dir / "daily" / year_str / f"{current_d.isoformat()}.parquet"
            )

            self.stdout.write(f"Processing prices for {current_d}...")
            # Look for GoatBots file in subdirectories as well (e.g. price-history-2026/)
            gb_file = None
            gb_candidates = [
                goatbots_dir / f"price-history-{current_d.isoformat()}.txt",
                goatbots_dir
                / f"price-history-{year_str}"
                / f"price-history-{current_d.isoformat()}.txt",
                goatbots_dir
                / "price-history"
                / f"price-history-{current_d.isoformat()}.txt",
            ]
            for cand in gb_candidates:
                if cand.exists():
                    gb_file = cand
                    break

            if not gb_file:
                # If not found locally, attempt download
                try:
                    gb_file = download_goatbots_daily(
                        goatbots_dir, target_date=current_d
                    )
                except (FileNotFoundError, ValueError) as err:
                    raise CommandError(str(err)) from err

            try:
                out_file = generate_daily_parquet(
                    target_date=current_d,
                    goatbots_file=gb_file,
                    mtgjson_prices_data=mtgjson_prices_data,
                    id_map=id_map,
                    output_path=daily_out,
                )
            except ValueError as err:
                raise CommandError(str(err)) from err

            daily_files.append(out_file)
            current_d += timedelta(days=1)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully generated {len(daily_files)} daily Parquet file(s)."
            )
        )

        # 5. Generate previous 365-day artifact
        if not options["no_previous_365d"]:
            previous_365d_out = output_dir / "prices_previous_365d.parquet"
            self.stdout.write(
                "Compiling prices_previous_365d.parquet serving artifact..."
            )
            daily_dir = output_dir / "daily"
            generate_previous_365d_parquet(daily_dir, previous_365d_out, days=365)

            self.stdout.write(
                self.style.SUCCESS(f"Successfully compiled {previous_365d_out}")
            )
