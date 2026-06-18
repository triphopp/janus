"""Pipe-delimited EOD settlement file loader.

Handles energy futures + options settlement files.
Parses US-format dates, disambiguates future vs option via CONTRACT TYPE + STRIKE.
Field mapping per blueprint section 05.
"""

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .base import ProviderBase, RAW_SCHEMA, validate_schema
from .symbology import Symbology
from .versioned_cache import add_availability_columns

# Column mapping: source field → standardized name
SETTLEMENT_COLUMN_MAP = {
    "TRADE DATE":          "as_of_date",
    "HUB":                 "hub",
    "PRODUCT":             "product_name",      # resolved via symbology
    "STRIP":               "delivery_month",
    "CONTRACT":            "contract_root",
    "CONTRACT TYPE":       "right_raw",
    "STRIKE":              "strike",
    "SETTLEMENT PRICE":    "price",
    "NET CHANGE":          "net_change",
    "EXPIRATION DATE":     "expiry",
    "PRODUCT_ID":          "product_id",
    "OPTION_VOLATILITY":   "iv_provided",
    "DELTA_FACTOR":        "delta_provided",
}

DATE_COLUMNS = ["TRADE DATE", "STRIP", "EXPIRATION DATE"]


class SettlementLoader(ProviderBase):
    """Load pipe-delimited EOD settlement files.

    Handles both futures and options in a single file.
    Disambiguates via CONTRACT TYPE (C/P = option, empty/F = future) + STRIKE presence.
    """

    def __init__(self, symbology: Optional[Symbology] = None):
        self.symbology = symbology or Symbology()

    def fetch(self, path_or_symbol: str, start, end) -> pd.DataFrame:
        """Parse pipe-delimited settlement file → RAW_SCHEMA DataFrame.

        Args:
            path_or_symbol: file path to settlement pipe-delimited file
            start, end: date range filter (inclusive)
        """
        path = Path(path_or_symbol)
        if not path.exists():
            raise FileNotFoundError(f"Settlement file not found: {path}")

        df = pd.read_csv(path, sep="|")

        # ── Parse dates (US format M/D/YYYY — never let pandas guess) ──
        for c in DATE_COLUMNS:
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], format="%m/%d/%Y")

        # Filter early so large settlement files do not run symbology checks,
        # net-change diagnostics, and schema coercion over rows outside the request.
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        if "TRADE DATE" in df.columns:
            df = df[(df["TRADE DATE"] >= start_ts) & (df["TRADE DATE"] <= end_ts)].copy()

        # ── Disambiguate future vs option ──
        contract_type = df.get("CONTRACT TYPE", pd.Series(dtype=str))
        strike = df.get("STRIKE", pd.Series(dtype=float))

        is_opt = contract_type.isin(["C", "P"]) & strike.notna()
        df["instrument_type"] = np.where(is_opt, "option", "future")
        df["right"] = np.where(is_opt, contract_type, None)

        # Null out option-only fields for futures
        for col in ["STRIKE", "OPTION_VOLATILITY", "DELTA_FACTOR"]:
            if col in df.columns:
                df.loc[~is_opt, col] = np.nan

        # ── Rename to standardized schema ──
        df = df.rename(columns=SETTLEMENT_COLUMN_MAP)

        # ── Normalize IV: source stores as percent (e.g. 29.14 → 0.2914) ──
        if "iv_provided" in df.columns:
            df["iv_provided"] = df["iv_provided"] / 100.0

        # ── Enforce symbology before any downstream adapter logic ──
        violations = self.symbology.validate_uniqueness()
        if violations:
            raise ValueError(f"Symbology uniqueness violations: {violations}")

        orphans = self.symbology.validate_no_orphans(df)
        if orphans:
            raise ValueError(f"Unmapped product_id values in settlement data: {orphans}")

        key_cols = ["product_id", "hub", "contract_root"]
        if all(col in df.columns for col in key_cols):
            for row in df[key_cols].drop_duplicates().itertuples(index=False):
                try:
                    self.symbology.resolve(
                        int(row.product_id),
                        str(row.hub),
                        str(row.contract_root),
                    )
                except KeyError as exc:
                    raise ValueError(f"Unresolved symbology tuple: {row}") from exc

        # ── Add metadata ──
        df["provider"] = "settlement"
        df["timestamp"] = None  # EOD = no intraday timestamp
        df = add_availability_columns(
            df,
            data_type="settlement",
            cfg={"available_at_lag": {"settlement": "3h"}},
        )
        # Earliest actionable moment for an EOD settlement row. Strategies that
        # decide later can overwrite this downstream.
        df["decision_time"] = df["available_at"]

        # ── Validate net_change ──
        df["net_change_flag"] = False
        if "net_change" in df.columns and "price" in df.columns:
            identity_cols = [
                col for col in (
                    "product_id", "contract_root", "hub", "instrument_type",
                    "delivery_month", "expiry", "right", "strike",
                )
                if col in df.columns
            ]
            sort_cols = [*identity_cols, "as_of_date"] if identity_cols else ["as_of_date"]
            df = df.sort_values(sort_cols)
            group_key = identity_cols[0] if len(identity_cols) == 1 else identity_cols
            groups = df.groupby(group_key, dropna=False) if identity_cols else [(None, df)]
            for _, grp in groups:
                grp = grp.sort_values("as_of_date")
                price_diff = grp["price"].diff()
                net_change_diff = (price_diff - grp["net_change"]).abs()
                # Flag rows where |price.diff() - net_change| > 1 tick
                bad = net_change_diff > 0.02  # ~2 cents tolerance
                if bad.any():
                    idx = grp.index[bad]
                    df.loc[idx, "net_change_flag"] = True

        # ── Tag provider ──
        df["provider"] = "settlement"

        # ── Validate schema ──
        return validate_schema(df, RAW_SCHEMA)

    def list_expired(self, root: str, asof) -> list:
        """Return option series expiring before asof. Override in subclass if needed."""
        # Settlement files are snapshots — expired series are in the file itself
        # This method is more relevant for live feeds
        return []


