"""v1.3 tests: rule-based regime labels."""

import numpy as np
import pandas as pd

from core.regime import assign_regime_labels, compute_transition_matrix


def test_assign_regime_labels_combines_configured_axes():
    df = pd.DataFrame({
        "return_std": np.r_[np.zeros(25), np.linspace(-0.02, 0.02, 25)],
        "vrp": [0.01, -0.01] * 25,
        "term_structure_slope": [1.0, -1.0] * 25,
        "skew_25d": [-0.10, 0.10] * 25,
        "event_week": [True, False] * 25,
    })
    cfg = {
        "regime_axes": ["vol_regime", "vrp_sign", "term_structure", "skew_direction"],
        "event_regimes": ["event_week"],
        "return_col": "return_std",
        "vol_window": 5,
    }

    labels = assign_regime_labels(df, cfg)

    assert labels.index.equals(df.index)
    assert labels.astype(str).str.contains("vrp_").any()
    assert labels.astype(str).str.contains("contango|backwardation", regex=True).any()
    assert labels.astype(str).str.contains("event_week|non_event_week", regex=True).any()


def test_transition_matrix_rows_sum_to_one_for_seen_states():
    labels = pd.Series(["a", "a", "b", "a", "b", "b"])
    matrix = compute_transition_matrix(labels)

    assert set(matrix.index) == {"a", "b"}
    row_sums = matrix.sum(axis=1)
    assert (row_sums[row_sums > 0] == 1.0).all()


def test_regime_labels_are_date_grain_for_chain_rows():
    df = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-01"] * 3 + ["2024-01-02"] * 3),
        "return_std": [0.01, 0.01, 0.01, -0.02, -0.02, -0.02],
        "vrp": [0.10, -0.05, 0.20, -0.30, 0.01, -0.20],
        "term_structure_slope": [1.0, 2.0, 3.0, -1.0, -2.0, -3.0],
    })

    labels = assign_regime_labels(df, {
        "regime_axes": ["vrp_sign", "term_structure"],
        "date_col": "as_of_date",
    })

    per_date_counts = labels.groupby(df["as_of_date"]).nunique()
    assert per_date_counts.tolist() == [1, 1]


def test_regime_labels_do_not_depend_on_same_date_row_order():
    df = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-01"] * 4 + ["2024-01-02"] * 4),
        "return_std": [0.01] * 4 + [-0.02] * 4,
        "vrp": [0.10, -0.05, 0.20, 0.30, -0.30, 0.01, -0.20, -0.40],
        "term_structure_slope": [1.0, 2.0, 3.0, 4.0, -1.0, -2.0, -3.0, -4.0],
    })
    shuffled = df.sample(frac=1.0, random_state=7).reset_index(drop=True)
    cfg = {"regime_axes": ["vrp_sign", "term_structure"], "date_col": "as_of_date"}

    original = assign_regime_labels(df, cfg).groupby(df["as_of_date"]).first()
    after_shuffle = assign_regime_labels(shuffled, cfg).groupby(shuffled["as_of_date"]).first()

    pd.testing.assert_series_equal(original, after_shuffle)


def test_regime_labels_past_unchanged_when_future_truncated():
    df = pd.DataFrame({
        "as_of_date": pd.date_range("2024-01-01", periods=40, freq="D"),
        "return_std": np.linspace(-0.02, 0.03, 40),
    })
    cfg = {
        "regime_axes": ["vol_regime"],
        "date_col": "as_of_date",
        "return_col": "return_std",
        "vol_window": 5,
        "vol_min_periods": 3,
        "vol_rank_min_periods": 3,
    }
    cut = 20

    full = assign_regime_labels(df, cfg).iloc[: cut + 1]
    truncated = assign_regime_labels(df.iloc[: cut + 1], cfg)

    pd.testing.assert_series_equal(full, truncated)
