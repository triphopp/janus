"""Equity provider A loader (POC: Yahoo Finance).

PROVIDER REALITY (yfinance >= 1.x, verified 1.3.0):
  `Ticker.history(auto_adjust=False)` returns:
    - Close      = SPLIT-adjusted, NOT dividend-adjusted (splits always back-adjusted,
                   even with auto_adjust=False — auto_adjust only toggles dividends).
    - Adj Close  = split + dividend adjusted.
  So Adj Close / Close = dividend-only factor; splits are invisible in that ratio.

Consequences this loader must surface (otherwise downstream lies):
  - `raw_close` (= Close) is split-adjusted → correct for RETURNS (no fake split-day
    jump) but it is NOT the true historical traded price (retroactive for price LEVELS).
  - `adj_factor` (= Adj Close / Close) captures DIVIDENDS only — not splits.
  - `raw_close_unadj` = true traded price, reconstructed from the Stock Splits column
    (raw_close * product of split ratios with ex-date strictly after t). Use this for
    any level/threshold strategy; it must be combined with a PIT-known split factor.
  - `split_factor` = future cumulative split ratio applied by the provider at each date.
    It is known only retroactively, so price-LEVEL use of raw_close leaks future splits.

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
        # actions=True → carries the "Stock Splits"/"Dividends" columns we need to
        # reconstruct the true unadjusted price and a PIT-aware split factor.
        df = ticker.history(start=start, end=end, auto_adjust=False, actions=True)

        if df.empty:
            return pd.DataFrame()

        df = df.reset_index()
        df.rename(columns={"Date": "as_of_date"}, inplace=True)

        # Compute adj_factor = Adj Close / Close when provider supplies both.
        # NOTE: with yfinance >= 1.x this is a DIVIDEND-only factor — Close is already
        # split-adjusted, so splits do not appear here (see module docstring).
        if "Adj Close" in df.columns and "Close" in df.columns:
            df["adj_factor"] = df["Adj Close"] / df["Close"]
            adj_factor_source = "yfinance_adj_close_dividend_only"
        else:
            df["adj_factor"] = 1.0
            adj_factor_source = "none"
        df["raw_close"] = df["Close"]

        # ── Reconstruct true unadjusted price from the split column ──
        # provider Close is split-adjusted: a price at date t has been divided by the
        # product of every split whose ex-date is strictly AFTER t. Multiply it back to
        # recover the price actually traded that day. split_factor is the retroactive
        # adjustment the provider baked in (== 1.0 when no later split exists).
        split_col = df["Stock Splits"] if "Stock Splits" in df.columns else pd.Series(0.0, index=df.index)
        split_ratio = pd.to_numeric(split_col, errors="coerce").replace(0.0, 1.0).fillna(1.0)
        # product of split ratios strictly after each row (rows assumed date-ascending)
        rev_cumprod_incl = split_ratio[::-1].cumprod()[::-1]
        future_split_factor = rev_cumprod_incl / split_ratio
        df["split_factor"] = future_split_factor.to_numpy()
        df["raw_close_unadj"] = df["raw_close"] * df["split_factor"]
        df["split_ratio"] = split_ratio.to_numpy()

        # Standardize output
        out = pd.DataFrame({
            "as_of_date":  pd.to_datetime(df["as_of_date"]),
            "symbol":      symbol,
            "raw_close":   df["raw_close"],            # split-adjusted (returns-correct)
            "raw_close_unadj": df["raw_close_unadj"],  # true traded price (level-correct)
            "split_factor": df["split_factor"],        # retroactive split adj == future-only
            "split_ratio":  df["split_ratio"],         # split ratio on this row's ex-date
            "adj_factor":  df["adj_factor"],
            "adj_factor_source": adj_factor_source,
            "adj_factor_is_pit": False,
            # int64, not int: high-volume symbols (SPX/SPY/QQQ) exceed 2^31 and the
            # platform-default int is int32 on Windows → silent overflow to negative.
            "volume":      df["Volume"].fillna(0).astype("int64"),
            "is_delisted": False,
            "provider":    "yfinance",
        })
        out = add_availability_columns(
            out,
            data_type="equity_price",
            cfg={
                "available_at_lag": {"equity_price": "3h"},
                "exchange_tz": "America/New_York",
                "market_close_time": "16:00",
            },
        )

        # ── PIT decision time ──
        # A price bar's earliest actionable moment is when it became known (available_at).
        # Stamping decision_time here lets the pipeline's PIT-timing guard actually run for
        # price-only equity runs instead of reporting "not_checked" (missing decision_time).
        # Strategies that decide later should overwrite this with their true decision time.
        out["decision_time"] = out["available_at"]

        # If symbol is in delisting list, mark rows after last available date
        if symbol in self._delisted:
            last_date = out["as_of_date"].max()
            # For backtest purposes, the data itself is valid PIT
            out["is_delisted"] = out["as_of_date"] == last_date

        return out

    def list_expired(self, root: str, asof) -> list:
        """Equities don't expire — return empty."""
        return []
