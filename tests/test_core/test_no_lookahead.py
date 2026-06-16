"""P3 tests — leakage guards L3 (perturbation) + L4 (static lint)."""

import numpy as np
import pandas as pd
import pytest

from core.leakage import assert_no_lookahead, scan_lookahead_patterns
from core.regime import assign_regime_labels


def _date_grain_df(n=60, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "as_of_date": dates,
        "product_id": 254,
        "return_std": rng.normal(0, 0.01, n),
    })


# ── L3: the real guard ────────────────────────────────────────────────────────

def test_regime_labeler_has_no_lookahead():
    """assign_regime_labels (post-C2-fix, date-grain causal) must not leak the future."""
    df = _date_grain_df()
    cfg = {"regime_axes": ["vol_regime"], "vol_window": 10, "return_col": "return_std"}
    assert_no_lookahead(lambda d: assign_regime_labels(d, cfg).to_frame("regime"), df)


def test_perturbation_test_catches_a_real_leak():
    """Sanity: a deliberately non-causal feature (full-sample z-score) must FAIL."""
    df = _date_grain_df()

    def leaky(d):
        x = d["return_std"]
        z = (x - x.mean()) / x.std()  # full-sample → uses the future
        return z.to_frame("z")

    with pytest.raises(AssertionError):
        assert_no_lookahead(leaky, df)


def test_causal_feature_passes():
    df = _date_grain_df()

    def causal(d):
        x = d["return_std"]
        return x.expanding(min_periods=5).mean().to_frame("exp_mean")

    assert_no_lookahead(causal, df)  # no raise


# ── L4: static lint ───────────────────────────────────────────────────────────

def test_no_banned_lookahead_patterns_in_source():
    hits = scan_lookahead_patterns(["core/*.py", "adapters/*.py"])
    assert hits == [], f"look-ahead patterns found: {hits}"
