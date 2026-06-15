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
