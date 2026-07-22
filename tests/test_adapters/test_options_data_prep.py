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


def test_bachelier_rejects_fractional_exchange_iv_without_explicit_unit():
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    with pytest.raises(ValueError, match="Provided IV unit is incompatible"):
        FuturesOptionsAdapter({
            "pricing_model": "bachelier",
            "iv_source": "provided",
            "dte": {"basis": "calendar", "day_count": "act_365"},
            "vol_window": 5,
        }).prepare(_mixed_futures_options_fixture())


def test_futures_options_option_only_fails_fast():
    """Futures options require PIT futures rows for the underlying map."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    raw = _mixed_futures_options_fixture()
    raw = raw[raw["instrument_type"] == "option"].copy()

    with pytest.raises(ValueError, match="underlying future"):
        FuturesOptionsAdapter({"pricing_model": "black76", "iv_source": "provided"}).prepare(raw)


def test_futures_options_drops_only_rows_missing_underlying_map():
    """One bad chain date should not fail the whole futures-options run."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    raw = _mixed_futures_options_fixture()
    bad_future = (
        (raw["instrument_type"] == "future")
        & (raw["as_of_date"] == pd.Timestamp("2024-01-02"))
    )
    raw = raw.loc[~bad_future].copy()

    df, _ = FuturesOptionsAdapter({
        "pricing_model": "black76",
        "iv_source": "provided",
        "dte": {"basis": "calendar", "day_count": "act_365"},
        "vol_window": 5,
    }).prepare(raw)

    options = df[df["instrument_type"] == "option"]
    assert options["as_of_date"].tolist() == [pd.Timestamp("2024-01-01")]
    assert options["F"].tolist() == [80.0]


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


def test_pcp_black76_european_alias_uses_equality_gate():
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    df = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-01", "2024-01-01"]),
        "product_id": 254,
        "contract_root": "B",
        "hub": "North Sea",
        "delivery_month": pd.Timestamp("2024-03-01"),
        "expiry": pd.Timestamp("2024-02-01"),
        "strike": 100.0,
        "right": ["C", "P"],
        "price": [12.0, 2.0],
        "option_price": [12.0, 2.0],
        "F": [100.0, 100.0],
        "T": 30.0 / 365.0,
        "r": 0.0,
    })

    adapter = FuturesOptionsAdapter({"pricing_model": "black76_european"})
    checked = adapter.check_pcp(df)
    assert checked["_pcp_flag"].all()
    assert adapter._option_quality["pcp_check_mode"] == "equality"


def test_pcp_american_model_checks_individual_premium_bounds():
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    df = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-01", "2024-01-01"]),
        "product_id": 254,
        "contract_root": "B",
        "hub": "North Sea",
        "delivery_month": pd.Timestamp("2024-03-01"),
        "expiry": pd.Timestamp("2024-02-01"),
        "strike": 100.0,
        "right": ["C", "P"],
        "price": [12.0, 2.0],
        "option_price": [12.0, 2.0],
        "F": [100.0, 100.0],
        "T": 30.0 / 365.0,
        "r": 0.0,
    })

    adapter = FuturesOptionsAdapter({"pricing_model": "black76_baw"})
    checked = adapter.check_pcp(df)
    assert not checked["_pcp_flag"].any()
    assert adapter._option_quality["pcp_check_status"] == "checked_american_bounds"

    df.loc[df["right"] == "C", "option_price"] = -1.0
    checked = adapter.check_pcp(df)
    assert checked.loc[df["right"] == "C", "_pcp_flag"].all()


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
        "adj_factor_is_pit": [True],
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


