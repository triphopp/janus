"""Equity provider A loader (POC: Yahoo Finance).

CRITICAL: Adj Close is retroactively recalculated on every split/div.
Store raw_close + adj_factor separately. Backtest uses raw at time t.
Delisted tickers disappear from feed — maintain separate delisting list.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .base import ProviderBase
from .versioned_cache import add_availability_columns


class EquityLoaderA(ProviderBase):
    """Equity provider A — Yahoo Finance for POC/testing.

    PIT caveats that this loader MUST handle:
    1. Adj Close uses future information → store raw_close + adj_factor separately
    2. Delisted tickers vanish from yfinance → need external delisting list
    3. Survivorship: never drop a delisted ticker's history
    """

    def __init__(self, delisting_list: Optional[list[str]] = None):
        """
        Args:
            delisting_list: known delisted symbols to preserve in survivorship
        """
        self._delisted: set[str] = set(delisting_list or [])

    def fetch(self, symbol: str, start, end) -> pd.DataFrame:
        """Fetch from Yahoo → return standardized equity DataFrame.

        Stores: raw_close, adj_factor, volume, is_delisted per as_of_date.
        """
        try:
            import yfinance as yf  # POC only - swap at ingestion layer for production
        except ImportError as exc:
            raise ImportError(
                "EquityLoaderA requires yfinance. Install project dependencies "
                "or use a non-yfinance provider."
            ) from exc

        cache_dir = Path("outputs/cache/yfinance")
        cache_dir.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(cache_dir))

        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start, end=end, auto_adjust=False)

        if df.empty:
            return pd.DataFrame()

        df = df.reset_index()
        df.rename(columns={"Date": "as_of_date"}, inplace=True)

        # Compute adj_factor = Adj Close / Close when provider supplies both.
        # If not supplied, preserve raw prices and make the missing adjustment explicit.
        if "Adj Close" in df.columns and "Close" in df.columns:
            df["adj_factor"] = df["Adj Close"] / df["Close"]
        else:
            df["adj_factor"] = 1.0
        df["raw_close"] = df["Close"]

        # Standardize output
        out = pd.DataFrame({
            "as_of_date":  pd.to_datetime(df["as_of_date"]),
            "symbol":      symbol,
            "raw_close":   df["raw_close"],
            "adj_factor":  df["adj_factor"],
            "volume":      df["Volume"].fillna(0).astype(int),
            "is_delisted": False,
            "provider":    "yfinance",
        })
        out = add_availability_columns(
            out,
            data_type="equity_price",
            cfg={"available_at_lag": {"equity_price": "3h"}},
        )

        # If symbol is in delisting list, mark rows after last available date
        if symbol in self._delisted:
            last_date = out["as_of_date"].max()
            # For backtest purposes, the data itself is valid PIT
            out["is_delisted"] = out["as_of_date"] == last_date

        return out

    def list_expired(self, root: str, asof) -> list:
        """Equities don't expire — return empty."""
        return []
