"""Transaction cost and liquidity model.

v1.4: three levels, one API.
- Level 1: fixed commission + fixed half-spread per leg
- Level 2: bid-ask scaling by DTE, moneyness, and volatility regime
- Level 3: simple market-impact add-on for large participation rates
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd


@dataclass
class CostBreakdown:
    """Cost result in dollars, always positive."""

    total: float
    legs: list[dict]
    level: int

    def as_dict(self) -> dict:
        return {"total": self.total, "legs": self.legs, "level": self.level}


def _txcfg(cfg: Optional[dict]) -> dict:
    cfg = cfg or {}
    nested = cfg.get("txcost", {})
    merged = {k: v for k, v in cfg.items() if k != "txcost"}
    merged.update(nested)
    return merged


def _get(obj: Any, name: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _qty(leg) -> float:
    return float(_get(leg, "qty", _get(leg, "quantity", 1.0)) or 0.0)


def _dte_days(leg) -> float:
    if _get(leg, "dte") is not None:
        return float(_get(leg, "dte"))
    if _get(leg, "DTE") is not None:
        return float(_get(leg, "DTE"))
    if _get(leg, "T") is not None:
        return float(_get(leg, "T")) * 365.0
    if _get(leg, "T_at_t") is not None:
        return float(_get(leg, "T_at_t")) * 365.0
    return 30.0


def _dte_spread_factor(dte_days: float, curve: dict) -> float:
    if not curve:
        return 1.0
    if dte_days < 14 and "<14" in curve:
        return float(curve["<14"])
    if 14 <= dte_days < 30 and "14-30" in curve:
        return float(curve["14-30"])
    if 30 <= dte_days < 60 and "30-60" in curve:
        return float(curve["30-60"])
    if dte_days >= 60 and ">60" in curve:
        return float(curve[">60"])
    return 1.0


def _moneyness_factor(leg, cfg: dict) -> float:
    strike = _get(leg, "strike", _get(leg, "K"))
    under = _get(leg, "F", _get(leg, "F_at_t", _get(leg, "S", _get(leg, "S_at_t"))))
    if not strike or not under:
        return 1.0
    threshold = float(cfg.get("moneyness_otm_threshold", 0.15))
    distance = abs(float(strike) / float(under) - 1.0)
    if distance <= threshold:
        return 1.0
    slope = float(cfg.get("moneyness_slope", 4.0))
    return min(float(cfg.get("moneyness_max_mult", 4.0)), 1.0 + slope * (distance - threshold))


def _fixed_cost(leg, cfg: dict) -> float:
    commission = float(cfg.get("commission_per_contract", 0.0))
    half_spread = float(cfg.get("half_spread_fixed", 0.0))
    return commission + half_spread


def _scaled_cost(leg, mkt, cfg: dict) -> float:
    base = float(cfg.get("commission_per_contract", 0.0))
    bid_ask_mid = _get(leg, "bid_ask_mid", _get(mkt, "bid_ask_mid", None))
    if bid_ask_mid is None:
        bid_ask_mid = 2.0 * float(cfg.get("half_spread_fixed", 0.0))

    dte_factor = _dte_spread_factor(_dte_days(leg), cfg.get("dte_spread_curve", {}))
    mono_factor = _moneyness_factor(leg, cfg)
    vol_regime = _get(leg, "vol_regime", _get(mkt, "vol_regime", "mid_vol"))
    regime_mult = cfg.get("spread_regime_mult", {}).get(vol_regime, 1.0)
    return base + (float(bid_ask_mid) * dte_factor * mono_factor * float(regime_mult) / 2.0)


def _impact_cost(leg, mkt, cfg: dict) -> float:
    scaled = _scaled_cost(leg, mkt, cfg)
    participation = float(_get(leg, "participation_rate", _get(mkt, "participation_rate", 0.0)) or 0.0)
    price = float(_get(leg, "price", _get(mkt, "price", 1.0)) or 1.0)
    impact_coeff = float(cfg.get("impact_coeff", 0.1))
    impact = impact_coeff * price * np.sqrt(max(participation, 0.0))
    return scaled + impact


def cost_per_trade(legs: list, mkt_data=None, cfg: Optional[dict] = None) -> CostBreakdown:
    """Compute positive cost for a trade with one or more legs."""
    txcfg = _txcfg(cfg)
    level = int(txcfg.get("level", txcfg.get("txcost_level", 1)))
    total_cost = 0.0
    breakdown = []

    for i, leg in enumerate(legs):
        if level == 1:
            per_contract = _fixed_cost(leg, txcfg)
        elif level == 2:
            per_contract = _scaled_cost(leg, mkt_data, txcfg)
        else:
            per_contract = _impact_cost(leg, mkt_data, txcfg)

        qty = abs(_qty(leg))
        cost = qty * per_contract
        total_cost += cost
        breakdown.append({
            "leg_index": i,
            "qty": qty,
            "per_contract": float(per_contract),
            "cost": float(cost),
            "level": level,
        })

    return CostBreakdown(total=float(total_cost), legs=breakdown, level=level)


def total(trades_df: pd.DataFrame, cfg: Optional[dict] = None) -> float:
    """Aggregate transaction cost from a trades DataFrame.

    Preferred: a precomputed tx_cost column. Fallback: sum common cost columns.
    Last resort: estimate fixed per-contract cost from qty.
    """
    if trades_df is None or trades_df.empty:
        return 0.0
    if "tx_cost" in trades_df.columns:
        return float(trades_df["tx_cost"].fillna(0.0).sum())

    cost_cols = [c for c in ["commission", "commission_cost", "bid_ask_cost", "slippage_cost"] if c in trades_df]
    if cost_cols:
        return float(trades_df[cost_cols].fillna(0.0).sum(axis=1).sum())

    txcfg = _txcfg(cfg)
    qty_col = "qty" if "qty" in trades_df.columns else "quantity" if "quantity" in trades_df.columns else None
    if qty_col is None:
        return 0.0
    per_contract = _fixed_cost({}, txcfg)
    return float(trades_df[qty_col].abs().fillna(0.0).sum() * per_contract)


def financing_cost(trades_df: pd.DataFrame, cfg: Optional[dict] = None) -> float:
    """Positive financing cost from notional, days held, and funding rate."""
    if trades_df is None or trades_df.empty:
        return 0.0
    if "financing_cost" in trades_df.columns:
        return float(trades_df["financing_cost"].fillna(0.0).sum())

    txcfg = _txcfg(cfg)
    if "notional" not in trades_df.columns:
        return 0.0
    days = trades_df["days_held"] if "days_held" in trades_df.columns else 1.0
    if "rf_rate" in trades_df.columns:
        rate = trades_df["rf_rate"]
    else:
        rate = float(txcfg.get("financing_rate", txcfg.get("rf_rate", 0.0)))
    margin = float(txcfg.get("margin_requirement", 1.0))
    return float((trades_df["notional"].abs() * margin * rate * days / 365.0).fillna(0.0).sum())
