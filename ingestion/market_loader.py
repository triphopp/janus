"""Market index loader — broad market returns for cross-clip validation.

Uses yfinance (already a project dependency) to fetch a market proxy (default SPY).
Purpose: if the market moved significantly on a day flagged by pit_mad_clip, the
individual stock's large return is likely a genuine systemic event, not a bad tick.

This avoids the Cloudflare block that prevents plain HTTP access to Stooq.
"""

from __future__ import annotations

import pandas as pd


def fetch(
    market_symbol: str = "SPY",
    start: object = None,
    end: object = None,
) -> pd.DataFrame:
    """Fetch daily close prices for a market index via yfinance.

    Returns DataFrame with columns [as_of_date, symbol, raw_close, provider].
    Returns empty DataFrame on any error — callers must handle gracefully.

    Args:
        market_symbol: yfinance ticker for the market proxy (default 'SPY').
        start: start date (str, date, or Timestamp).
        end: end date (str, date, or Timestamp).
    """
    try:
        import yfinance as yf
    except ImportError:
        return pd.DataFrame()

    try:
        raw = yf.download(
            market_symbol,
            start=str(pd.Timestamp(start).date()),
            end=str((pd.Timestamp(end) + pd.Timedelta(days=1)).date()),
            auto_adjust=False,
            progress=False,
        )
    except Exception:
        return pd.DataFrame()

    if raw is None or raw.empty:
        return pd.DataFrame()

    close_col = "Close"
    if isinstance(raw.columns, pd.MultiIndex):
        close_col = ("Close", market_symbol)

    if close_col not in raw.columns:
        return pd.DataFrame()

    df = raw[[close_col]].copy()
    df.columns = ["raw_close"]
    df.index.name = "as_of_date"
    df = df.reset_index()
    df["as_of_date"] = pd.to_datetime(df["as_of_date"]).dt.tz_localize(None)
    df["raw_close"] = pd.to_numeric(df["raw_close"], errors="coerce")
    df = df.dropna(subset=["raw_close"])
    df["symbol"] = market_symbol
    df["provider"] = f"yfinance:{market_symbol}"
    return df[["as_of_date", "symbol", "raw_close", "provider"]].reset_index(drop=True)
