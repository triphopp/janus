import numpy as np
import pandas as pd

from core import greeks
from core import model_comparison
from core import pricing


def _frame():
    params = {
        "tree_steps": 300,
        "tree_exercise_style": "american",
        "tree_underlying_type": "future",
    }
    market_price = pricing.price(
        "black76_baw", 100.0, 100.0, 0.5, 0.05, 0.25, "P"
    )
    delta = greeks.single_leg_greeks(
        "black76_baw", 100.0, 100.0, 0.5, 0.05, 0.25, "P"
    )["delta"]
    return pd.DataFrame({
        "instrument_type": ["option"],
        "as_of_date": [pd.Timestamp("2024-01-02")],
        "expiry": [pd.Timestamp("2024-07-02")],
        "exercise_style": ["american"],
        "option_underlying_type": ["future"],
        "right": ["P"],
        "strike": [100.0],
        "F": [100.0],
        "T": [0.5],
        "r": [0.05],
        "option_price": [market_price],
        "iv": [0.25],
        "delta": [delta],
    }), params


def test_comparison_calibrates_models_and_keeps_volatility_units_separate():
    frame, params = _frame()
    cfg = {
        "pricing_model": "black76_baw",
        "compare_models": ["black76_european", "crr_binomial", "bachelier"],
        **params,
    }
    built = model_comparison.compare_models(frame, cfg)
    out = built["frame"].set_index("comparison_model")

    assert set(out.index) == {"black76_european", "crr_binomial", "bachelier"}
    assert set(out["comparison_status"]) == {"ok"}
    assert np.max(np.abs(out["calibration_residual"])) < 1e-5
    assert np.isfinite(out.loc["crr_binomial", "price_difference_at_canonical_iv"])
    assert np.isnan(out.loc["bachelier", "price_difference_at_canonical_iv"])
    assert out.loc["bachelier", "comparison_volatility_unit"] == (
        "absolute_price_per_sqrt_year"
    )


def test_comparison_writer_creates_csv_and_summary(tmp_path):
    frame, params = _frame()
    cfg = {
        "pricing_model": "black76_baw",
        "compare_models": ["crr_binomial"],
        **params,
    }
    result = model_comparison.write_model_comparison(frame, cfg, tmp_path)

    assert result["rows"] == 1
    assert (tmp_path / "tables" / "model_comparison.csv").exists()
    assert (tmp_path / "tables" / "model_comparison_summary.json").exists()
