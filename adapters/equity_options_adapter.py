"""Equity options adapter — BS-Merton, strike-adjust, NYSE close.

Overrides OptionsBase for equity-specific concerns:
- Pricing model: bsm (Black-Scholes-Merton with dividend yield)
- Strike adjustment for corporate actions
- NYSE trading hours (4pm close)
"""

from typing import Tuple

import numpy as np
import pandas as pd

from .options_base import OptionsBase


class EquityOptionsAdapter(OptionsBase):
    """Prepare equity options data for core pipeline.

    Inherits ~65% from OptionsBase. Overrides only equity-specific parts.
    """

    def prepare(self, raw_df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
        """Equity options prepare pipeline."""
        df = raw_df.copy()

        # ── Strike adjustment (corp actions) ──
        if "strike" in df.columns and "adj_factor" in df.columns:
            df["strike"] = df["strike"] / df["adj_factor"]

        # ── Build underlying price ──
        if "raw_close" in df.columns and "adj_factor" in df.columns:
            df["price_std"] = df["raw_close"] * df["adj_factor"]
        elif "price" in df.columns:
            df["price_std"] = df["price"]

        # ── Forward price (spot adjusted for dividends) ──
        div_yield = self.cfg.get("div_yield", 0.0)
        df["F"] = df["price_std"]  # For equity options, F ≈ S (with div adjustment in model)

        # ── Returns + vol ──
        df = self.compute_returns(df)

        # ── DTE (via core/dte.py — single source of truth) ──
        dte_cfg = self.cfg.get("dte", {"basis": "trading", "day_count": "bus_252",
                                        "exclude_expiry_date": False})

        # ── IV surface ──
        df = self.build_iv_surface(df)

        # ── Greeks (closed-form, BS-Merton) ──
        df = self.compute_greeks(df)

        # ── VRP  ──
        df = self.compute_vrp_sign(df)

        # ── Skew ──
        df = self.compute_skew(df)

        # ── PCP check ──
        df = self.check_pcp(df)

        # ── Build cfg ──
        cfg = {
            **self.cfg,
            "pricing_model": self.cfg.get("pricing_model", "bsm"),
            "price_col": "price_std",
            "vol_col": "vol_std",
            "return_col": "return_std",
            "vol_window": self.cfg.get("vol_window", 21),
            "trend_window": self.cfg.get("trend_window", 126),
            "purge_bars": self.cfg.get("purge_bars", 5),
            "regime_axes": self.cfg.get("regime_axes", [
                "vol_regime", "vrp_sign", "skew_direction"
            ]),
        }

        return df, cfg
