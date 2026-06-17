"""Equity adapter — corp actions, return outlier tags, survivorship.

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
    - MAD return outlier tagging with optional derived winsorized returns
    - Survivorship: delisted tickers preserved, flagged
    - Corporate actions: splits/divs handled via adj_factor
    """

    def prepare(self, raw_df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
        df = raw_df.copy()
        if "as_of_date" in df.columns:
            df["as_of_date"] = pd.to_datetime(df["as_of_date"], errors="coerce")

        # ── Price level + dividend handling ──
        # The dividend-aware path (loader emits a `dividend` column) is PIT-correct:
        # the price level stays the split-adjusted close (actually-traded), and the
        # dividend enters the RETURN as a same-day add-back (below). A dividend is
        # known on its own ex-date, so a total return on day t carries no look-ahead.
        # adj_factor (Adj Close / Close, dividend-only) is kept only for diagnostics.
        #
        # Legacy / non-dividend path (no `dividend` column): preserve the prior rule —
        # never trust Yahoo's retro Adj Close as PIT, and WARN on any material
        # unexplained adj_factor, since we ignore it when forming price_std.
        has_dividend_col = "dividend" in df.columns
        if "raw_close" in df.columns and "adj_factor" in df.columns:
            df["adjusted_price_provider"] = df["raw_close"] * df["adj_factor"]
            if has_dividend_col:
                df["price_std"] = df["raw_close"]
                df["price_adjustment_warning"] = False
            else:
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

        # ── Dividend (PIT): cash dividend with ex-date == t, known at t ──
        if has_dividend_col:
            df["dividend"] = pd.to_numeric(df["dividend"], errors="coerce").fillna(0.0)
        else:
            df["dividend"] = 0.0
        df["dividend_pit_applied"] = bool(has_dividend_col and "adj_factor" in df.columns)

        # ── Returns: PIT total return = price change + same-day dividend yield ──
        # return_total[t] = (P[t] + D[t]) / P[t-1] - 1, where D[t] is the ex-date
        # dividend. Reduces to a plain price return on non-ex-div days (D=0).
        if "symbol" in df.columns:
            df = df.sort_values(["symbol", "as_of_date"])
            prev_close = df.groupby("symbol")["price_std"].shift(1)
        else:
            df = df.sort_values("as_of_date")
            prev_close = df["price_std"].shift(1)
        df["return_price"] = df["price_std"] / prev_close - 1          # ex-dividend (diagnostic)
        df["return_raw"] = (df["price_std"] + df["dividend"]) / prev_close - 1  # total return
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

        # NOTE: PIT-MAD return clip is intentionally NOT called here.
        # Call apply_return_clip(df) separately (run_pipeline does this) so the
        # clip is a distinct, CDC-observable stage. Hiding it inside prepare()
        # made before/after invisible to the change ledger.

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
            "return_action": self._return_action(),
            "derived_return_col": self._derived_return_col(),
            "metrics_return_col": self.cfg.get("metrics_return_col", "return_std"),
            "n_trials": self.cfg.get("n_trials", 40),
            "psi_threshold": self.cfg.get("psi_threshold", 0.25),
            "regime_axes": self.cfg.get("regime_axes", ["vol_regime"]),
            "event_flags": [],
        }

        return df, cfg

    def apply_return_clip(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply PIT-MAD return outlier policy as a separate, CDC-visible stage.

        Called by run_pipeline AFTER prepare() so the clip is a distinct hop in
        the stage chain (adapter → return_clip → validators) and CDC can diff the
        before/after. Formerly hidden inside prepare() which made it invisible.
        """
        return self._pit_mad_clip(
            df,
            "return_std",
            k=self.cfg.get("outlier_k", 5.0),
            min_periods=self.cfg.get("outlier_min_periods", 20),
            action=self._return_action(),
            derived_col=self._derived_return_col(),
        )

    def validate_clips(
        self,
        df: pd.DataFrame,
        validation_df: pd.DataFrame,
        agree_tol: float = 0.02,
    ) -> pd.DataFrame:
        """Cross-provider validation gate for return outlier tags.

        For each row where _return_outlier_flag=True, compute the validation
        provider's return for the same (symbol, date) and compare to return_raw:

          |val_return - return_raw| <= agree_tol  →  provider_confirmed genuine event
          providers disagree significantly         →  provider_conflict, needs review
          validation provider has no data          →  leave unreviewed

        Args:
            validation_df: DataFrame with [as_of_date, symbol, raw_close] from
                           a second provider (e.g. StooqLoader.fetch()).
            agree_tol: max absolute return difference to count as agreement (default 0.02 = 2%).
        """
        if validation_df.empty or "_return_outlier_flag" not in df.columns:
            return df

        df = df.copy()

        # Compute per-symbol daily returns from the validation provider.
        val = validation_df[["as_of_date", "symbol", "raw_close"]].copy()
        val["as_of_date"] = pd.to_datetime(val["as_of_date"]).dt.tz_localize(None)
        val = val.sort_values(["symbol", "as_of_date"])
        val["val_return"] = val.groupby("symbol")["raw_close"].pct_change()

        # Build lookup: (symbol, date_str) → val_return
        val["_date_str"] = val["as_of_date"].dt.date.astype(str)
        val_lookup: dict[tuple, float] = {}
        for _, row in val.dropna(subset=["val_return"]).iterrows():
            val_lookup[(str(row["symbol"]), str(row["_date_str"]))] = float(row["val_return"])

        # Normalize df dates for matching.
        df_dates = pd.to_datetime(df["as_of_date"], utc=True).dt.tz_localize(None).dt.date.astype(str)

        flagged_idx = df.index[df["_return_outlier_flag"].fillna(False)]
        for idx in flagged_idx:
            sym = str(df.at[idx, "symbol"])
            date_str = df_dates.at[idx]
            val_ret = val_lookup.get((sym, date_str))

            if val_ret is None:
                # No second-provider data — leave the tag unreviewed.
                continue

            raw_ret = df.at[idx, "return_raw"]
            if raw_ret is None or (isinstance(raw_ret, float) and np.isnan(raw_ret)):
                continue

            if abs(val_ret - raw_ret) <= agree_tol:
                # Both providers agree the return was large — genuine event.
                df.at[idx, "_return_outlier_reason"] = "cross_provider_validated"
                df.at[idx, "_return_validation_status"] = "provider_confirmed"
                df.at[idx, "_return_outlier_evidence"] = f"provider_return={val_ret:.4f}"
            else:
                # Providers disagree — keep the tag visible for review.
                df.at[idx, "_return_outlier_reason"] = (
                    f"provider_conflict(stooq={val_ret:.4f},raw={raw_ret:.4f})"
                )
                df.at[idx, "_return_validation_status"] = "needs_review"
                df.at[idx, "_return_outlier_evidence"] = f"provider_return={val_ret:.4f}"

        return df

    def _return_action(self) -> str:
        action = self.cfg.get("return_action")
        if action is None:
            action = (self.cfg.get("outlier_policy") or {}).get("return_action", "tag_only")
        allowed = {"tag_only", "derive_winsorized", "mutate_after_validation"}
        if action not in allowed:
            raise ValueError(f"Unsupported outlier_policy.return_action={action!r}")
        return str(action)

    def _derived_return_col(self) -> str:
        return str(
            self.cfg.get("derived_return_col")
            or (self.cfg.get("outlier_policy") or {}).get("derived_return_col")
            or "return_winsorized"
        )

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
        window: int = 63,
        action: str = "tag_only",
        derived_col: str = "return_winsorized",
    ) -> pd.DataFrame:
        """Tag return outliers using prior observations only.

        Uses a rolling window (default 63 bars ≈ 1 quarter) rather than expanding
        so MAD tracks the current volatility regime. Expanding MAD dilutes historical
        calm periods into the estimate, causing genuine earnings/event-driven returns
        to be tagged as false outliers (e.g. TSLA +22% on earnings day).
        Still PIT: uses shift(1) so no future data bleeds in.
        """
        if col not in df.columns:
            return df

        df = df.copy()
        df["_return_outlier_flag"] = False
        df["_return_outlier_reason"] = ""
        df["_return_outlier_policy"] = action
        df["_return_outlier_evidence"] = ""
        df["_return_clip_lower"] = np.nan
        df["_return_clip_upper"] = np.nan
        df["_return_validation_status"] = "unreviewed"
        if action in {"derive_winsorized", "mutate_after_validation"}:
            df[derived_col] = df[col]

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
            median = prior.rolling(window=window, min_periods=min_periods).median()
            mad = (prior - median).abs().rolling(window=window, min_periods=min_periods).median()
            threshold = k * mad * 1.4826
            upper = median + threshold
            lower = median - threshold
            outliers = (series > upper) | (series < lower)
            outliers = outliers.fillna(False)

            outlier_idx = outliers[outliers].index
            if len(outlier_idx) == 0:
                continue

            df.loc[outlier_idx, "_return_outlier_flag"] = True
            df.loc[outlier_idx, "_return_outlier_reason"] = "pit_mad_outlier"
            df.loc[outlier_idx, "_return_clip_lower"] = lower.loc[outlier_idx].astype(float)
            df.loc[outlier_idx, "_return_clip_upper"] = upper.loc[outlier_idx].astype(float)

            if action in {"derive_winsorized", "mutate_after_validation"}:
                df.loc[outlier_idx, derived_col] = [
                    np.clip(df.loc[row_idx, col], lower.loc[row_idx], upper.loc[row_idx])
                    for row_idx in outlier_idx
                ]

        return df
