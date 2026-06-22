"""Executable contract tests for planned option-cleaning observability.

These tests intentionally describe behavior that is not implemented yet. They
are strict xfails so future implementation agents can remove the marker and use
the same assertions as the acceptance contract.
"""

import numpy as np
import pandas as pd
import pytest


def _five_option_chain() -> pd.DataFrame:
    as_of = pd.Timestamp("2024-01-01")
    delivery = pd.Timestamp("2024-03-01")
    base = {
        "as_of_date": as_of,
        "product_id": 254,
        "contract_root": "B",
        "hub": "North Sea",
        "delivery_month": delivery,
    }
    rows = [
        {
            **base,
            "instrument_type": "future",
            "expiry": delivery,
            "right": None,
            "strike": np.nan,
            "price": 80.0,
            "iv_provided": np.nan,
            "delta_provided": np.nan,
        }
    ]
    for i, strike in enumerate([80.0, 81.0, 82.0, 83.0, 84.0]):
        rows.append({
            **base,
            "instrument_type": "option",
            "expiry": pd.Timestamp("2024-02-15"),
            "right": "C",
            "strike": strike,
            "price": 1.0,
            "iv_provided": 0.25,
            "delta_provided": 0.40,
        })
    return pd.DataFrame(rows)


def test_option_universe_reason_counts_for_provided_iv_and_delta():
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    raw = _five_option_chain()
    opts = raw.index[raw["instrument_type"] == "option"].tolist()
    raw.loc[opts[1], "expiry"] = raw.loc[opts[1], "as_of_date"]
    raw.loc[opts[2], "price"] = 0.0
    raw.loc[opts[3], "iv_provided"] = 2.5
    raw.loc[opts[4], "delta_provided"] = 0.95

    df, cfg = FuturesOptionsAdapter({
        "pricing_model": "black76",
        "iv_source": "provided",
        "compute_greeks": False,
        "dte": {"basis": "calendar", "day_count": "act_365"},
        "option_universe": {
            "min_dte_days": 1,
            "min_option_price": 0.01,
            "max_iv": 1.0,
            "delta_band": {"min_abs_delta": 0.20, "max_abs_delta": 0.80},
        },
    }).prepare(raw)

    assert (df["instrument_type"] == "future").sum() == 1
    assert (df["instrument_type"] == "option").sum() == 1
    assert cfg["option_quality"]["universe_drop_rows"] == 4
    assert cfg["option_quality"]["universe_drop_by_reason"] == {
        "dte_below_min": 1,
        "premium_below_min": 1,
        "iv_above_cap": 1,
        "delta_above_max": 1,
    }


def test_solved_iv_trim_distinguishes_high_iv_from_unsolved_iv():
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    as_of = pd.Timestamp("2024-01-01")
    delivery = pd.Timestamp("2024-03-01")
    expiry = pd.Timestamp("2024-02-15")
    raw = pd.DataFrame([
        {
            "as_of_date": as_of,
            "product_id": 254,
            "contract_root": "B",
            "hub": "North Sea",
            "instrument_type": "future",
            "delivery_month": delivery,
            "expiry": delivery,
            "right": None,
            "strike": np.nan,
            "price": 100.0,
        },
        {
            "as_of_date": as_of,
            "product_id": 254,
            "contract_root": "B",
            "hub": "North Sea",
            "instrument_type": "option",
            "delivery_month": delivery,
            "expiry": expiry,
            "right": "C",
            "strike": 100.0,
            "price": 20.0,
        },
        {
            "as_of_date": as_of,
            "product_id": 254,
            "contract_root": "B",
            "hub": "North Sea",
            "instrument_type": "option",
            "delivery_month": delivery,
            "expiry": expiry,
            "right": "P",
            "strike": 130.0,
            "price": 0.01,
        },
    ])

    _, cfg = FuturesOptionsAdapter({
        "pricing_model": "black76",
        "iv_source": "solve",
        "compute_greeks": False,
        "dte": {"basis": "calendar", "day_count": "act_365"},
        "option_universe": {"max_iv": 0.20},
    }).prepare(raw)

    reasons = cfg["option_quality"]["universe_drop_by_reason"]
    assert reasons["iv_above_cap"] == 1
    assert reasons["iv_missing_or_unsolved"] == 1


def test_deprecated_iv_cap_alias_does_not_override_canonical_universe_key():
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    explicit = FuturesOptionsAdapter({
        "family": "futures_options",
        "iv_cap": 3.0,
        "option_universe": {"max_iv": 1.0},
    })
    assert explicit._option_universe_cfg()["max_iv"] == 1.0

    alias = FuturesOptionsAdapter({"family": "futures_options", "iv_cap": 3.0})
    assert alias._option_universe_cfg()["max_iv"] == 3.0
    assert "validation.iv_cap is deprecated" in " ".join(alias._config_warnings)


def test_futures_missing_underlying_map_is_reported_separately():
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    raw = _five_option_chain()
    extra = raw[raw["instrument_type"] == "option"].head(1).copy()
    extra["as_of_date"] = pd.Timestamp("2024-01-02")
    raw = pd.concat([raw, extra], ignore_index=True)

    df, cfg = FuturesOptionsAdapter({
        "pricing_model": "black76",
        "iv_source": "provided",
        "compute_greeks": False,
        "dte": {"basis": "calendar", "day_count": "act_365"},
    }).prepare(raw)

    assert pd.Timestamp("2024-01-02") not in set(df.loc[df["instrument_type"] == "option", "as_of_date"])
    assert cfg["option_quality"]["underlying_map"]["missing_rows"] == 1
    assert cfg["option_quality"]["underlying_map"]["drop_rate"] == pytest.approx(1 / 6)
    assert cfg["option_quality"]["universe_drop_by_reason"]["missing_underlying_future"] == 1


def test_wide_spread_is_universe_exclusion_not_quarantine():
    from adapters.equity_options_adapter import EquityOptionsAdapter
    from core import contracts as cmod

    raw = pd.DataFrame({
        "as_of_date": [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-01")],
        "available_at": pd.to_datetime(["2024-01-01T16:00:00Z"] * 2),
        "ingested_at": pd.to_datetime(["2024-01-01T16:01:00Z"] * 2),
        "symbol": ["AAPL", "AAPL"],
        "expiry": [pd.Timestamp("2024-03-01"), pd.Timestamp("2024-03-01")],
        "right": ["C", "C"],
        "strike": [100.0, 105.0],
        "price": [5.0, 5.0],
        "underlying_price": [100.0, 100.0],
        "raw_close": [100.0, 100.0],
        "adj_factor": [1.0, 1.0],
        "iv_provided": [0.25, 0.25],
        "volume": [10, 10],
        "open_interest": [100, 100],
        "instrument_type": ["option", "option"],
        "relative_spread": [0.05, 0.75],
    })

    contract = cmod.load_contract("equity_options", 1, "contracts")
    contract_result = cmod.validate(raw, contract)
    _, cfg = EquityOptionsAdapter({
        "pricing_model": "bsm",
        "iv_source": "provided",
        "compute_greeks": False,
        "dte": {"basis": "calendar", "day_count": "act_365"},
        "option_universe": {"max_relative_spread": 0.25},
    }).prepare(contract_result.passed)

    assert contract_result.report["rows_quarantined"] == 0
    assert cfg["option_quality"]["universe_drop_by_reason"]["spread_above_max"] == 1
