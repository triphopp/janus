"""Equity adapter — corp actions, MAD clip, survivorship.

Handles: raw→adjusted price, delisting flags, corporate action adjustments.
Generic — no specific ticker names in this file.
"""

from typing import Tuple

import numpy as np
import pandas as pd

from .base import AdapterBase


class EquityAdapter(AdapterBase):
    """Prepare equity data for core pipeline.

    Key concerns:
    - Adjusted price: raw_close * adj_factor_at_t (PIT — using data known at t only)
    - MAD outlier clipping
    - Survivorship: delisted tickers preserved, flagged
    - Corporate actions: splits/divs handled via adj_factor
    """

    def prepare(self, raw_df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
        df = raw_df.copy()

        # ── Build PIT-adjusted price ──
        # CRITICAL: adj_factor at time t must only use info known at t.
        # Yahoo's Adj Close uses future splits/divs → use raw_close * adj_factor instead.
        if "raw_close" in df.columns and "adj_factor" in df.columns:
            df["price_std"] = df["raw_close"] * df["adj_factor"]
        elif "raw_close" in df.columns:
            df["price_std"] = df["raw_close"]
        elif "price" in df.columns:
            df["price_std"] = df["price"]
        else:
            raise ValueError("No price column found in equity raw data")

        # ── Returns ──
        if "symbol" in df.columns:
            df = df.sort_values(["symbol", "as_of_date"])
            df["return_std"] = df.groupby("symbol")["price_std"].pct_change()
        else:
            df = df.sort_values("as_of_date")
            df["return_std"] = df["price_std"].pct_change()

        # ── Realized volatility ──
        vol_window = self.cfg.get("vol_window", 21)
        if "symbol" in df.columns:
            df["vol_std"] = df.groupby("symbol")["return_std"].transform(
                lambda x: x.rolling(vol_window, min_periods=5).std()
            )
        else:
            df["vol_std"] = df["return_std"].rolling(vol_window, min_periods=5).std()

        # ── Volume ──
        vol_col = self.cfg.get("vol_col_cfg", "volume")
        if vol_col in df.columns:
            df["volume_std"] = df[vol_col]

        # ── Survivorship flag ──
        if "is_delisted" in df.columns:
            df["survivor_flag"] = df["is_delisted"]

        # ── MAD outlier clip ──
        df = self._mad_clip(df, "return_std", k=self.cfg.get("outlier_k", 5.0))

        # ── Build cfg for core ──
        cfg = {
            **self.cfg,
            "price_col": "price_std",
            "vol_col": "vol_std",
            "return_col": "return_std",
            "vol_window": vol_window,
            "trend_window": self.cfg.get("trend_window", 126),
            "regime_axes": self.cfg.get("regime_axes", ["vol_regime"]),
            "event_flags": [],
        }

        return df, cfg

    @staticmethod
    def _mad_clip(df: pd.DataFrame, col: str, k: float = 5.0) -> pd.DataFrame:
        """Clip column at median ± k * MAD."""
        if col not in df.columns:
            return df
        median = df[col].median()
        mad = (df[col] - median).abs().median()
        if mad == 0:
            return df
        upper = median + k * mad * 1.4826
        lower = median - k * mad * 1.4826
        df[col] = df[col].clip(lower, upper)
        return df
