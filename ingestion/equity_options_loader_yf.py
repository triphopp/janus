"""Equity option-chain loader (yfinance).

IMPORTANT — snapshot only. yfinance exposes the CURRENT option chain
(`Ticker.option_chain(expiry)`) but NO option price history. Every row therefore
carries as_of_date = today. A walk-forward backtest over a past date range cannot be
built from this source; the coverage-SLA and min-sample gates downstream will (correctly)
flag the single-day snapshot as not backtestable. Swap in a real vendor feed (ORATS,
OptionMetrics, exchange settlement) for historical chains.

Output is one row per (expiry, right, strike) with the EQUITY_OPTIONS chain schema the
equity_options adapter + contract expect:
    as_of_date, symbol, expiry, right, strike, price, underlying_price,
    raw_close, adj_factor, iv_provided, volume, open_interest, instrument_type, provider
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .base import ProviderBase
from .versioned_cache import add_availability_columns


_WIDE_SPREAD_THRESHOLD = 0.5


class EquityOptionsLoaderYF(ProviderBase):
    """Yahoo Finance option-chain provider (snapshot)."""

    def __init__(self, max_expiries: Optional[int] = None):
        # cap how many expiries to pull (yfinance is one HTTP call per expiry)
        self.max_expiries = max_expiries

    def list_expired(self, root: str, asof) -> list:
        # yfinance exposes only live (unexpired) expiries; no expired-series archive.
        return []

    def fetch(self, symbol: str, start, end) -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError as e:
            raise ImportError(
                "yfinance is required for the equity-options yfinance provider. "
                "pip install yfinance, or use a vendor provider."
            ) from e

        tkr = yf.Ticker(symbol)
        expiries = list(tkr.options or [])
        if not expiries:
            raise ValueError(
                f"yfinance returned no option expiries for '{symbol}'. Index symbols "
                f"like ^SPX have no usable chain here — use a vendor feed."
            )
        if self.max_expiries:
            expiries = expiries[: self.max_expiries]

        # underlying spot — snapshot, as of now
        spot = self._spot(tkr)
        as_of = pd.Timestamp.utcnow().tz_localize(None).normalize()

        frames: list[pd.DataFrame] = []
        for exp in expiries:
            try:
                oc = tkr.option_chain(exp)
            except Exception:
                continue
            for right, leg in (("C", oc.calls), ("P", oc.puts)):
                if leg is None or leg.empty:
                    continue
                frames.append(self._leg_frame(leg, right, exp))

        if not frames:
            raise ValueError(f"no option-chain rows parsed for '{symbol}'")

        chain = pd.concat(frames, ignore_index=True)
        out = pd.DataFrame({
            "as_of_date":        as_of,
            "symbol":            symbol,
            "expiry":            pd.to_datetime(chain["expiry"]),
            "right":             chain["right"].astype("string"),
            "strike":            chain["strike"].astype("float64"),
            "price":             chain["price"].astype("float64"),
            "price_source":      chain["price_source"].astype("string"),
            "bid":               chain["bid"].astype("float64"),
            "ask":               chain["ask"].astype("float64"),
            "bid_ask_spread":    chain["bid_ask_spread"].astype("float64"),
            "relative_spread":   chain["relative_spread"].astype("float64"),
            "_wide_spread_flag": chain["_wide_spread_flag"].astype(bool),
            "underlying_price":  float(spot),
            "raw_close":         float(spot),
            "adj_factor":        1.0,
            "iv_provided":       chain["iv"].astype("float64"),
            "volume":            chain["volume"].fillna(0).astype("int64"),
            "open_interest":     chain["open_interest"].fillna(0).astype("int64"),
            "instrument_type":   "option",
            "provider":          "yfinance",
        })
        # drop unpriced rows (no bid/ask and no last) — they cannot be validated
        out = out[out["price"].notna() & (out["price"] >= 0)].reset_index(drop=True)

        out = add_availability_columns(
            out,
            data_type="equity_price",
            cfg={
                "available_at_lag": {"equity_price": "0h"},  # snapshot is live as captured
                "exchange_tz": "America/New_York",
                "market_close_time": "16:00",
            },
        )
        out["decision_time"] = out["available_at"]
        return out

    @staticmethod
    def _spot(tkr) -> float:
        # fast_info first, fall back to last daily close
        try:
            fi = tkr.fast_info
            px = fi.get("lastPrice") or fi.get("last_price")
            if px:
                return float(px)
        except Exception:
            pass
        hist = tkr.history(period="5d")
        if hist is not None and not hist.empty:
            return float(hist["Close"].dropna().iloc[-1])
        raise ValueError("could not resolve underlying spot price")

    @staticmethod
    def _leg_frame(leg: pd.DataFrame, right: str, exp) -> pd.DataFrame:
        n = len(leg)
        bid_col = leg["bid"] if "bid" in leg.columns else [None] * n
        ask_col = leg["ask"] if "ask" in leg.columns else [None] * n
        last_col = leg["lastPrice"] if "lastPrice" in leg.columns else [np.nan] * n

        prices, sources, bids_out, asks_out, spreads, rel_spreads, wide_flags = (
            [], [], [], [], [], [], []
        )
        for b, a, l in zip(bid_col, ask_col, last_col):
            b_f = float(b) if pd.notna(b) else None
            a_f = float(a) if pd.notna(a) else None
            l_f = float(l) if pd.notna(l) else np.nan
            bids_out.append(b_f if b_f is not None else np.nan)
            asks_out.append(a_f if a_f is not None else np.nan)
            if b_f is not None and a_f is not None and b_f > 0 and a_f > 0 and a_f >= b_f:
                mid = (b_f + a_f) / 2.0
                sprd = a_f - b_f
                rel = sprd / mid if mid > 0 else np.nan
                prices.append(mid)
                sources.append("mid")
                spreads.append(sprd)
                rel_spreads.append(rel)
                wide_flags.append(not np.isnan(rel) and rel >= _WIDE_SPREAD_THRESHOLD)
            elif not np.isnan(l_f):
                prices.append(l_f)
                sources.append("last")
                spreads.append(np.nan)
                rel_spreads.append(np.nan)
                wide_flags.append(False)
            else:
                prices.append(np.nan)
                sources.append("missing")
                spreads.append(np.nan)
                rel_spreads.append(np.nan)
                wide_flags.append(False)

        return pd.DataFrame({
            "expiry": exp,
            "right": right,
            "strike": leg["strike"].to_numpy(),
            "price": prices,
            "price_source": pd.array(sources, dtype="string"),
            "bid": bids_out,
            "ask": asks_out,
            "bid_ask_spread": spreads,
            "relative_spread": rel_spreads,
            "_wide_spread_flag": wide_flags,
            "iv": leg["impliedVolatility"].to_numpy() if "impliedVolatility" in leg else np.nan,
            "volume": leg["volume"].to_numpy() if "volume" in leg else 0,
            "open_interest": leg["openInterest"].to_numpy() if "openInterest" in leg else 0,
        })
