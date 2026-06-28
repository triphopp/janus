"""IV validation trusts exchange IV; inversion is near-money-only (issue 025)."""

import numpy as np
import pandas as pd

from core import options_quality as oq, pricing as pr, option_chain_export as oce
from core.run_readiness import assess_option_market_readiness


def _validate(rows, cfg=None):
    cfg = {"pricing_model": "black76", "iv_validate_threshold": 0.005,
           "price_tick": 0.01, "iv_validate_min_time_value_ticks": 2.0, **(cfg or {})}
    return pr.validate_provided_iv(pd.DataFrame(rows), cfg)


# ── Invertibility (near-money only) ───────────────────────────────────────────

def test_inversion_check_skips_rows_below_min_time_value():
    # deep ITM call: price == intrinsic (no time value) → not invertible, not flagged
    deep = {"option_price": 33.69, "F": 69.69, "strike": 36.0, "T": 0.06, "r": 0.05,
            "right": "C", "iv_provided": 0.56}
    out = _validate([deep])
    assert bool(out["iv_invertible"].iloc[0]) is False
    assert bool(out["iv_flag"].iloc[0]) is False     # exchange IV not penalized


def test_near_money_disagreement_is_flagged_only_when_invertible():
    # near-money with real time value and a big provided/inversion gap → flagged
    nm = {"option_price": 2.10, "F": 70.0, "strike": 70.0, "T": 0.06, "r": 0.05,
          "right": "C", "iv_provided": 2.5}
    out = _validate([nm])
    assert bool(out["iv_invertible"].iloc[0]) is True
    assert bool(out["iv_flag"].iloc[0]) is True


def test_near_money_band_is_config_driven():
    nm = {"option_price": 2.10, "F": 70.0, "strike": 70.0, "T": 0.06, "r": 0.05,
          "right": "C", "iv_provided": 2.5}
    # require an absurd amount of time value → nothing is invertible
    out = _validate([nm], cfg={"iv_validate_min_time_value_ticks": 100000})
    assert bool(out["iv_invertible"].iloc[0]) is False
    assert bool(out["iv_flag"].iloc[0]) is False


# ── Near-money aggregate diagnostic ───────────────────────────────────────────

def _summarize_with_invertible():
    df = pd.DataFrame({
        "instrument_type": ["option"] * 4,
        "right": ["C"] * 4,
        "iv": [0.30, 0.31, 0.30, 0.32],
        "iv_solved": [0.30, 0.31, 0.95, 0.33],     # row2 disagrees
        "iv_diff": [0.00, 0.00, 0.65, 0.01],
        "iv_invertible": [True, True, True, True],
        "iv_flag": [False, False, True, False],
    })
    return oq.summarize(df, {"near_money_iv_mismatch_threshold": 0.05})


def test_near_money_iv_aggregate_diagnostic_computed():
    s = _summarize_with_invertible()["near_money_iv"]
    assert s["invertible_rows"] == 4
    assert s["mismatch_rate"] == 0.25          # 1 of 4 > 0.05
    assert s["median_abs_diff"] is not None


def test_systemic_near_money_mismatch_sets_review_or_blocked():
    summary = {
        "option_rows": 100,
        "iv": {"flag_rate": 0.0},
        "near_money_iv": {"invertible_rows": 100, "mismatch_rate": 0.40},
        "pcp": {"flag_rate": 0.0}, "delta": {"bad_sign_count": 0},
        "premium": {"flag_rate": 0.0}, "underlying_map": {"drop_rate": 0.0},
    }
    out = assess_option_market_readiness(summary)
    chk = out["checks"]["iv_provider_model_mismatch"]
    assert chk["basis"] == "near_money_aggregate"
    assert chk["status"] == "blocked"          # 0.40 >= block 0.20


def test_clean_near_money_aggregate_is_ready():
    summary = {
        "option_rows": 100,
        "iv": {"flag_rate": 0.30},                       # raw flag high...
        "near_money_iv": {"invertible_rows": 100, "mismatch_rate": 0.0},  # ...but aggregate clean
        "pcp": {"flag_rate": 0.0}, "delta": {"bad_sign_count": 0},
        "premium": {"flag_rate": 0.0}, "underlying_map": {"drop_rate": 0.0},
    }
    out = assess_option_market_readiness(summary)
    assert out["checks"]["iv_provider_model_mismatch"]["status"] == "ready"


# ── Export: IV disagreement kept, genuine corruption excluded ─────────────────

def _export_frame():
    base = dict(product_id=425, contract_root="T", hub="WTI",
                delivery_month=pd.Timestamp("2024-11-01"),
                as_of_date=pd.Timestamp("2024-09-25"),
                expiry=pd.Timestamp("2024-10-17"), T=0.06, r=0.05, dte_days=22.0,
                instrument_type="option", underlying_price=70.0)
    return pd.DataFrame([
        {**base, "right": "C", "strike": 36.0, "price": 34.0, "option_price": 34.0,
         "iv": 0.56, "iv_flag": True, "_iv_quality_flag": True, "_premium_quality_flag": False},
        {**base, "right": "P", "strike": 60.0, "price": 0.01, "option_price": 0.01,
         "iv": 0.30, "iv_flag": False, "_iv_quality_flag": False, "_premium_quality_flag": True},
    ])


_CFG = {"family": "futures_options", "pricing_model": "black76", "rf_rate": 0.05,
        "exchange_tz": "America/New_York", "exchange_calendar": "NYMEX",
        "export": {"product": "WTI", "underlying_root": "CL", "option_root": "LO",
                   "exchange": "NYMEX", "currency": "USD", "price_unit": "USD_per_barrel",
                   "contract_unit": "1000_barrels", "price_tick": 0.01}}


def test_provided_iv_run_does_not_exclude_on_inversion_disagreement():
    built = oce.build_option_chain_greeks(_export_frame(), _CFG)
    syms = "".join(built["frame"]["option_symbol"])
    assert "C36" in syms          # IV-disagreement deep-ITM kept
    assert built["frame"][built["frame"]["option_symbol"].str.contains("C36")].iloc[0]["implied_volatility"] == "0.560000"


def test_genuinely_corrupt_row_below_intrinsic_still_excluded():
    built = oce.build_option_chain_greeks(_export_frame(), _CFG)
    assert "P60" not in "".join(built["frame"]["option_symbol"])


def test_missing_or_nonpositive_iv_still_excluded():
    frame = _export_frame()
    frame.loc[frame["strike"] == 36.0, "iv"] = 0.0   # non-positive IV
    built = oce.build_option_chain_greeks(frame, _CFG)
    assert "C36" not in "".join(built["frame"]["option_symbol"])
