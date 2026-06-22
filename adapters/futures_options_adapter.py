"""Futures options adapter — Black-76, roll, term structure.

Overrides OptionsBase for futures-specific concerns:
- Pricing model: black76 (futures = underlying, no carry cost)
- Roll convention affects underlying price
- Term structure → regime axis
- Scheduled events from config files
"""

from typing import Tuple

import numpy as np
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
        df = self._normalize_option_columns(raw_df)
        self._require_option_chain_schema(df, "futures options")

        # ── Build continuous futures (shared with FuturesAdapter) ──
        fut = FuturesAdapter(self.cfg)
        df = fut.build_continuous_futures(df)

        # ── Flag scheduled events (generic — reads cfg['event_calendars']) ──
        df = fut.flag_scheduled_events(df)

        # ── Term structure ──
        df = fut.compute_term_structure(df)

        # ── Underlying futures map ──
        df = self._attach_underlying_futures(df)

        # ── Returns + vol ──
        df = self.compute_returns(df)

        # ── DTE (via core/dte.py) ──
        dte_cfg = self.cfg.get("dte", {"basis": "calendar", "day_count": "act_365",
                                        "exclude_expiry_date": False})

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
            # skew_direction omitted: compute_skew returns placeholder 0.0 — dead axis
        ] + self.cfg.get("event_regimes", [])

        # Resolve max_dte: actual max DTE in the dataset (for purge window)
        max_dte = 90
        if "dte" in df.columns:
            max_dte = int(df["dte"].dropna().max()) if not df["dte"].dropna().empty else 90
        elif "dte_days" in df.columns:
            max_dte = int(df["dte_days"].dropna().max()) if not df["dte_days"].dropna().empty else 90
        elif "T" in df.columns:
            max_dte = int((df["T"].dropna().max() * 365)) if not df["T"].dropna().empty else 90

        # Full contract key for validators — prevents duplicate-identity false-positives
        # on option chains where many strikes share (product_id, as_of_date).
        identity_cols = [
            col for col in (
                "as_of_date", "product_id", "contract_root", "hub",
                "delivery_month", "expiry", "right", "strike",
            )
            if col in df.columns
        ]
        outlier_identity_cols = [
            col for col in ("product_id", "contract_root", "hub", "delivery_month", "expiry")
            if col in df.columns
        ]

        cfg = {
            **self.cfg,
            "pricing_model": self.cfg.get("pricing_model", "black76"),
            "price_col": "price_std",
            "vol_col": "vol_std",
            "return_col": "return_std",
            "vol_window": self.cfg.get("vol_window", 21),
            "trend_window": self.cfg.get("trend_window", 126),
            "n_folds": self.cfg.get("n_folds", 8),
            "purge_bars": self.cfg.get("purge_bars", "max_dte"),
            "event_embargo_bars": self.cfg.get("event_embargo_bars", 2),
            "label_end_col": self.cfg.get("label_end_col", "expiry"),
            "_max_dte": max_dte,
            "regime_axes": regime_axes,
            "event_flags": self.cfg.get("event_calendars", []),
            "max_concentration": self.cfg.get("max_concentration", 0.80),
            "kl_threshold": self.cfg.get("kl_threshold", 0.5),
            "js_threshold": self.cfg.get("js_threshold", 0.3),
            "rf_rate_col": self.cfg.get("rf_rate_col", "sofr"),
            "dte": dte_cfg,
            "metrics_mode": self.cfg.get("metrics_mode", "strategy_required"),
            "identity_cols": identity_cols,
            "outlier_identity_cols": outlier_identity_cols,
            "option_quality": self._option_quality,
            "_config_warnings": self._config_warnings,
        }

        df = self._flag_option_quality(df)
        return df, cfg

    def _attach_underlying_futures(self, df: pd.DataFrame) -> pd.DataFrame:
        """Attach PIT futures prices to each option row."""
        df = df.copy()
        option_mask = self._option_mask(df)
        future_mask = self._future_mask(df)

        if not future_mask.any():
            raise ValueError("futures options require underlying future rows for F mapping")

        df["option_price"] = np.nan
        df.loc[option_mask, "option_price"] = df.loc[option_mask, "price"]

        df["underlying_price"] = np.nan
        df.loc[future_mask, "underlying_price"] = df.loc[future_mask, "price_std"]

        identity_cols = [
            col for col in ("product_id", "contract_root", "hub")
            if col in df.columns
        ]
        join_keys = ["as_of_date", *identity_cols]
        if "delivery_month" in df.columns and df.loc[option_mask, "delivery_month"].notna().any():
            join_keys.append("delivery_month")

        futures_cols = [*join_keys, "price_std"]
        if "expiry" in df.columns:
            futures_cols.append("expiry")
        sort_cols = [col for col in [*join_keys, "expiry"] if col in df.columns]
        futures = (
            df.loc[future_mask & df["price_std"].notna(), futures_cols]
            .sort_values(sort_cols)
            .groupby(join_keys, dropna=False)["price_std"]
            .first()
            .rename("_underlying_price")
        )

        df = df.join(futures, on=join_keys)
        df.loc[option_mask, "underlying_price"] = df.loc[option_mask, "_underlying_price"]

        missing = option_mask & df["underlying_price"].isna()
        if missing.any():
            n_missing = int(missing.sum())
            total_options = int(option_mask.sum())
            examples = df.loc[missing, join_keys].drop_duplicates().head(3).to_dict("records")
            self._option_quality["underlying_map"] = {
                "missing_rows": n_missing,
                "drop_rate": n_missing / total_options if total_options > 0 else 0.0,
                "examples": examples,
            }
            self._count_option_drop("missing_underlying_future", missing, option_mask)
            self._option_quality["universe_drop_rows"] += n_missing
            if bool(self.cfg.get("strict_underlying_map", False)) or bool(missing.loc[option_mask].all()):
                raise ValueError(
                    "Unable to map options to underlying future rows for "
                    f"{n_missing} option rows; examples: {examples}"
                )
            df.loc[missing, "_underlying_map_flag"] = True
            df.loc[missing, "_underlying_map_reason"] = "missing_underlying_future"
            print(
                "  Futures options: dropped "
                f"{n_missing} option rows without underlying future map; "
                f"examples: {examples}"
            )
            df = df.loc[~missing].copy()

        df["F"] = df["underlying_price"]
        df["price_std"] = df["underlying_price"]
        return df.drop(columns=["_underlying_price"])