def test_equity_options_blocks_retro_adjustment_by_default():
    """Retro provider factors must not feed option pricing unless explicitly PIT."""
    from adapters.equity_options_adapter import EquityOptionsAdapter

    raw = pd.DataFrame({
        "as_of_date": [pd.Timestamp("2024-01-01")],
        "product_id": [500],
        "raw_close": [100.0],
        "adj_factor": [0.5],
        "adj_factor_is_pit": [False],
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

    assert df.loc[0, "underlying_price"] == 100.0
    assert df.loc[0, "strike"] == 100.0
    assert df.loc[0, "adjusted_price_provider"] == 50.0
    assert df.loc[0, "strike_adjusted_provider"] == 50.0
    assert bool(df.loc[0, "price_adjustment_warning"])


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

    assert core_cfg["label_end_col"] == "expiry"
    assert "delivery_month" in core_cfg.get("outlier_identity_cols", [])


def test_futures_options_universe_filter_drops_expiry_day_and_long_tail():
    """WTI-sized chains can narrow option rows before expensive IV/Greeks work."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    raw = _mixed_futures_options_fixture()
    extra = []
    as_of = pd.Timestamp("2024-01-01")
    delivery = pd.Timestamp("2024-03-01")
    extra.append({
        "as_of_date": as_of,
        "product_id": 254,
        "contract_root": "B",
        "hub": "North Sea",
        "instrument_type": "option",
        "delivery_month": delivery,
        "expiry": as_of,
        "right": "P",
        "strike": 80.0,
        "price": 0.0,
        "iv_provided": 0.25,
    })
    extra.append({
        "as_of_date": as_of,
        "product_id": 254,
        "contract_root": "B",
        "hub": "North Sea",
        "instrument_type": "option",
        "delivery_month": delivery,
        "expiry": pd.Timestamp("2027-01-01"),
        "right": "C",
        "strike": 80.0,
        "price": 5.0,
        "iv_provided": 0.25,
    })
    raw = pd.concat([raw, pd.DataFrame(extra)], ignore_index=True)

    df, _ = FuturesOptionsAdapter({
        "pricing_model": "black76",
        "iv_source": "provided",
        "dte": {"basis": "calendar", "day_count": "act_365"},
        "vol_window": 5,
        "option_universe": {"min_dte_days": 1, "max_dte_days": 730, "min_option_price": 0.00001},
    }).prepare(raw)

    options = df[df["instrument_type"] == "option"]
    assert (options["dte_days"] >= 1).all()
    assert (options["dte_days"] <= 730).all()
    assert (options["option_price"] > 0).all()
    assert (df["instrument_type"] == "future").any()


def test_futures_options_universe_filters_provided_iv_and_delta_band():
    """Provided IV/delta filters should run before expensive solve/Greek loops."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    raw = _mixed_futures_options_fixture()
    option_idx = raw.index[raw["instrument_type"] == "option"].tolist()
    raw["delta_provided"] = np.nan
    raw.loc[option_idx[0], "delta_provided"] = 0.40
    raw.loc[option_idx[1], "delta_provided"] = 0.90
    raw.loc[option_idx[1], "iv_provided"] = 2.50

    df, _ = FuturesOptionsAdapter({
        "pricing_model": "black76",
        "iv_source": "provided",
        "compute_greeks": False,
        "dte": {"basis": "calendar", "day_count": "act_365"},
        "vol_window": 5,
        "option_universe": {
            "max_iv": 1.0,
            "delta_band": {"min_abs_delta": 0.20, "max_abs_delta": 0.80},
        },
    }).prepare(raw)

    options = df[df["instrument_type"] == "option"]
    assert options["iv"].tolist() == [0.25]
    assert options["delta_provided"].tolist() == [0.40]
    assert (df["instrument_type"] == "future").any()


def test_delta_band_can_filter_after_computed_greeks():
    """When no provided delta exists, delta band applies after Greeks are computed."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    df = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-01", "2024-01-01"]),
        "instrument_type": ["option", "option"],
        "right": ["C", "C"],
        "strike": [100.0, 200.0],
        "price": [5.0, 0.1],
        "option_price": [5.0, 0.1],
        "underlying_price": [100.0, 100.0],
        "F": [100.0, 100.0],
        "T": [0.5, 0.5],
        "r": [0.0, 0.0],
        "iv": [0.20, 0.20],
    })

    out = FuturesOptionsAdapter({
        "pricing_model": "black76",
        "option_universe": {"delta_band": {"min_abs_delta": 0.20, "max_abs_delta": 0.80}},
    }).compute_greeks(df)

    assert out["strike"].tolist() == [100.0]
    assert out["delta"].between(0.20, 0.80).all()


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


def _identity_tagged_wti_fixture() -> pd.DataFrame:
    raw = _mixed_futures_options_fixture().copy()
    raw["product_id"] = 425
    raw["contract_root"] = "T"
    raw["hub"] = "WTI"
    raw["product_family"] = "futures_options"
    raw["option_underlying_type"] = "future"
    raw["exercise_style"] = "american"
    raw["product_identity_status"] = "resolved"
    raw["source_option_root"] = "T"
    raw["underlying_root"] = "T"
    raw["equivalent_option_root_cme"] = "LO"
    return raw


def test_futures_options_adapter_rejects_product_family_mismatch():
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    raw = _identity_tagged_wti_fixture()
    raw.loc[raw["instrument_type"] == "option", "product_family"] = "equity_options"

    with pytest.raises(ValueError, match="different product_family"):
        FuturesOptionsAdapter({
            "pricing_model": "black76",
            "iv_source": "provided",
        }).prepare(raw)


def test_auto_pricing_selects_implemented_american_target_for_official_run():
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    df, core_cfg = FuturesOptionsAdapter({
        "pricing_model": "auto",
        "iv_source": "provided",
        "compute_greeks": False,
        "dte": {"basis": "calendar", "day_count": "act_365"},
        "vol_window": 5,
        "require_fixed_data_version": True,
    }).prepare(_identity_tagged_wti_fixture())

    opts = df[df["instrument_type"] == "option"]
    assert core_cfg["pricing_model"] == "black76_baw"
    assert set(opts["pricing_model_source"]) == {"policy_default"}
    assert opts["pricing_model_contract_match"].all()
    assert not opts["is_model_approximation"].all()
    assert opts["baw_boundary_converged"].all()
    assert set(opts["pricing_status"]) == {"ok"}
    assert core_cfg["option_quality"]["pricing_model_diagnostics"]["validity_warnings"] == 0


def test_auto_pricing_diagnostic_still_selects_implemented_american_model():
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    df, core_cfg = FuturesOptionsAdapter({
        "pricing_model": "auto",
        "allow_model_approximation": True,
        "preset": "diagnostic",
        "require_fixed_data_version": False,
        "iv_source": "provided",
        "compute_greeks": False,
        "dte": {"basis": "calendar", "day_count": "act_365"},
        "vol_window": 5,
    }).prepare(_identity_tagged_wti_fixture())

    opts = df[df["instrument_type"] == "option"]
    assert core_cfg["pricing_model"] == "black76_baw"
    assert set(opts["pricing_model_target"]) == {"black76_baw"}
    assert set(opts["pricing_model_source"]) == {"policy_default"}
    assert opts["pricing_model_contract_match"].all()
    assert not opts["is_model_approximation"].all()
    assert set(opts["pricing_model_contract_reason"]) == {"policy_default_contract_match"}


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


def test_equity_options_core_cfg_uses_expiry_as_label_end():
    from adapters.equity_options_adapter import EquityOptionsAdapter

    raw = pd.DataFrame({
        "as_of_date": [pd.Timestamp("2024-01-01")],
        "product_id": [500],
        "raw_close": [100.0],
        "adj_factor": [1.0],
        "price": [5.0],
        "strike": [100.0],
        "expiry": [pd.Timestamp("2024-03-01")],
        "right": ["C"],
        "iv_provided": [0.25],
    })

    _, core_cfg = EquityOptionsAdapter({
        "pricing_model": "bsm",
        "iv_source": "provided",
        "dte": {"basis": "calendar", "day_count": "act_365"},
    }).prepare(raw)

    assert core_cfg["label_end_col"] == "expiry"


# ---------------------------------------------------------------------------
# Vectorized compute_greeks adapter tests (Level 1 + 2)
# ---------------------------------------------------------------------------

def _option_row(
    right="C", strike=80.0, S=80.0, T=0.5, r=0.05, iv=0.25, instrument_type="option"
) -> dict:
    return {
        "instrument_type": instrument_type,
        "right": right,
        "strike": strike,
        "underlying_price": S,
        "T": T,
        "r": r,
        "iv": iv,
    }


def test_compute_greeks_vectorized_matches_scalar_contract():
    """Vectorized compute_greeks matches single_leg_greeks for valid option rows.
    Future rows keep Greek columns as NaN."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter
    from core.greeks import single_leg_greeks

    rows = [
        _option_row("C", 80.0, 80.0, 0.5, 0.05, 0.25),   # valid call
        _option_row("P", 75.0, 80.0, 0.5, 0.05, 0.30),   # valid put
        _option_row("C", 80.0, 80.0, 0.0, 0.05, 0.25),   # invalid T=0
        _option_row("C", 80.0, 80.0, 0.5, 0.05, np.nan), # invalid iv=NaN
        {"instrument_type": "future", "right": None, "strike": np.nan,
         "underlying_price": 80.0, "T": np.nan, "r": 0.05, "iv": np.nan},
    ]
    df = pd.DataFrame(rows)

    out = FuturesOptionsAdapter({"pricing_model": "black76"}).compute_greeks(df)

    valid_idx = [0, 1]
    invalid_idx = [2, 3, 4]

    for idx in invalid_idx:
        for g in ("delta", "gamma", "vega", "theta", "rho"):
            assert pd.isna(out.iloc[idx][g]), f"row {idx} {g} should be NaN"

    for local_i, idx in enumerate(valid_idx):
        row = rows[idx]
        expected = single_leg_greeks(
            "black76", row["underlying_price"], row["strike"], row["T"], row["r"], row["iv"], row["right"]
        )
        for g in ("delta", "gamma", "vega", "theta", "rho"):
            assert out.iloc[idx][g] == pytest.approx(expected[g], rel=1e-10, abs=1e-12), (
                f"row {idx} {g}: vectorized != scalar"
            )


def test_compute_greeks_preserves_underlying_precedence():
    """Vectorized path respects underlying_price > S > F > price_std precedence."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter
    from core.greeks import single_leg_greeks

    # Row with all four columns present: underlying_price wins
    df = pd.DataFrame([{
        "instrument_type": "option",
        "right": "C",
        "strike": 80.0,
        "underlying_price": 80.0,
        "S": 999.0,
        "F": 888.0,
        "price_std": 777.0,
        "T": 0.5,
        "r": 0.05,
        "iv": 0.25,
    }])
    out = FuturesOptionsAdapter({"pricing_model": "black76"}).compute_greeks(df)
    expected = single_leg_greeks("black76", 80.0, 80.0, 0.5, 0.05, 0.25, "C")
    assert out.iloc[0]["delta"] == pytest.approx(expected["delta"], rel=1e-10)

    # Row with underlying_price NaN, S wins
    df2 = pd.DataFrame([{
        "instrument_type": "option",
        "right": "C",
        "strike": 80.0,
        "underlying_price": np.nan,
        "S": 80.0,
        "F": 888.0,
        "price_std": 777.0,
        "T": 0.5,
        "r": 0.05,
        "iv": 0.25,
    }])
    out2 = FuturesOptionsAdapter({"pricing_model": "black76"}).compute_greeks(df2)
    assert out2.iloc[0]["delta"] == pytest.approx(expected["delta"], rel=1e-10)

    # Row with only F
    df3 = pd.DataFrame([{
        "instrument_type": "option",
        "right": "C",
        "strike": 80.0,
        "F": 80.0,
        "T": 0.5,
        "r": 0.05,
        "iv": 0.25,
    }])
    out3 = FuturesOptionsAdapter({"pricing_model": "black76"}).compute_greeks(df3)
    assert out3.iloc[0]["delta"] == pytest.approx(expected["delta"], rel=1e-10)


def test_delta_band_can_filter_after_vectorized_greeks():
    """When no provided delta exists, delta band applies after Greeks are computed."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    df = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-01", "2024-01-01"]),
        "instrument_type": ["option", "option"],
        "right": ["C", "C"],
        "strike": [100.0, 200.0],
        "price": [5.0, 0.1],
        "option_price": [5.0, 0.1],
        "underlying_price": [100.0, 100.0],
        "F": [100.0, 100.0],
        "T": [0.5, 0.5],
        "r": [0.0, 0.0],
        "iv": [0.20, 0.20],
    })

    out = FuturesOptionsAdapter({
        "pricing_model": "black76",
        "option_universe": {"delta_band": {"min_abs_delta": 0.20, "max_abs_delta": 0.80}},
    }).compute_greeks(df)

    assert out["strike"].tolist() == [100.0]
    assert out["delta"].between(0.20, 0.80).all()


