"""Grain separation invariants (issue 012)."""

import numpy as np
import pandas as pd
import pytest

from core import grain as g
from core.causal import causal_vol, to_causal_series


def _option_chain():
    """Two dates, three strikes × 2 rights = 6 option rows per date (mixed grain)."""
    rows = []
    for as_of, fut in [("2024-01-02", 70.0), ("2024-01-03", 71.0)]:
        for strike in (65, 70, 75):
            for right in ("C", "P"):
                rows.append({
                    "as_of_date": pd.Timestamp(as_of),
                    "strike": float(strike),
                    "right": right,
                    "iv": 0.30 + strike / 1000.0,
                    "underlying_price": fut,
                })
    return pd.DataFrame(rows)


def test_market_price_rows_and_option_contract_rows_are_separate_grains():
    chain = _option_chain()
    assert g.infer_grain(chain) == g.GRAIN_MIXED
    assert g.rows_per_date(chain) == 6

    date_level = g.to_date_grain(chain, ["underlying_price"])
    assert g.infer_grain(date_level) == g.GRAIN_DATE
    assert len(date_level) == 2  # one row per decision date


def test_rolling_operation_rejects_mixed_grain_option_chain():
    chain = _option_chain()
    # Build a date-indexed series WITH duplicate dates (mixed grain) and roll it.
    series = pd.Series(chain["iv"].to_numpy(), index=pd.DatetimeIndex(chain["as_of_date"]))
    with pytest.raises(ValueError, match="duplicate dates"):
        causal_vol(series, window=2)


def test_require_date_grain_raises_on_mixed():
    with pytest.raises(g.MixedGrainError, match="date-grain input"):
        g.require_date_grain(_option_chain(), "vol_regime")
    # Reduced frame passes.
    reduced = g.to_date_grain(_option_chain(), ["iv"])
    g.require_date_grain(reduced, "vol_regime")  # no raise


def test_same_date_shuffle_does_not_change_date_level_features():
    chain = _option_chain()
    shuffled = chain.sample(frac=1.0, random_state=11).reset_index(drop=True)

    a = g.to_date_grain(chain, ["iv", "underlying_price"])
    b = g.to_date_grain(shuffled, ["iv", "underlying_price"])
    pd.testing.assert_frame_equal(a, b)

    # Same through the causal reduction used by regime code.
    sa = to_causal_series(chain, "iv")
    sb = to_causal_series(shuffled, "iv")
    pd.testing.assert_series_equal(sa, sb)


def test_future_truncation_does_not_change_past_features():
    chain = _option_chain()
    past_only = chain[chain["as_of_date"] <= pd.Timestamp("2024-01-02")]

    full = g.to_date_grain(chain, ["iv"])
    truncated = g.to_date_grain(past_only, ["iv"])

    # The 2024-01-02 row must be identical with or without the future date present.
    full_row = full[full["as_of_date"] == pd.Timestamp("2024-01-02")].reset_index(drop=True)
    trunc_row = truncated[truncated["as_of_date"] == pd.Timestamp("2024-01-02")].reset_index(drop=True)
    pd.testing.assert_frame_equal(full_row, trunc_row)


def test_vrp_skew_term_structure_declare_selection_rules():
    for feat in ("vrp", "skew_25d", "term_structure"):
        decl = g.declare_feature_grain(feat, g.GRAIN_DATE, selection_rule="atm")
        assert decl["selection_rule"] == "atm"
        assert decl["grain"] == "date"

    # A date-grain feature without a selection rule is rejected — it would hide an
    # order-dependent pick of which contract the date-level value came from.
    with pytest.raises(ValueError, match="selection_rule"):
        g.declare_feature_grain("vrp", g.GRAIN_DATE)
