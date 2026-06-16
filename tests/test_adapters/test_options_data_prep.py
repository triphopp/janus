"""Regression tests for options data-prep invariants."""

import numpy as np
import pandas as pd
import pytest


def _mixed_futures_options_fixture() -> pd.DataFrame:
    dates = pd.to_datetime(["2024-01-01", "2024-01-02"])
    rows = []
    for i, as_of in enumerate(dates):
        delivery = pd.Timestamp("2024-03-01")
        rows.append({
            "as_of_date": as_of,
            "product_id": 254,
            "contract_root": "B",
            "hub": "North Sea",
            "instrument_type": "future",
            "delivery_month": delivery,
            "expiry": delivery,
            "right": None,
            "strike": np.nan,
            "price": 80.0 + i,
            "iv_provided": np.nan,
        })
        rows.append({
            "as_of_date": as_of,
            "product_id": 254,
            "contract_root": "B",
            "hub": "North Sea",
            "instrument_type": "option",
            "delivery_month": delivery,
            "expiry": pd.Timestamp("2024-02-20"),
            "right": "C",
            "strike": 80.0,
            "price": 5.0 + i,
            "iv_provided": 0.25,
        })
    return pd.DataFrame(rows)


def test_futures_options_maps_underlying_price_not_premium():
    """Option F must come from the matching future, not the option premium."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    cfg = {
        "pricing_model": "black76",
        "iv_source": "provided",
        "dte": {"basis": "calendar", "day_count": "act_365"},
        "vol_window": 5,
    }

    df, _ = FuturesOptionsAdapter(cfg).prepare(_mixed_futures_options_fixture())
    options = df[df["instrument_type"] == "option"].sort_values("as_of_date")

    assert options["option_price"].tolist() == [5.0, 6.0]
    assert options["F"].tolist() == [80.0, 81.0]
    assert not (options["F"] == options["option_price"]).any()


def test_futures_options_option_only_fails_fast():
    """Futures options require PIT futures rows for the underlying map."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    raw = _mixed_futures_options_fixture()
    raw = raw[raw["instrument_type"] == "option"].copy()

    with pytest.raises(ValueError, match="underlying future"):
        FuturesOptionsAdapter({"pricing_model": "black76", "iv_source": "provided"}).prepare(raw)


def test_pcp_pairs_do_not_cross_dates():
    """Same expiry/strike across dates must not create cross-date PCP flags."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    df = pd.DataFrame({
        "as_of_date": pd.to_datetime([
            "2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"
        ]),
        "product_id": 254,
        "contract_root": "B",
        "hub": "North Sea",
        "delivery_month": pd.Timestamp("2024-03-01"),
        "expiry": pd.Timestamp("2024-02-01"),
        "strike": 100.0,
        "right": ["C", "P", "C", "P"],
        "price": [5.0, 5.0, 12.0, 2.0],
        "option_price": [5.0, 5.0, 12.0, 2.0],
        "F": [100.0, 100.0, 110.0, 110.0],
        "T": 30.0 / 365.0,
        "r": 0.0,
    })

    checked = FuturesOptionsAdapter({"pricing_model": "black76"}).check_pcp(df)

    assert checked["_pcp_flag"].sum() == 0
    assert checked["pcp_pair_missing"].sum() == 0
    assert checked["pcp_duplicate_pair"].sum() == 0


def test_equity_options_requires_chain_schema():
    """Equity-options adapters must reject equity OHLC without option-chain columns."""
    from adapters.equity_options_adapter import EquityOptionsAdapter

    raw = pd.DataFrame({
        "as_of_date": pd.date_range("2024-01-01", periods=3, freq="B"),
        "raw_close": [100.0, 101.0, 102.0],
        "adj_factor": 1.0,
        "volume": 1000,
    })

    with pytest.raises(ValueError, match="option chain"):
        EquityOptionsAdapter({"pricing_model": "bsm"}).prepare(raw)


def test_equity_strike_adjustment_split_fixture():
    """Adjusted strikes should stay on the same scale as adjusted spot."""
    from adapters.equity_options_adapter import EquityOptionsAdapter

    raw = pd.DataFrame({
        "as_of_date": [pd.Timestamp("2024-01-01")],
        "product_id": [500],
        "raw_close": [100.0],
        "adj_factor": [0.5],
        "price": [5.0],
        "strike": [100.0],
        "expiry": [pd.Timestamp("2024-03-01")],
        "right": ["C"],
        "iv_provided": [0.25],
    })

    df, _ = EquityOptionsAdapter({
        "pricing_model": "bsm",
        "iv_source": "provided",
        "dte": {"basis": "calendar", "day_count": "act_365"},
    }).prepare(raw)

    assert df.loc[0, "underlying_price"] == 50.0
    assert df.loc[0, "strike"] == 50.0


def test_futures_options_identity_cols_set_to_full_contract_key():
    """core_cfg must carry identity_cols covering expiry/right/strike so that
    missing_completeness does not flag chain rows as duplicate_identity_date."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    cfg = {
        "pricing_model": "black76",
        "iv_source": "provided",
        "dte": {"basis": "calendar", "day_count": "act_365"},
        "vol_window": 5,
    }
    _, core_cfg = FuturesOptionsAdapter(cfg).prepare(_mixed_futures_options_fixture())

    identity = core_cfg.get("identity_cols", [])
    for key_col in ("expiry", "right", "strike"):
        assert key_col in identity, f"identity_cols must include '{key_col}'; got {identity}"


def test_skew_direction_absent_from_futures_options_regime_axes():
    """skew_direction is a placeholder (always 0.0) and must not appear in regime_axes."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    cfg = {
        "pricing_model": "black76",
        "iv_source": "provided",
        "dte": {"basis": "calendar", "day_count": "act_365"},
        "vol_window": 5,
    }
    _, core_cfg = FuturesOptionsAdapter(cfg).prepare(_mixed_futures_options_fixture())

    assert "skew_direction" not in core_cfg.get("regime_axes", []), (
        "skew_direction must be removed from regime_axes until compute_skew is implemented"
    )


def test_nested_options_config_is_normalized_before_adapter_math():
    """Adapters should see nested pricing/cv config as canonical flat keys."""
    from adapters.equity_options_adapter import EquityOptionsAdapter

    adapter = EquityOptionsAdapter({
        "pricing": {
            "model": "bsm",
            "div_yield": 0.015,
            "iv_solver_bounds": [0.0001, 4.0],
        },
        "cv": {"n_folds": 3, "purge_bars": 7},
        "validation": {"min_oi": 100},
    })

    assert adapter.cfg["pricing_model"] == "bsm"
    assert adapter.cfg["div_yield"] == 0.015
    assert adapter.cfg["iv_solver_bounds"] == [0.0001, 4.0]
    assert adapter.cfg["n_folds"] == 3
    assert adapter.cfg["purge_bars"] == 7
    assert adapter.cfg["min_oi"] == 100