def test_compute_greeks_reads_pricing_backend_config():
    """pricing.greeks_backend config is respected; Greeks still computed correctly."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    df = pd.DataFrame([_option_row("C", 80.0, 80.0, 0.5, 0.05, 0.25)])
    adapter = FuturesOptionsAdapter({
        "pricing_model": "black76",
        "pricing": {"compute_greeks": True, "greeks_backend": "numpy", "greeks_batch_size": 2},
    })
    out = adapter.compute_greeks(df)

    assert pd.notna(out.iloc[0]["delta"])
    assert adapter._option_quality["greeks_runtime"]["requested_backend"] == "numpy"
    assert adapter._option_quality["greeks_runtime"]["resolved_backend"] == "numpy"
    assert adapter._option_quality["greeks_runtime"]["rows"] == 1


def test_compute_greeks_backend_loop_matches_numpy():
    """loop backend and numpy backend produce identical Greek values."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter
    from core.greeks import single_leg_greeks

    rows = [_option_row("C", K, 80.0, 0.5, 0.05, 0.25) for K in [70.0, 80.0, 90.0]]
    rows += [_option_row("P", K, 80.0, 0.5, 0.05, 0.25) for K in [70.0, 80.0, 90.0]]
    df = pd.DataFrame(rows)

    cfg_base = {"pricing_model": "black76"}
    out_np = FuturesOptionsAdapter({**cfg_base, "greeks_backend": "numpy"}).compute_greeks(df.copy())
    out_lp = FuturesOptionsAdapter({**cfg_base, "greeks_backend": "loop"}).compute_greeks(df.copy())

    for g in ("delta", "gamma", "vega", "theta", "rho"):
        np.testing.assert_allclose(out_lp[g].values, out_np[g].values, rtol=1e-10, atol=1e-12,
                                    err_msg=f"{g}: loop != numpy in adapter")


