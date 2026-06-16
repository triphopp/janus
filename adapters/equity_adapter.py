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
        if "as_of_date" in df.columns:
            df["as_of_date"] = pd.to_datetime(df["as_of_date"], errors="coerce")

        # ── Build PIT-adjusted price ──
        # CRITICAL: adj_factor at time t must only use info known at t.
        # Yahoo's Adj Close uses future splits/divs; do not treat it as PIT truth.
        if "raw_close" in df.columns and "adj_factor" in df.columns:
            df["adjusted_price_provider"] = df["raw_close"] * df["adj_factor"]
            adj_is_pit = df.get("adj_factor_is_pit", False)
            if not isinstance(adj_is_pit, pd.Series):
                adj_is_pit = pd.Series(bool(adj_is_pit), index=df.index)
            adj_is_pit = adj_is_pit.fillna(False).astype(bool)
            use_retro = bool(self.cfg.get("allow_retro_adjusted_prices", False))
            use_adjustment = adj_is_pit | use_retro
            df["price_std"] = np.where(use_adjustment, df["adjusted_price_provider"], df["raw_close"])
            df["price_adjustment_warning"] = (~use_adjustment) & (df["adj_factor"].fillna(1.0) != 1.0)
        elif "raw_close" in df.columns:
            df["price_std"] = df["raw_close"]
            df["price_adjustment_warning"] = False
        elif "price" in df.columns:
            df["price_std"] = df["price"]
            df["price_adjustment_warning"] = False
        else:
            raise ValueError("No price column found in equity raw data")

        # ── Returns ──
        if "symbol" in df.columns:
            df = df.sort_values(["symbol", "as_of_date"])
            df["return_raw"] = df.groupby("symbol")["price_std"].pct_change()
        else:
            df = df.sort_values("as_of_date")
            df["return_raw"] = df["price_std"].pct_change()
        df["return_std"] = df["return_raw"]

        # ── Realized volatility ──
        vol_window = self.cfg.get("vol_window", 21)
        min_periods = min(5, vol_window)
        if "symbol" in df.columns:
            df["vol_std"] = df.groupby("symbol")["return_std"].transform(
                lambda x: x.rolling(vol_window, min_periods=min_periods).std()
            )
        else:
            df["vol_std"] = df["return_std"].rolling(vol_window, min_periods=min_periods).std()

        # ── Volume ──
        vol_col = self.cfg.get("volume_col", self.cfg.get("vol_col_cfg", "volume"))
        if vol_col in df.columns:
            df["volume_std"] = df[vol_col]

        # ── Survivorship flag ──
        if "is_delisted" in df.columns:
            df["survivor_flag"] = df["is_delisted"]

        # ── PIT MAD return outlier clip ──
        df = self._pit_mad_clip(
            df,
            "return_std",
            k=self.cfg.get("outlier_k", 5.0),
            min_periods=self.cfg.get("outlier_min_periods", 20),
        )

        # ── Build cfg for core ──
        cfg = {
            **self.cfg,
            "price_col": "price_std",
            "vol_col": "vol_std",
            "volume_col": vol_col,
            "return_col": "return_std",
            "vol_window": vol_window,
            "trend_window": self.cfg.get("trend_window", 126),
            "n_folds": self.cfg.get("n_folds", 8),
            "purge_bars": self.cfg.get("purge_bars", 5),
            "event_embargo_bars": self.cfg.get("event_embargo_bars", 2),
            "min_volume": self.cfg.get("min_volume"),
            "outlier_k": self.cfg.get("outlier_k", 5.0),
            "n_trials": self.cfg.get("n_trials", 40),
            "psi_threshold": self.cfg.get("psi_threshold", 0.25),
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

    @staticmethod
    def _pit_mad_clip(
        df: pd.DataFrame,
        col: str,
        k: float = 5.0,
        min_periods: int = 20,
    ) -> pd.DataFrame:
        """Clip return outliers using prior observations only, with flags."""
        if col not in df.columns:
            return df

        df = df.copy()
        df["_return_outlier_flag"] = False
        df["_return_outlier_reason"] = ""

        if "symbol" in df.columns:
            groups = df.groupby("symbol").groups.values()
        elif "product_id" in df.columns:
            groups = df.groupby("product_id").groups.values()
        else:
            groups = [df.index]

        for grp_idx in groups:
            idx = list(grp_idx)
            series = df.loc[idx, col].astype(float)
            prior = series.shift(1)
            median = prior.expanding(min_periods=min_periods).median()
            mad = (prior - median).abs().expanding(min_periods=min_periods).median()
            threshold = k * mad * 1.4826
            upper = median + threshold
            lower = median - threshold
            outliers = (series > upper) | (series < lower)
            outliers = outliers.fillna(False)

            outlier_idx = outliers[outliers].index
            if len(outlier_idx) == 0:
                continue

            df.loc[outlier_idx, "_return_outlier_flag"] = True
            df.loc[outlier_idx, "_return_outlier_reason"] = "pit_mad_clip;"
            df.loc[outlier_idx, col] = [
                np.clip(df.loc[row_idx, col], lower.loc[row_idx], upper.loc[row_idx])
                for row_idx in outlier_idx
            ]

        return df
