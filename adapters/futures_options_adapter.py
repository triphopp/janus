"""Futures options adapter — Black-76, roll, term structure.

Overrides OptionsBase for futures-specific concerns:
- Pricing model: black76 (futures = underlying, no carry cost)
- Roll convention affects underlying price
- Term structure → regime axis
- Scheduled events from config files
"""

from typing import Tuple

import pandas as pd

from .options_base import OptionsBase
from .futures_adapter import FuturesAdapter


class FuturesOptionsAdapter(OptionsBase):
    """Prepare futures options data for core pipeline.

    Inherits ~65% from OptionsBase.
    Overrides only futures-specific parts.
    """

    def prepare(self, raw_df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
        """Futures options prepare pipeline."""
        df = raw_df.copy()

        # ── Build continuous futures (shared with FuturesAdapter) ──
        fut = FuturesAdapter(self.cfg)
        df = fut.build_continuous_futures(df)

        # ── Flag scheduled events (generic — reads cfg['event_calendars']) ──
        df = fut.flag_scheduled_events(df)

        # ── Term structure ──
        df = fut.compute_term_structure(df)

        # ── Returns + vol ──
        df = self.compute_returns(df)

        # ── DTE (via core/dte.py) ──
        dte_cfg = self.cfg.get("dte", {"basis": "calendar", "day_count": "act_365",
                                        "exclude_expiry_date": False})

        # ── Forward price ──
        df["F"] = df["price_std"]  # For futures options, F = futures price

        # ── IV surface (respects cfg['iv_source']) ──
        df = self.build_iv_surface(df)

        # ── Greeks (closed-form, Black-76) — inherited ──
        df = self.compute_greeks(df)

        # ── VRP — inherited ──
        df = self.compute_vrp_sign(df)

        # ── PCP check — inherited ──
        df = self.check_pcp(df)

        # ── Skew — inherited ──
        df = self.compute_skew(df)

        # ── Build cfg for core ──
        regime_axes = [
            "vol_regime",
            "term_structure",
            "vrp_sign",
            "skew_direction",
        ] + self.cfg.get("event_regimes", [])

        cfg = {
            **self.cfg,
            "pricing_model": self.cfg.get("pricing_model", "black76"),
            "price_col": "price_std",
            "vol_col": "vol_std",
            "return_col": "return_std",
            "vol_window": self.cfg.get("vol_window", 21),
            "trend_window": self.cfg.get("trend_window", 126),
            "purge_bars": self.cfg.get("cv", {}).get("purge_bars", "max_dte"),
            "regime_axes": regime_axes,
            "event_flags": self.cfg.get("event_calendars", []),
            "max_concentration": self.cfg.get("cv", {}).get("max_concentration", 0.80),
            "kl_threshold": self.cfg.get("cv", {}).get("kl_threshold", 0.5),
            "js_threshold": self.cfg.get("cv", {}).get("js_threshold", 0.3),
            "rf_rate_col": self.cfg.get("performance", {}).get("rf_rate_source", "sofr"),
            "dte": dte_cfg,
        }

        return df, cfg
