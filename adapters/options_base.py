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


class OptionsBase(AdapterBase):
    """Base class for options adapters — shared logic for IV, Greeks, PCP, VRP.

    Subclasses must override prepare() but can call inherited methods.
    """

    OPTION_REQUIRED_COLUMNS = ("as_of_date", "expiry", "right", "strike", "price")

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
            right = df["right"].astype("string").str.upper()
            inferred = right.isin(["C", "P"]).fillna(False) & df["strike"].notna()

        if "instrument_type" in df.columns:
            typed = df["instrument_type"].astype("string").str.lower().eq("option").fillna(False)
            return typed | inferred

        return inferred

    def _future_mask(self, df: pd.DataFrame) -> pd.Series:
        """Return True for rows that are futures/underlying rows."""
        if "instrument_type" in df.columns:
            typed = df["instrument_type"].astype("string").str.lower().eq("future").fillna(False)
            return typed & ~self._option_mask(df)
        return ~self._option_mask(df)

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
            underlying = (
                df.dropna(subset=["underlying_price"])
                .sort_values(["as_of_date"])
                .groupby("as_of_date")["underlying_price"]
                .first()
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

        if "expiry" in df.columns and "as_of_date" in df.columns:
            df["T"] = _dte.compute_dte_series(df, dte_cfg)
            df["dte_days"] = (df["expiry"] - df["as_of_date"]).dt.days
            df.loc[df["as_of_date"] > df["expiry"], "dte_days"] = np.nan
            df["r"] = rf_rate

        option_mask = self._option_mask(df)
        df["iv"] = np.nan
        if option_mask.any():
            df.loc[option_mask, "iv_source_used"] = iv_source

        if iv_source == "provided" and "iv_provided" in df.columns and option_mask.any():
            # Validate provided IV against self-solved
            checked = _pricing.validate_provided_iv(df.loc[option_mask], self.cfg)
            for col in ("iv_solved", "iv_diff"):
                if col not in df.columns:
                    df[col] = np.nan
                df.loc[checked.index, col] = checked[col]
            if "iv_flag" not in df.columns:
                df["iv_flag"] = False
            df.loc[checked.index, "iv_flag"] = checked["iv_flag"]
            df.loc[option_mask, "iv"] = checked["iv_provided"].copy()

        elif iv_source == "solve" and option_mask.any():
            # Solve IV for each row
            ivs = pd.Series(np.nan, index=df.index, dtype=float)
            for idx, row in df.loc[option_mask].iterrows():
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

        return df

    def compute_greeks(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute Greeks for all option rows.

        Uses closed-form from core/greeks.py.
        Respects cfg['pricing_model'] — black76 for futures, bsm for equity.
        """
        df = df.copy()
        model = self.cfg.get("pricing_model", "black76")
        rf_rate = self.cfg.get("rf_rate", 0.05)
        div_yield = self.cfg.get("div_yield", 0.0)

        greeks_cols = ["delta", "gamma", "vega", "theta", "rho"]
        for col in greeks_cols:
            df[col] = np.nan

        option_mask = self._option_mask(df)
        for idx, row in df.loc[option_mask].iterrows():
            if pd.isna(row.get("T")) or row.get("T", 0) <= 0 or pd.isna(row.get("iv")):
                continue

            g = _greeks.single_leg_greeks(
                model=model,
                S_or_F=self._row_underlying_value(row),
                K=row.get("strike", np.nan),
                T=row.get("T", np.nan),
                r=row.get("r", rf_rate),
                sigma=row.get("iv", 0.2),
                right=row.get("right", "C"),
                q=div_yield,
            )
            for col in greeks_cols:
                df.loc[idx, col] = g[col]

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

        df["vrp_sign"] = df["vrp"].apply(
            lambda x: "vrp_positive" if x > 0.01 else ("vrp_negative" if x < -0.01 else "vrp_neutral")
        )
        return df

    def check_pcp(self, df: pd.DataFrame, tol: float = 0.05) -> pd.DataFrame:
        """Put-Call Parity check.

        Pairing is scoped to a single decision date and underlying identity.
        """
        df = df.copy()
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
