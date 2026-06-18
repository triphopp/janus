"""Run-level data quality scorecard."""

from __future__ import annotations

from typing import Any

import pandas as pd


DEFAULT_BUDGETS = {
    "return_outlier_rate": {"aql": 0.01, "ltpd": 0.05},
    "price_outlier_rate": {"aql": 0.005, "ltpd": 0.02},
    "bound_violation_rate": {"aql": 0.001, "ltpd": 0.01},
    "missing_rate": {"aql": 0.02, "ltpd": 0.10},
    "quarantine_rate": {"aql": 0.01, "ltpd": 0.05},
    "coverage_shortfall": {"aql": 0.05, "ltpd": 0.20},
}

_STATUS_RANK = {"pass": 0, "warn": 1, "fail": 2}


class DataQualityViolation(Exception):
    """Raised when scorecard enforcement is fail and status is fail."""


def _bool_count(df: pd.DataFrame, col: str) -> int:
    if col not in df.columns:
        return 0
    series = df[col]
    if series.dtype == object or str(series.dtype).startswith("string"):
        return int(
            series.fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"1", "true", "t", "yes", "y"})
            .sum()
        )
    return int(series.fillna(False).astype(bool).sum())


def _status(rate: float, aql: float, ltpd: float) -> str:
    if rate <= aql:
        return "pass"
    if rate <= ltpd:
        return "warn"
    return "fail"


def _dimension(name: str, rate: float, n_defect: int, n_total: int, budget: dict[str, Any]) -> dict:
    aql = float(budget.get("aql", 0.0))
    ltpd = float(budget.get("ltpd", aql))
    return {
        "name": name,
        "rate": round(float(rate), 6),
        "n_defect": int(n_defect),
        "n_total": int(n_total),
        "aql": aql,
        "ltpd": ltpd,
        "status": _status(float(rate), aql, ltpd),
    }


def build_scorecard(
    df: pd.DataFrame,
    cfg: dict,
    *,
    contract_gate: dict | None = None,
    coverage_gate: dict | None = None,
) -> dict:
    dq_cfg = cfg.get("data_quality") or {}
    enforcement = dq_cfg.get("enforcement", "warn")
    budgets = {**DEFAULT_BUDGETS, **(dq_cfg.get("budgets") or {})}
    n_total = int(len(df))
    denom = max(n_total, 1)

    dims = []
    for name, col in (
        ("return_outlier_rate", "_return_outlier_flag"),
        ("price_outlier_rate", "_outlier_flag"),
        ("bound_violation_rate", "_bound_flag"),
        ("missing_rate", "_missing_flag"),
    ):
        n_defect = _bool_count(df, col)
        dims.append(_dimension(name, n_defect / denom, n_defect, n_total, budgets[name]))

    contract_gate = contract_gate or {}
    q_rate = float(contract_gate.get("quarantine_rate") or 0.0)
    q_total = int(contract_gate.get("rows_in") or n_total)
    q_defect = int(contract_gate.get("rows_quarantined") or round(q_rate * max(q_total, 1)))
    dims.append(_dimension("quarantine_rate", q_rate, q_defect, q_total, budgets["quarantine_rate"]))

    coverage_gate = coverage_gate or {}
    coverage_ratio = float(coverage_gate.get("coverage_ratio") or 1.0)
    expected = int(coverage_gate.get("expected_trading_days") or 0)
    present = int(coverage_gate.get("present_trading_days") or expected)
    dims.append(
        _dimension(
            "coverage_shortfall",
            max(0.0, 1.0 - coverage_ratio),
            max(0, expected - present),
            expected,
            budgets["coverage_shortfall"],
        )
    )

    worst = max(dims, key=lambda d: (_STATUS_RANK[d["status"]], d["rate"]))
    return {
        "status": worst["status"],
        "enforcement": enforcement,
        "worst_dimension": worst["name"],
        "dimensions": dims,
    }


def enforce_scorecard(scorecard: dict) -> None:
    if scorecard.get("enforcement") == "fail" and scorecard.get("status") == "fail":
        raise DataQualityViolation(
            f"data_quality scorecard failed; worst_dimension={scorecard.get('worst_dimension')}"
        )