def test_compute_greeks_disabled_still_returns_nan_columns():
    """compute_greeks=False: columns exist but are all NaN."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    df = pd.DataFrame([_option_row("C", 80.0, 80.0, 0.5, 0.05, 0.25)])
    out = FuturesOptionsAdapter({
        "pricing_model": "black76",
        "compute_greeks": False,
    }).compute_greeks(df)

    for g in ("delta", "gamma", "vega", "theta", "rho"):
        assert g in out.columns
        assert out[g].isna().all(), f"{g} should be NaN when compute_greeks=False"


def test_backend_selection_does_not_change_outputs():
    """loop, numpy, and auto backends produce equivalent Greek values."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    rows = [_option_row(r, K, 80.0, T, 0.05, 0.25)
            for r in ("C", "P") for K in (75.0, 80.0, 85.0) for T in (0.25, 0.5)]
    df = pd.DataFrame(rows)

    cfg_base = {"pricing_model": "black76"}
    results = {
        b: FuturesOptionsAdapter({**cfg_base, "greeks_backend": b}).compute_greeks(df.copy())
        for b in ("loop", "numpy", "auto")
    }

    ref = results["numpy"]
    for b in ("loop", "auto"):
        for g in ("delta", "gamma", "vega", "theta", "rho"):
            np.testing.assert_allclose(
                results[b][g].values, ref[g].values,
                rtol=1e-10, atol=1e-12,
                err_msg=f"{g}: backend={b!r} differs from numpy in adapter",
            )


