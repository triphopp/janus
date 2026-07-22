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
        df = self._normalize_option_columns(raw_df)
        df = self._validate_adapter_identity(
            df,
            adapter_family="equity_options",
            option_underlying_type="spot",
        )
        option_mask = self._require_option_chain_schema(df, "equity options")
        df = self._apply_pricing_model_policy(df, default_model="bsm")
        option_mask = self._option_mask(df)

        # ── Build PIT-safe strike / underlying price ──
        # Yahoo-style adjustment factors are retroactive unless the provider marks them
        # PIT. Preserve provider-adjusted values for audit, but do not feed them into
        # pricing by default.
        if "raw_close" in df.columns and "adj_factor" in df.columns:
            adj_is_pit = df.get("adj_factor_is_pit", False)
            if not isinstance(adj_is_pit, pd.Series):
                adj_is_pit = pd.Series(bool(adj_is_pit), index=df.index)
            if not pd.api.types.is_bool_dtype(adj_is_pit):
                adj_is_pit = adj_is_pit.map(
                    lambda v: str(v).strip().lower() in {"1", "true", "t", "yes", "y"}
                )
            adj_is_pit = adj_is_pit.fillna(False).astype(bool)
            use_retro = bool(self.cfg.get("allow_retro_adjusted_prices", False))
            use_adjustment = adj_is_pit | use_retro
            factor_changed = df["adj_factor"].fillna(1.0) != 1.0

            df["adjusted_price_provider"] = df["raw_close"] * df["adj_factor"]
            df["underlying_price"] = np.where(
                use_adjustment,
                df["adjusted_price_provider"],
                df["raw_close"],
            )
            df["price_adjustment_warning"] = (~use_adjustment) & factor_changed

            if "strike" in df.columns:
                df["strike_raw"] = df["strike"]
                df["strike_adjusted_provider"] = df["strike"] * df["adj_factor"]
                df["strike"] = np.where(
                    use_adjustment,
                    df["strike_adjusted_provider"],
                    df["strike"],
                )
                df["strike_adjustment_warning"] = (~use_adjustment) & factor_changed
        elif "underlying_price" in df.columns:
            df["underlying_price"] = df["underlying_price"]
            df["price_adjustment_warning"] = False
        elif "raw_close" in df.columns:
            df["underlying_price"] = df["raw_close"]
            df["price_adjustment_warning"] = False
        elif "S" in df.columns:
            df["underlying_price"] = df["S"]
            df["price_adjustment_warning"] = False
        else:
            raise ValueError("equity options require an underlying price column such as raw_close or S")

        df["option_price"] = np.nan
        df.loc[option_mask, "option_price"] = df.loc[option_mask, "price"]

        # ── Forward price (spot adjusted for dividends) ──
        df["S"] = df["underlying_price"]
        df["F"] = df["underlying_price"]  # Compatibility; BSM uses S and q.
        df["price_std"] = df["underlying_price"]

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

        # Full contract key for validators — prevents duplicate-identity false-positives
        # on option chains where many strikes share (symbol, as_of_date).
        identity_cols = [
            col for col in ("as_of_date", "product_id", "symbol", "expiry", "right", "strike")
            if col in df.columns
        ]
        outlier_identity_cols = [
            col for col in ("product_id", "symbol")
            if col in df.columns
        ]

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
            "n_folds": self.cfg.get("n_folds", 8),
            "event_embargo_bars": self.cfg.get("event_embargo_bars", 2),
            "label_end_col": self.cfg.get("label_end_col", "expiry"),
            "dte": dte_cfg,
            "metrics_mode": self.cfg.get("metrics_mode", "strategy_required"),
            # skew_direction omitted: compute_skew returns placeholder 0.0 — dead axis
            "regime_axes": self.cfg.get("regime_axes", ["vol_regime", "vrp_sign"]),
            "identity_cols": identity_cols,
            "outlier_identity_cols": outlier_identity_cols,
            "option_quality": self._option_quality,
            "_config_warnings": self._config_warnings,
        }

        df = self._flag_option_quality(df)
        return df, cfg
