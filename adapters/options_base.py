"""OptionsBase — shared options logic (~65% reuse).

Handles: IV surface, PCP validation, VRP, skew, Greeks (via core.greeks),
provided-IV cross-check. Subclasses override only asset-specific parts:
- equity_options: BS-Merton, strike-adjust, NYSE close
- futures_options: Black-76, roll, term structure event regimes
"""

from typing import Tuple

import numpy as np
import pandas as pd

from .base import AdapterBase

# Import core modules
from core import pricing as _pricing
from core import greeks as _greeks
from core import dte as _dte
from core.progress import progress_iter, should_show_progress


class OptionsBase(AdapterBase):
    """Base class for options adapters — shared logic for IV, Greeks, PCP, VRP.

    Subclasses must override prepare() but can call inherited methods.
    """

    OPTION_REQUIRED_COLUMNS = ("as_of_date", "expiry", "right", "strike", "price")

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._config_warnings: list = []
        self._option_quality: dict = {
            "universe_drop_rows": 0,
            "universe_drop_by_reason": {},
        }

    def _normalize_option_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize option-chain dtypes used by shared math."""
        df = df.copy()

        for col in ("as_of_date", "expiry", "delivery_month", "available_at", "ingested_at"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")

        if "right" in df.columns:
            right = df["right"].astype("string").str.upper()
            df["right"] = right.where(right.isin(["C", "P"]), pd.NA)

        numeric_cols = (
            "price", "option_price", "underlying_price", "price_std", "raw_close",
            "adj_factor", "strike", "iv_provided", "delta_provided", "F", "S",
        )
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    def _option_mask(self, df: pd.DataFrame) -> pd.Series:
        """Return True for rows that are option contracts."""
        inferred = pd.Series(False, index=df.index)
        if "right" in df.columns and "strike" in df.columns:
            inferred = self._right_is_option(df["right"]) & df["strike"].notna()

        if "instrument_type" in df.columns:
            typed = self._instrument_type_is(df, "option")
            return typed | inferred

        return inferred

    def _future_mask(self, df: pd.DataFrame) -> pd.Series:
        """Return True for rows that are futures/underlying rows."""
        if "instrument_type" in df.columns:
            typed = self._instrument_type_is(df, "future")
            return typed & ~self._option_mask(df)
        return ~self._option_mask(df)

    @staticmethod
    def _right_is_option(series: pd.Series) -> pd.Series:
        """Fast C/P test that only case-folds non-canonical values."""
        canonical_cp = pd.Series(series.isin(["C", "P"]), index=series.index).fillna(False).astype(bool)
        mask = canonical_cp.copy()
        noncanonical = (
            pd.Series(series.notna(), index=series.index).fillna(False).astype(bool)
            & ~canonical_cp
        )
        if noncanonical.any():
            fallback = (
                series.loc[noncanonical]
                .astype("string")
                .str.upper()
                .isin(["C", "P"])
                .fillna(False)
                .astype(bool)
            )
            mask.loc[noncanonical] = fallback.to_numpy()
        return mask

    @staticmethod
    def _right_eq(series: pd.Series, value: str) -> pd.Series:
        """Fast option right equality for canonical C/P labels."""
        mask = pd.Series(series.eq(value), index=series.index).fillna(False).astype(bool)
        canonical_cp = pd.Series(series.isin(["C", "P"]), index=series.index).fillna(False).astype(bool)
        noncanonical = (
            pd.Series(series.notna(), index=series.index).fillna(False).astype(bool)
            & ~canonical_cp
        )
        if noncanonical.any():
            fallback = (
                series.loc[noncanonical]
                .astype("string")
                .str.upper()
                .eq(value)
                .fillna(False)
                .astype(bool)
            )
            mask.loc[noncanonical] = fallback.to_numpy()
        return mask

    @staticmethod
    def _instrument_type_is(df: pd.DataFrame, value: str) -> pd.Series:
        """Fast instrument_type equality for canonical option/future labels."""
        if "instrument_type" not in df.columns:
            return pd.Series(False, index=df.index)
        series = df["instrument_type"]
        mask = pd.Series(series.eq(value), index=df.index).fillna(False).astype(bool)
        canonical = pd.Series(series.isin(["option", "future"]), index=df.index).fillna(False).astype(bool)
        noncanonical = pd.Series(series.notna(), index=df.index).fillna(False).astype(bool) & ~canonical
        if noncanonical.any():
            fallback = (
                series.loc[noncanonical]
                .astype("string")
                .str.lower()
                .eq(value)
                .fillna(False)
                .astype(bool)
            )
            mask.loc[noncanonical] = fallback.to_numpy()
        return mask

    def _require_option_chain_schema(self, df: pd.DataFrame, context: str = "option chain") -> pd.Series:
        """Fail fast when an options adapter receives non-chain data."""
        missing_cols = [col for col in self.OPTION_REQUIRED_COLUMNS if col not in df.columns]
        if missing_cols:
            raise ValueError(
                f"{context} input is not an option chain; missing required columns: "
                f"{', '.join(missing_cols)}"
            )

        option_mask = self._option_mask(df)
        if not option_mask.any():
            raise ValueError(f"{context} input contains no option chain rows")

        required = list(self.OPTION_REQUIRED_COLUMNS)
        bad_nulls = [col for col in required if df.loc[option_mask, col].isna().any()]
        if bad_nulls:
            raise ValueError(
                f"{context} rows have null required option fields: {', '.join(bad_nulls)}"
            )

        return option_mask

    def _option_universe_cfg(self) -> dict:
        """Return optional option-chain filters without changing legacy defaults."""
        universe = dict(self.cfg.get("option_universe") or {})
        for key in ("min_dte_days", "max_dte_days", "min_option_price"):
            if key in self.cfg and key not in universe:
                universe[key] = self.cfg[key]
        # Accept deprecated iv_cap as alias only when canonical key is absent.
        if "max_iv" not in universe:
            legacy = self.cfg.get("iv_cap")
            if legacy is not None:
                universe["max_iv"] = legacy
                warning = (
                    "validation.iv_cap is deprecated for option universe filtering; "
                    "use option_universe.max_iv"
                )
                if warning not in self._config_warnings:
                    self._config_warnings.append(warning)
        return universe

    def _count_option_drop(
        self, reason: str, mask: pd.Series, option_mask: pd.Series
    ) -> None:
        n = int((mask & option_mask).sum())
        if n:
            by_reason = self._option_quality["universe_drop_by_reason"]
            by_reason[reason] = by_reason.get(reason, 0) + n

    def _max_iv_cfg(self):
        return self._option_universe_cfg().get("max_iv")

    def _delta_band_cfg(self) -> dict:
        universe = self._option_universe_cfg()
        band = universe.get("delta_band") if isinstance(universe.get("delta_band"), dict) else {}
        min_abs = band.get("min_abs_delta", band.get("min_abs"))
        max_abs = band.get("max_abs_delta", band.get("max_abs"))
        if min_abs is None and max_abs is None:
            return {}
        return {
            "min_abs_delta": float(min_abs) if min_abs is not None else None,
            "max_abs_delta": float(max_abs) if max_abs is not None else None,
        }

    def _has_usable_option_values(self, df: pd.DataFrame, col: str) -> bool:
        if col not in df.columns:
            return False
        option_mask = self._option_mask(df)
        if not option_mask.any():
            return False
        values = pd.to_numeric(df.loc[option_mask, col], errors="coerce")
        return bool(values.notna().any())

    def _filter_max_iv(self, df: pd.DataFrame, iv_col: str) -> pd.DataFrame:
        """Apply max IV to option rows while retaining underlying rows."""
        max_iv = self._max_iv_cfg()
        if max_iv is None or iv_col not in df.columns:
            return df

        option_mask = self._option_mask(df)
        if not option_mask.any():
            return df

        iv = pd.to_numeric(df[iv_col], errors="coerce")
        above_cap = option_mask & iv.notna() & (iv > float(max_iv))
        missing_iv = option_mask & iv.isna()
        self._count_option_drop("iv_above_cap", above_cap, option_mask)
        self._count_option_drop("iv_missing_or_unsolved", missing_iv, option_mask)
        dropped = int((above_cap | missing_iv).sum())
        self._option_quality["universe_drop_rows"] += dropped
        keep = (~option_mask) | (iv.notna() & (iv <= float(max_iv)))
        return df.loc[keep].copy()

    def _filter_delta_band(self, df: pd.DataFrame, delta_col: str) -> pd.DataFrame:
        """Apply abs(delta) band to option rows while retaining underlying rows."""
        band = self._delta_band_cfg()
        if not band or delta_col not in df.columns:
            return df

        option_mask = self._option_mask(df)
        if not option_mask.any():
            return df

        abs_delta = pd.to_numeric(df[delta_col], errors="coerce").abs()
        keep = pd.Series(True, index=df.index)
        min_abs = band.get("min_abs_delta")
        if min_abs is not None:
            passes = (~option_mask) | (abs_delta.notna() & (abs_delta >= float(min_abs)))
            self._count_option_drop("delta_below_min", option_mask & ~passes, option_mask)
            keep &= passes
        max_abs = band.get("max_abs_delta")
        if max_abs is not None:
            passes_max = (~option_mask) | (abs_delta.notna() & (abs_delta <= float(max_abs)))
            self._count_option_drop("delta_above_max", keep & ~passes_max, option_mask)
            keep &= passes_max
        dropped = int((option_mask & ~keep).sum())
        self._option_quality["universe_drop_rows"] += dropped
        return df.loc[keep].copy()

    def _filter_option_universe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply configured option-row filters while retaining underlying rows."""
        universe = self._option_universe_cfg()
        if not universe:
            return df

        option_mask = self._option_mask(df)
        if not option_mask.any():
            return df

        keep = pd.Series(True, index=df.index)
        dte_days = pd.to_numeric(df.get("dte_days"), errors="coerce")

        min_dte = universe.get("min_dte_days")
        if min_dte is not None and dte_days is not None:
            passes = (~option_mask) | (dte_days.notna() & (dte_days >= int(min_dte)))
            self._count_option_drop("dte_below_min", option_mask & ~passes, option_mask)
            keep &= passes

        max_dte = universe.get("max_dte_days")
        if max_dte is not None and dte_days is not None:
            passes = (~option_mask) | (dte_days.notna() & (dte_days <= int(max_dte)))
            self._count_option_drop("dte_above_max", option_mask & ~passes, option_mask)
            keep &= passes

        min_price = universe.get("min_option_price")
        if min_price is not None:
            premium_col = "option_price" if "option_price" in df.columns else "price"
            premium = pd.to_numeric(df[premium_col], errors="coerce")
            passes = (~option_mask) | (premium >= float(min_price))
            self._count_option_drop("premium_below_min", option_mask & ~passes, option_mask)
            keep &= passes

        max_spread = universe.get("max_relative_spread")
        if max_spread is not None and "relative_spread" in df.columns:
            spread = pd.to_numeric(df["relative_spread"], errors="coerce")
            passes = (~option_mask) | (spread.notna() & (spread <= float(max_spread)))
            self._count_option_drop("spread_above_max", option_mask & ~passes, option_mask)
            keep &= passes

        dropped_here = int((option_mask & ~keep).sum())
        self._option_quality["universe_drop_rows"] += dropped_here

        out = df.loc[keep].copy()

        # Provided IV/delta can narrow the universe before expensive pricing loops.
        # Solved IV and computed delta are filtered after those values exist.
        if self.cfg.get("iv_source", "solve") == "provided":
            out = self._filter_max_iv(out, "iv_provided")
        if self._has_usable_option_values(out, "delta_provided"):
            out = self._filter_delta_band(out, "delta_provided")

        return out

    def _flag_option_quality(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add silver-level quality flag columns. Rows are never dropped."""
        df = df.copy()
        opt = self._option_mask(df)

        hard_iv_cap = (self.cfg.get("option_quality") or {}).get("hard_iv_cap")
        iv_diff_threshold = float(
            (self.cfg.get("option_quality") or {}).get("iv_diff_threshold", 0.10)
        )

        df["_iv_quality_flag"] = False
        df["_iv_quality_reason"] = ""
        df["_delta_quality_flag"] = False
        df["_delta_quality_reason"] = ""
        df["_premium_quality_flag"] = False
        df["_premium_quality_reason"] = ""

        if not opt.any():
            return df

        # ── IV ───────────────────────────────────────────────────────────────
        if "iv" in df.columns:
            iv = pd.to_numeric(df["iv"], errors="coerce")

            mask = opt & iv.notna() & (iv <= 0)
            df.loc[mask, "_iv_quality_flag"] = True
            df.loc[mask, "_iv_quality_reason"] += "iv_non_positive;"

            if hard_iv_cap is not None:
                mask = opt & iv.notna() & (iv > float(hard_iv_cap))
                df.loc[mask, "_iv_quality_flag"] = True
                df.loc[mask, "_iv_quality_reason"] += "iv_above_hard_cap;"

            mask = opt & iv.isna()
            df.loc[mask, "_iv_quality_flag"] = True
            df.loc[mask, "_iv_quality_reason"] += "iv_unsolved;"

            if "iv_solved" in df.columns:
                iv_solved = pd.to_numeric(df["iv_solved"], errors="coerce")
                diff = (iv - iv_solved).abs()
                # Only near-the-money rows have a trustworthy price-inverted IV
                # (issue 025); deep ITM/OTM disagreement is an inversion artifact and
                # must not flag the authoritative exchange IV.
                invertible = (
                    df["iv_invertible"].fillna(False)
                    if "iv_invertible" in df.columns
                    else pd.Series(True, index=df.index)
                )
                mask = opt & diff.notna() & (diff > iv_diff_threshold) & invertible
                df.loc[mask, "_iv_quality_flag"] = True
                df.loc[mask, "_iv_quality_reason"] += "provided_iv_diff_above_threshold;"

        # ── Delta ─────────────────────────────────────────────────────────────
        if "delta" in df.columns and "right" in df.columns:
            delta = pd.to_numeric(df["delta"], errors="coerce")
            is_call = self._right_eq(df["right"], "C")
            is_put = self._right_eq(df["right"], "P")

            mask = opt & is_call & delta.notna() & (delta < 0)
            df.loc[mask, "_delta_quality_flag"] = True
            df.loc[mask, "_delta_quality_reason"] += "call_delta_negative;"

            mask = opt & is_put & delta.notna() & (delta > 0)
            df.loc[mask, "_delta_quality_flag"] = True
            df.loc[mask, "_delta_quality_reason"] += "put_delta_positive;"

            mask = opt & delta.notna() & (delta.abs() > 1)
            df.loc[mask, "_delta_quality_flag"] = True
            df.loc[mask, "_delta_quality_reason"] += "abs_delta_gt_one;"

        # ── Premium ───────────────────────────────────────────────────────────
        if "price" in df.columns and "strike" in df.columns and "right" in df.columns:
            price = pd.to_numeric(df["price"], errors="coerce")
            K = pd.to_numeric(df["strike"], errors="coerce")
            is_call = self._right_eq(df["right"], "C")
            is_put = self._right_eq(df["right"], "P")
            underlying_col = next(
                (c for c in ("F", "underlying_price", "spot") if c in df.columns), None
            )
            if underlying_col is not None:
                F = pd.to_numeric(df[underlying_col], errors="coerce")
                below = (
                    (opt & is_call & price.notna() & (price < (F - K).clip(lower=0) - 0.001))
                    | (opt & is_put & price.notna() & (price < (K - F).clip(lower=0) - 0.001))
                )
                df.loc[below, "_premium_quality_flag"] = True
                df.loc[below, "_premium_quality_reason"] += "premium_below_intrinsic;"

        return df

    def _row_underlying_value(self, row: pd.Series) -> float:
        """Pick the model underlying column for one option row."""
        for col in ("underlying_price", "S", "F", "price_std"):
            value = row.get(col, np.nan)
            if not pd.isna(value):
                return value
        return np.nan

    def _row_option_price(self, row: pd.Series) -> float:
        """Pick the option premium column for one option row."""
        value = row.get("option_price", np.nan)
        if not pd.isna(value):
            return value
        return row.get("price", np.nan)

    def compute_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute standardized returns from front-month futures price.

        Options DataFrame has many rows per day (one per strike/expiry), so
        pct_change() across mixed rows produces garbage. Instead, derive daily
        returns from the front-month futures price and broadcast to all rows.
        """
        df = df.copy()
        vol_window = self.cfg.get("vol_window", 21)

        if "as_of_date" in df.columns and "underlying_price" in df.columns:
            source_cols = ["as_of_date", "underlying_price"]
            if "instrument_type" in df.columns:
                source_mask = self._instrument_type_is(df, "future") & df["underlying_price"].notna()
                source = df.loc[source_mask, source_cols]
                if source.empty:
                    source = df.loc[df["underlying_price"].notna(), source_cols]
            else:
                source = df.loc[df["underlying_price"].notna(), source_cols]
            underlying = (
                source.sort_values(["as_of_date"], kind="mergesort")
                .drop_duplicates(["as_of_date"], keep="first")
                .set_index("as_of_date")["underlying_price"]
            )
            if not underlying.empty:
                underlying_ret = underlying.pct_change().rename("return_std")
                underlying_vol = (
                    underlying_ret.rolling(vol_window, min_periods=5).std().rename("vol_std")
                )
                df = df.join(underlying_ret, on="as_of_date")
                df = df.join(underlying_vol, on="as_of_date")
                return df

        # Front-month futures: earliest-expiry future per date, one row per date.
        if "instrument_type" in df.columns and self._future_mask(df).any():
            fut = (
                df[self._future_mask(df)]
                .sort_values(["as_of_date", "expiry"])
                .groupby("as_of_date")["price_std"]
                .first()
                .rename("_fut_price")
            )
            fut_ret = fut.pct_change().rename("return_std")
            fut_vol = fut_ret.rolling(vol_window, min_periods=5).std().rename("vol_std")

            df = df.join(fut_ret, on="as_of_date")
            df = df.join(fut_vol, on="as_of_date")
        else:
            # Fallback for pure-futures DataFrames
            df = df.sort_values(["as_of_date"])
            df["return_std"] = df.groupby("product_id", group_keys=False)["price_std"].pct_change()
            df["vol_std"] = df["return_std"].rolling(vol_window, min_periods=5).std()

        return df

    def build_iv_surface(self, df: pd.DataFrame) -> pd.DataFrame:
        """Build IV surface from provided or solved IV.

        Respects cfg['iv_source']:
        - 'provided': use iv_provided from exchange (validate first)
        - 'solve': solve IV ourselves from market prices

        Returns DataFrame with 'iv' column and validation flags.
        """
        df = df.copy()
        iv_source = self.cfg.get("iv_source", "solve")
        model = self.cfg.get("pricing_model", "black76")

        # Compute T (time to expiry) for all rows
        dte_cfg = self.cfg.get("dte", {})
        rf_rate = self.cfg.get("rf_rate", 0.05)
        div_yield = self.cfg.get("div_yield", 0.0)
        solver_bounds = tuple(self.cfg.get("iv_solver_bounds", (1e-4, 5.0)))

        option_mask = self._option_mask(df)
        if "expiry" in df.columns and "as_of_date" in df.columns:
            df["T"] = np.nan
            df["dte_days"] = np.nan
            if option_mask.any():
                option_rows = df.loc[option_mask]
                df.loc[option_mask, "T"] = _dte.compute_dte_series(option_rows, dte_cfg)
                dte_days = (option_rows["expiry"] - option_rows["as_of_date"]).dt.days
                dte_days = dte_days.where(option_rows["as_of_date"] <= option_rows["expiry"], np.nan)
                df.loc[option_mask, "dte_days"] = dte_days
            df["r"] = rf_rate

        df = self._filter_option_universe(df)
        option_mask = self._option_mask(df)
        df["iv"] = np.nan
        if option_mask.any():
            df.loc[option_mask, "iv_source_used"] = iv_source

        if iv_source == "provided" and "iv_provided" in df.columns and option_mask.any():
            pricing_cfg = self.cfg.get("pricing") or {}
            # Exchange settlement IV is authoritative (issue 025): by default we do NOT
            # re-derive IV by inverting the settlement price. Price-inversion is only a
            # reliable cross-check near the money and merely reproduces the exchange IV
            # there, while corrupting the wings/deep-ITM. Opt in with
            # validate_provided_iv: true only when an explicit model self-test is wanted.
            validate_iv = bool(self.cfg.get(
                "validate_provided_iv",
                pricing_cfg.get("validate_provided_iv", False),
            ))
            if validate_iv:
                if "iv_flag" not in df.columns:
                    df["iv_flag"] = False
                if "iv_invertible" not in df.columns:
                    df["iv_invertible"] = False
                for col in ("iv_solved", "iv_diff"):
                    if col not in df.columns:
                        df[col] = np.nan
                # Large historical chains can sample this self-test while still using
                # exchange-provided IV for all rows.
                check_df = df.loc[option_mask]
                sample_size = self.cfg.get(
                    "iv_validate_sample_size",
                    pricing_cfg.get("iv_validate_sample_size"),
                )
                if sample_size is not None and len(check_df) > int(sample_size):
                    check_df = check_df.sample(
                        int(sample_size),
                        random_state=int(self.cfg.get("iv_validate_random_state", 0)),
                    )
                checked = _pricing.validate_provided_iv(check_df, self.cfg)
                df.loc[checked.index, "iv_solved"] = checked["iv_solved"]
                df.loc[checked.index, "iv_diff"] = checked["iv_diff"]
                df.loc[checked.index, "iv_flag"] = checked["iv_flag"]
                df.loc[checked.index, "iv_invertible"] = checked["iv_invertible"]
                df.loc[option_mask, "iv_validation"] = "checked"
            else:
                df.loc[option_mask, "iv_validation"] = "trusted_exchange"
            df.loc[option_mask, "iv"] = df.loc[option_mask, "iv_provided"].copy()

        elif iv_source == "solve" and option_mask.any():
            # Solve IV for each row
            ivs = pd.Series(np.nan, index=df.index, dtype=float)
            option_rows = df.loc[option_mask]
            show_progress = should_show_progress(self.cfg, total=len(option_rows))
            for idx, row in progress_iter(
                option_rows.iterrows(),
                "IV solve",
                total=len(option_rows),
                enabled=show_progress,
            ):
                if pd.isna(row.get("T")) or row.get("T", 0) <= 0:
                    continue
                iv = _pricing.solve_iv(
                    model=model,
                    mkt_price=self._row_option_price(row),
                    S_or_F=self._row_underlying_value(row),
                    K=row.get("strike", np.nan),
                    T=row.get("T", np.nan),
                    r=row.get("r", rf_rate),
                    right=row.get("right", "C"),
                    q=div_yield,
                    bounds=solver_bounds,
                )
                ivs.loc[idx] = iv
            df["iv"] = ivs

        return self._filter_max_iv(df, "iv")

    def compute_greeks(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute Greeks for all option rows.

        Uses vectorized batch_greeks() from core/greeks.py (no iterrows).
        Respects cfg['pricing_model'] — black76 for futures, bsm for equity.
        Config keys (pricing.* prefix or root): greeks_backend, greeks_batch_size, greeks_dtype.
        """
        df = df.copy()
        model = self.cfg.get("pricing_model", "black76")
        rf_rate = self.cfg.get("rf_rate", 0.05)
        div_yield = self.cfg.get("div_yield", 0.0)

        greeks_cols = ["delta", "gamma", "vega", "theta", "rho"]
        for col in greeks_cols:
            df[col] = np.nan

        pricing_cfg = self.cfg.get("pricing") or {}
        if not bool(self.cfg.get("compute_greeks", pricing_cfg.get("compute_greeks", True))):
            self._option_quality["greeks_runtime"] = {
                "status": "disabled",
                "requested_backend": None,
                "resolved_backend": None,
                "rows": 0,
            }
            return df

        backend = self.cfg.get("greeks_backend", pricing_cfg.get("greeks_backend", "numpy"))
        batch_size = self.cfg.get("greeks_batch_size", pricing_cfg.get("greeks_batch_size", None))
        dtype = self.cfg.get("greeks_dtype", pricing_cfg.get("greeks_dtype", "float64"))
        cuda_min_rows = self.cfg.get("greeks_cuda_min_rows", pricing_cfg.get("greeks_cuda_min_rows", None))
        if batch_size is not None:
            batch_size = int(batch_size)
        if cuda_min_rows is not None:
            cuda_min_rows = int(cuda_min_rows)

        option_mask = self._option_mask(df)
        option_rows = df.loc[option_mask]

        if option_rows.empty:
            self._option_quality["greeks_runtime"] = {
                "status": "skipped",
                "reason": "no_option_rows",
                "requested_backend": str(backend),
                "resolved_backend": None,
                "rows": 0,
                "dtype": str(dtype),
                "batch_size": batch_size,
                "cuda_min_rows": cuda_min_rows,
            }
            if not self._has_usable_option_values(df, "delta_provided"):
                df = self._filter_delta_band(df, "delta")
            return df

        valid_T = (
            option_rows["T"].notna() & (option_rows["T"] > 0)
            if "T" in option_rows.columns
            else pd.Series(False, index=option_rows.index)
        )
        valid_iv = (
            option_rows["iv"].notna()
            if "iv" in option_rows.columns
            else pd.Series(False, index=option_rows.index)
        )
        valid_mask = valid_T & valid_iv
        valid_rows = option_rows.loc[valid_mask]
        resolved_backend = None
        if not valid_rows.empty:
            resolved_backend = _greeks._resolve_greeks_backend(
                str(backend),
                len(valid_rows),
                cuda_min_rows=cuda_min_rows,
            )
        self._option_quality["greeks_runtime"] = {
            "status": "computed" if not valid_rows.empty else "skipped",
            "reason": None if not valid_rows.empty else "no_valid_t_iv_rows",
            "requested_backend": str(backend),
            "resolved_backend": resolved_backend,
            "rows": int(len(valid_rows)),
            "option_rows": int(len(option_rows)),
            "dtype": str(dtype),
            "batch_size": batch_size,
            "cuda_min_rows": cuda_min_rows,
        }

        if not valid_rows.empty:
            # Vectorized underlying precedence: underlying_price → S → F → price_std
            S_or_F = pd.Series(np.nan, index=valid_rows.index, dtype=float)
            for col in ("underlying_price", "S", "F", "price_std"):
                if col in valid_rows.columns:
                    fill_mask = S_or_F.isna() & valid_rows[col].notna()
                    S_or_F[fill_mask] = pd.to_numeric(valid_rows.loc[fill_mask, col], errors="coerce")

            K_arr = pd.to_numeric(valid_rows.get("strike", pd.Series(np.nan, index=valid_rows.index)), errors="coerce")
            T_arr = valid_rows["T"]
            r_arr = valid_rows["r"] if "r" in valid_rows.columns else pd.Series(rf_rate, index=valid_rows.index)
            r_arr = r_arr.fillna(rf_rate)
            sigma_arr = valid_rows["iv"]
            right_arr = valid_rows["right"] if "right" in valid_rows.columns else pd.Series("C", index=valid_rows.index)

            greeks_result = _greeks.batch_greeks(
                model=model,
                S_or_F=S_or_F.values,
                K=K_arr.values,
                T=T_arr.values,
                r=r_arr.values,
                sigma=sigma_arr.values,
                right=right_arr.values,
                q=div_yield,
                backend=backend,
                batch_size=batch_size,
                dtype=str(dtype),
                cuda_min_rows=cuda_min_rows,
            )

            for col in greeks_cols:
                df.loc[valid_rows.index, col] = greeks_result[col]

        if not self._has_usable_option_values(df, "delta_provided"):
            df = self._filter_delta_band(df, "delta")

        return df

    def compute_vrp_sign(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute Variance Risk Premium sign.

        VRP = ATM IV − Realized Vol.
        Positive = options expensive relative to realized.
        """
        df = df.copy()

        if "iv" not in df.columns:
            df["vrp"] = 0.0
            df["vrp_sign"] = "neutral"
            return df

        # ATM proxy: use rows where |delta| ≈ 0.5 (or strike closest to forward)
        # Simplified: compare IV to rolling realized vol
        if "vol_std" in df.columns:
            df["vrp"] = df["iv"] - df["vol_std"]
        else:
            df["vrp"] = 0.0

        df["vrp_sign"] = np.select(
            [df["vrp"] > 0.01, df["vrp"] < -0.01],
            ["vrp_positive", "vrp_negative"],
            default="vrp_neutral",
        )
        return df

    def check_pcp(self, df: pd.DataFrame, tol: float = 0.05) -> pd.DataFrame:
        """Put-Call Parity check.

        Pairing is scoped to a single decision date and underlying identity.
        """
        df = df.copy()
        pricing_cfg = self.cfg.get("pricing") or {}
        if not bool(self.cfg.get("check_pcp", pricing_cfg.get("check_pcp", True))):
            df["_pcp_flag"] = False
            df["pcp_pair_missing"] = False
            df["pcp_duplicate_pair"] = False
            return df
        if "right" not in df.columns:
            return df

        df["_pcp_flag"] = False
        df["pcp_pair_missing"] = False
        df["pcp_duplicate_pair"] = False

        option_mask = self._option_mask(df)
        option_df = df.loc[option_mask].copy()
        if option_df.empty:
            return df

        key_candidates = [
            "as_of_date",
            "product_id",
            "contract_root",
            "hub",
            "delivery_month",
            "expiry",
            "strike",
        ]
        key_cols = [col for col in key_candidates if col in option_df.columns]
        if not {"as_of_date", "expiry", "strike"}.issubset(set(key_cols)):
            return df

        model = self.cfg.get("pricing_model", "black76")
        div_yield = self.cfg.get("div_yield", 0.0)

        for _, grp in option_df.groupby(key_cols, dropna=False):
            calls = grp[grp["right"] == "C"]
            puts = grp[grp["right"] == "P"]

            if calls.empty or puts.empty:
                df.loc[grp.index, "pcp_pair_missing"] = True
                continue

            if len(calls) != 1 or len(puts) != 1:
                df.loc[grp.index, "pcp_duplicate_pair"] = True
                continue

            c_idx = calls.index[0]
            p_idx = puts.index[0]
            c_row = df.loc[c_idx]
            p_row = df.loc[p_idx]

            if c_row.get("T", 0) <= 0 or pd.isna(c_row.get("T")):
                continue

            r = c_row.get("r", 0.05)
            t = c_row["T"]
            k = c_row["strike"]
            disc_r = np.exp(-r * t)

            if model == "black76":
                expected_diff = disc_r * (self._row_underlying_value(c_row) - k)
            elif model in ("bs", "bsm"):
                s = self._row_underlying_value(c_row)
                expected_diff = s * np.exp(-div_yield * t) - k * disc_r
            else:
                raise ValueError(f"Unknown pricing model: {model}")

            actual_diff = self._row_option_price(c_row) - self._row_option_price(p_row)
            if abs(actual_diff - expected_diff) > tol:
                df.loc[[c_idx, p_idx], "_pcp_flag"] = True

        return df

    def compute_skew(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute 25-delta skew (put IV − call IV at 25-delta)."""
        df = df.copy()
        if "delta" not in df.columns or "iv" not in df.columns:
            df["skew_25d"] = 0.0
            return df

        # Per date, find 25-delta put call IV difference
        # Placeholder — full implementation needs IV surface interpolation
        df["skew_25d"] = 0.0

        return df