# Leakage guard tests

def test_compute_greeks_does_not_use_future_context_rows():
    """Future/context rows must not affect option row Greeks."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    opt_row = _option_row("C", 80.0, 80.0, 0.5, 0.05, 0.25)
    # Extreme future/context row — if leaked, would drastically change delta
    ctx_row = {
        "instrument_type": "future",
        "right": None,
        "strike": np.nan,
        "underlying_price": 9999.0,
        "S": 9999.0,
        "F": 9999.0,
        "price_std": 9999.0,
        "T": np.nan,
        "r": 0.05,
        "iv": np.nan,
    }
    adapter = FuturesOptionsAdapter({"pricing_model": "black76"})

    # With context row present
    df_with = pd.DataFrame([opt_row, ctx_row])
    out_with = adapter.compute_greeks(df_with)
    delta_with = out_with[out_with["instrument_type"] == "option"]["delta"].values[0]

    # Without context row
    df_without = pd.DataFrame([opt_row])
    out_without = adapter.compute_greeks(df_without)
    delta_without = out_without["delta"].values[0]

    assert delta_with == pytest.approx(delta_without, rel=1e-10), (
        "Option delta changed when future/context row was added (future-row leakage)"
    )
    # Context row keeps NaN Greeks
    ctx_out = out_with[out_with["instrument_type"] == "future"]
    for g in ("delta", "gamma", "vega", "theta", "rho"):
        assert ctx_out[g].isna().all(), f"Future row should have NaN {g}"


def test_compute_greeks_does_not_use_later_option_rows():
    """Earlier-date Greeks are unaffected by later-date rows with extreme params."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    early = {**_option_row("C", 80.0, 80.0, 0.5, 0.05, 0.25), "as_of_date": pd.Timestamp("2024-01-01")}
    # Extreme later-date row
    late = {**_option_row("C", 9999.0, 9999.0, 5.0, 0.05, 9.99), "as_of_date": pd.Timestamp("2024-06-01")}

    adapter = FuturesOptionsAdapter({"pricing_model": "black76"})

    out_full = adapter.compute_greeks(pd.DataFrame([early, late]))
    out_early_only = adapter.compute_greeks(pd.DataFrame([early]))

    delta_full = out_full.iloc[0]["delta"]
    delta_only = out_early_only.iloc[0]["delta"]
    assert delta_full == pytest.approx(delta_only, rel=1e-10), (
        "Earlier-date delta changed when later-date row was present (lookahead leakage)"
    )


def test_event_calendar_respects_available_at_lag(tmp_path):
    """Event rows must not become known before their configured release lag."""
    from adapters.futures_adapter import FuturesAdapter

    event_file = tmp_path / "eia.csv"
    event_file.write_text("date,event,impact\n2024-01-02,EIA,high\n", encoding="utf-8")
    raw = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-02"]),
        "available_at": pd.to_datetime(["2024-01-02T03:00:00Z"]),
        "decision_time": pd.to_datetime(["2024-01-02T03:00:00Z"]),
        "price": [80.0],
    })

    out = FuturesAdapter({
        "event_calendars": [str(event_file)],
        "available_at_lag": {"eia_inventory": "P5D"},
    }).flag_scheduled_events(raw)

    assert not bool(out.loc[0, "scheduled_event"])

    out = FuturesAdapter({
        "event_calendars": [str(event_file)],
        "available_at_lag": {"eia_inventory": "0h"},
    }).flag_scheduled_events(raw)

    assert bool(out.loc[0, "scheduled_event"])
