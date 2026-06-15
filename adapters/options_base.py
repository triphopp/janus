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

    def compute_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute standardized returns."""
        df = df.copy()
        df = df.sort_values(["as_of_date"])
        df["return_std"] = df.groupby("product_id", group_keys=False)["price_std"].pct_change()
        vol_window = self.cfg.get("vol_window", 21)
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

        if "expiry" in df.columns and "as_of_date" in df.columns:
            df["T"] = _dte.compute_dte_series(df, dte_cfg)
            df["r"] = rf_rate

        if iv_source == "provided" and "iv_provided" in df.columns:
            # Validate provided IV against self-solved
            df = _pricing.validate_provided_iv(df, self.cfg)
            df["iv"] = df["iv_provided"].copy()

        elif iv_source == "solve":
            # Solve IV for each row
            ivs = []
            for _, row in df.iterrows():
                if pd.isna(row.get("T")) or row.get("T", 0) <= 0:
                    ivs.append(np.nan)
                    continue
                iv = _pricing.solve_iv(
                    model=model,
                    mkt_price=row.get("price", np.nan),
                    S_or_F=row.get("F", row.get("price_std", np.nan)),
                    K=row.get("strike", np.nan),
                    T=row.get("T", np.nan),
                    r=row.get("r", rf_rate),
                    right=row.get("right", "C"),
                )
                ivs.append(iv)
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

        greeks_list = []
        for _, row in df.iterrows():
            if pd.isna(row.get("T")) or row.get("T", 0) <= 0 or pd.isna(row.get("iv")):
                greeks_list.append({"delta": np.nan, "gamma": np.nan, "vega": np.nan,
                                    "theta": np.nan, "rho": np.nan})
                continue

            g = _greeks.single_leg_greeks(
                model=model,
                S_or_F=row.get("F", row.get("price_std", np.nan)),
                K=row.get("strike", np.nan),
                T=row.get("T", np.nan),
                r=row.get("r", rf_rate),
                sigma=row.get("iv", 0.2),
                right=row.get("right", "C"),
            )
            greeks_list.append(g)

        greeks_df = pd.DataFrame(greeks_list, index=df.index)
        for col in greeks_df.columns:
            df[col] = greeks_df[col]

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

        C − P = e^(-rT)(F − K) ± tol
        Flags violations.
        """
        df = df.copy()
        if "right" not in df.columns:
            return df

        df["_pcp_flag"] = False

        # Identify put-call pairs: same expiry, same strike, same underlying
        # Simplified check: for each call, find matching put
        calls = df[df["right"] == "C"]
        puts = df[df["right"] == "P"]

        if calls.empty or puts.empty:
            return df

        for (expiry, strike), c_grp in calls.groupby(["expiry", "strike"]):
            p_match = puts[(puts["expiry"] == expiry) & (puts["strike"] == strike)]
            if p_match.empty:
                continue
            for c_idx, c_row in c_grp.iterrows():
                for p_idx, p_row in p_match.iterrows():
                    if c_row.get("T", 0) <= 0 or pd.isna(c_row.get("T")):
                        continue
                    disc = np.exp(-c_row.get("r", 0.05) * c_row["T"])
                    expected_diff = disc * (c_row.get("F", c_row.get("price_std", 0)) - strike)
                    actual_diff = c_row["price"] - p_row["price"]
                    if abs(actual_diff - expected_diff) > tol:
                        df.loc[c_idx, "_pcp_flag"] = True
                        df.loc[p_idx, "_pcp_flag"] = True

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
