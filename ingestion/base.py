"""ProviderBase — contract for all data providers.

Every provider must implement fetch() returning RAW_SCHEMA-compliant DataFrame.
Survivorship: expired/delisted series must be preserved (PIT).
"""

from abc import ABC, abstractmethod
from typing import Optional, Tuple

import pandas as pd

# ── RAW_SCHEMA: ingestion → adapter contract ──
# Supports futures + options in single table, disambiguated by instrument_type.
# Equity uses abbreviated schema: as_of_date, symbol, raw_close, adj_factor, volume, is_delisted.
RAW_SCHEMA = {
    "as_of_date":       "datetime64[ns]",        # TRADE DATE (PIT anchor) — settlement knows EOD
    "available_at":     "datetime64[ns, UTC]",   # when data was knowable by the strategy
    "ingested_at":      "datetime64[ns, UTC]",   # when pipeline captured this version
    "timestamp":        "datetime64[ns, UTC]|None",  # only intraday; EOD = None
    "product_id":       "int",                   # stable key (e.g. 254) — join uses this
    "contract_root":    "str",                   # B (Brent), CL (WTI) ...
    "hub":              "str",                   # North Sea ...
    "instrument_type":  "str",                   # 'future' | 'option'
    "right":            "str|None",              # 'C'|'P' (option); None (future)
    "strike":           "float|None",            # None for futures
    "delivery_month":   "datetime64[ns]",        # STRIP — for term structure + roll
    "expiry":           "datetime64[ns]",        # EXPIRATION DATE — DTE/purge
    "price":            "float",                 # SETTLEMENT (not last-trade!)
    "net_change":       "float|None",           # null on a contract's first obs (no prior settle)
    "iv_provided":      "float|None",            # OPTION_VOLATILITY (exchange-calc) — validate
    "delta_provided":   "float|None",            # DELTA_FACTOR
    "provider":         "str",                   # settlement | yfinance | massive
}

# Equity schema (simpler — no strike/expiry)
EQUITY_RAW_SCHEMA = {
    "as_of_date":    "datetime64[ns]",
    "available_at":  "datetime64[ns, UTC]",
    "ingested_at":   "datetime64[ns, UTC]",
    "symbol":        "str",
    "raw_close":     "float",
    "adj_factor":    "float",
    "volume":        "int",
    "is_delisted":   "bool",
}


class ProviderBase(ABC):
    """Abstract base for all data providers."""

    @abstractmethod
    def fetch(self, symbol: str, start, end) -> pd.DataFrame:
        """Fetch raw → normalize to RAW_SCHEMA → return.

        Must NOT drop expired series (survivorship).
        Must be point-in-time.
        """
        ...

    @abstractmethod
    def list_expired(self, root: str, asof) -> list:
        """Return option series that expired before asof — preserve for backtest."""
        ...


def validate_schema(df: pd.DataFrame, schema: dict) -> pd.DataFrame:
    """Validate and coerce a provider DataFrame against the declared schema."""
    df = df.copy()
    required = {k for k, v in schema.items() if "|None" not in v}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"RAW_SCHEMA violation: missing columns {missing}")

    for col, dtype in schema.items():
        if col not in df.columns:
            continue

        optional = "|None" in dtype
        base_dtype = dtype.replace("|None", "")
        before_bad = df[col].isna()

        if base_dtype.startswith("datetime64"):
            df[col] = pd.to_datetime(df[col], errors="coerce", utc="UTC" in base_dtype)
            df[col] = df[col].astype(base_dtype)
        elif base_dtype == "int":
            df[col] = pd.to_numeric(df[col], errors="coerce")
            if not optional and df[col].isna().any():
                bad = df.index[df[col].isna() & ~before_bad].tolist()[:5]
                raise ValueError(f"RAW_SCHEMA violation: invalid int values in {col}; rows={bad}")
            df[col] = df[col].astype("Int64" if optional or df[col].isna().any() else "int64")
        elif base_dtype == "float":
            df[col] = pd.to_numeric(df[col], errors="coerce")
        elif base_dtype == "bool":
            if df[col].dtype != bool:
                df[col] = df[col].astype("boolean")
        elif base_dtype == "str":
            df[col] = df[col].astype("string")

        if not optional and df[col].isna().any():
            bad = df.index[df[col].isna()].tolist()[:5]
            raise ValueError(f"RAW_SCHEMA violation: null required values in {col}; rows={bad}")
    return df