def parse_pipe_row(row: str) -> dict:
    """Parse a single pipe-delimited row into a dict. For testing.

    Example row:
    9/25/2024|North Sea|Brent Crude Futures|11/1/2024|B|C|10.0000|63.46000|-1.71000|9/25/2024|254|0.01000|0.00000
    """
    fields = row.split("|")
    result = {
        "as_of_date":      pd.Timestamp(fields[0]),
        "hub":             fields[1],
        "product_name":    fields[2],
        "delivery_month":  pd.Timestamp(fields[3]),
        "contract_root":   fields[4],
        "right_raw":       fields[5],
        "strike":          float(fields[6]) if fields[6] else None,
        "price":           float(fields[7]),
        "net_change":      float(fields[8]) if len(fields) > 8 else 0.0,
        "expiry":          pd.Timestamp(fields[9]) if len(fields) > 9 else None,
        "product_id":      int(fields[10]) if len(fields) > 10 else None,
        "iv_provided":     float(fields[11]) if len(fields) > 11 and fields[11] else None,
        "delta_provided":  float(fields[12]) if len(fields) > 12 and fields[12] else None,
    }
    # Determine instrument_type
    right = fields[5] if fields[5] in ("C", "P") else None
    strike = float(fields[6]) if fields[6] else None
    result["instrument_type"] = "option" if (right and strike is not None) else "future"
    result["right"] = right
    result["available_at"] = pd.to_datetime(result["as_of_date"] + pd.Timedelta(hours=3), utc=True)
    result["decision_time"] = result["available_at"]
    result["ingested_at"] = pd.Timestamp.now("UTC")
    result["provider"] = "settlement"
    result["timestamp"] = None
    return result
