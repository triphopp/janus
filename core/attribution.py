"""Performance attribution waterfall.

v1.4: decompose gross P&L into explainable layers, residual, costs,
financing, and net P&L. Options use entry-time Greeks. Equity uses PIT factor
exposures. Futures use spot/basis/roll layers when available.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from core import txcost


@dataclass
class Layer:
    name: str
    pnl: float
    asset: str = "all"
    description: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "pnl": self.pnl,
            "asset": self.asset,
            "description": self.description,
        }


@dataclass
class WaterfallResult:
    gross: float
    layers: list[Layer]
    residual: float
    cost: float
    financing: float
    net: float

    def as_dict(self) -> dict:
        return {
            "gross": self.gross,
            "layers": [layer.as_dict() for layer in self.layers],
            "residual": self.residual,
            "cost": self.cost,
            "financing": self.financing,
            "net": self.net,
        }


def _sum_product(df: pd.DataFrame, left: str, right: str, scale: float = 1.0) -> float:
    if left not in df.columns or right not in df.columns:
        return 0.0
    return float((df[left].fillna(0.0) * df[right].fillna(0.0) * scale).sum())


def _gross_pnl(df: pd.DataFrame) -> float:
    for col in ["pnl_gross", "gross_pnl", "pnl"]:
        if col in df.columns:
            return float(df[col].fillna(0.0).sum())
    if "return" in df.columns and "notional" in df.columns:
        return float((df["return"].fillna(0.0) * df["notional"].fillna(0.0)).sum())
    return 0.0


def _greek_decompose(df: pd.DataFrame, cfg: dict) -> list[Layer]:
    """Greek P&L from entry-time Greeks only.

    Do not use exit-time or full-period average Greeks; that creates circular
    look-back attribution.
    """
    return [
        Layer(
            "delta",
            _sum_product(df, "delta_entry", "d_underlying"),
            "options",
            "entry net delta times realized underlying move",
        ),
        Layer(
            "gamma",
            _sum_product(df, "gamma_entry", "d_underlying_sq", 0.5),
            "options",
            "0.5 times entry gamma times squared underlying move",
        ),
        Layer(
            "theta",
            _sum_product(df, "theta_entry", "days_held"),
            "options",
            "entry net theta times days held",
        ),
        Layer(
            "vega_parallel",
            _sum_product(df, "vega_total_entry", "d_iv_parallel"),
            "options",
            "parallel IV move times total vega",
        ),
        Layer(
            "vega_term",
            _sum_product(df, "vega_term_risk", "d_iv_term_slope"),
            "options",
            "non-parallel term-structure IV move",
        ),
    ]


def _factor_decompose(df: pd.DataFrame, cfg: dict) -> list[Layer]:
    layers = []
    if {"market_beta", "market_return", "portfolio_value"} <= set(df.columns):
        layers.append(Layer(
            "market_beta",
            float((df["market_beta"] * df["market_return"] * df["portfolio_value"]).fillna(0.0).sum()),
            "equity",
            "market beta exposure times market return",
        ))

    factor_pairs = cfg.get("factor_pairs", [])
    if not factor_pairs:
        for col in df.columns:
            if col.endswith("_exposure"):
                base = col[:-9]
                ret_col = f"{base}_return"
                if ret_col in df.columns:
                    factor_pairs.append((col, ret_col, base))

    for exposure_col, return_col, name in factor_pairs:
        if exposure_col in df.columns and return_col in df.columns:
            scale = df["portfolio_value"] if "portfolio_value" in df.columns else 1.0
            pnl = (df[exposure_col].fillna(0.0) * df[return_col].fillna(0.0) * scale).sum()
            layers.append(Layer(f"factor_{name}", float(pnl), "equity", "style or sector factor P&L"))
    return layers


def _basis_decompose(df: pd.DataFrame, cfg: dict) -> list[Layer]:
    cols = [
        ("spot_pnl", "spot", "spot move P&L"),
        ("roll_carry_pnl", "roll_carry", "roll/carry P&L"),
        ("basis_pnl", "basis", "basis change P&L"),
    ]
    layers = []
    for col, name, desc in cols:
        if col in df.columns:
            layers.append(Layer(name, float(df[col].fillna(0.0).sum()), "futures", desc))
    return layers


def waterfall(trades_df: pd.DataFrame, cfg: dict) -> WaterfallResult:
    """Unified attribution entry point."""
    gross = _gross_pnl(trades_df)
    family = cfg.get("family", "")

    if family in ("equity_options", "futures_options"):
        layers = _greek_decompose(trades_df, cfg)
    elif family == "equity":
        layers = _factor_decompose(trades_df, cfg)
    else:
        layers = _basis_decompose(trades_df, cfg)

    explained = sum(layer.pnl for layer in layers)
    residual = gross - explained
    cost = txcost.total(trades_df, cfg)
    financing = txcost.financing_cost(trades_df, cfg)
    net = gross - cost - financing
    return WaterfallResult(
        gross=float(gross),
        layers=layers,
        residual=float(residual),
        cost=float(cost),
        financing=float(financing),
        net=float(net),
    )


def to_frame(result: WaterfallResult) -> pd.DataFrame:
    """Render waterfall result as a tidy DataFrame."""
    rows = [{"layer": "gross", "pnl": result.gross, "asset": "all"}]
    rows.extend({"layer": layer.name, "pnl": layer.pnl, "asset": layer.asset} for layer in result.layers)
    rows.extend([
        {"layer": "residual", "pnl": result.residual, "asset": "all"},
        {"layer": "cost", "pnl": -result.cost, "asset": "all"},
        {"layer": "financing", "pnl": -result.financing, "asset": "all"},
        {"layer": "net", "pnl": result.net, "asset": "all"},
    ])
    return pd.DataFrame(rows)
