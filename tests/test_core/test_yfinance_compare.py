"""Pipeline vs direct yfinance comparison tests."""

import pandas as pd

from core.yfinance_compare import (
    align_pipeline_direct,
    cross_validation_comparison,
    data_comparison,
    metric_comparison,
    prepare_direct_frame,
)


def _sample_pipeline_frame():
    dates = pd.date_range("2024-01-01", periods=30, freq="B", tz="America/New_York")
    returns = pd.Series([0.001, -0.002, 0.003, -0.001, 0.002] * 6)
    price = 100 * (1 + returns).cumprod()
    return pd.DataFrame(
        {
            "as_of_date": dates,
            "market_date": dates.date,
            "symbol": "AAPL",
            "raw_close": price / 0.95,
            "adj_factor": 0.95,
            "price_std": price,
            "return_std": price.pct_change(),
            "vol_std": returns.rolling(5, min_periods=5).std(),
            "volume": 1000000,
        }
    )


def test_prepare_direct_frame_builds_core_columns():
    raw = pd.DataFrame(
        {
            "as_of_date": pd.date_range("2024-01-01", periods=6, freq="B", tz="America/New_York"),
            "market_date": pd.date_range("2024-01-01", periods=6, freq="B").date,
            "symbol": "AAPL",
            "raw_close": [100, 101, 102, 103, 104, 105],
            "direct_adj_close": [95, 96, 97, 98, 99, 100],
            "adj_factor": [0.95] * 6,
            "volume": [1000000] * 6,
        }
    )

    df, cfg = prepare_direct_frame(raw, {"regime_axes": ["vol_regime"]})

    assert {"price_std", "return_std", "vol_std", "volume_std"}.issubset(df.columns)
    assert cfg["return_col"] == "return_std"
    assert df["price_std"].iloc[-1] == 100


def test_data_and_metric_comparison_detect_return_drift():
    pipeline = _sample_pipeline_frame()
    direct = pipeline.copy()
    direct.rename(columns={"price_std": "direct_adj_close"}, inplace=True)
    direct["price_std"] = direct["direct_adj_close"]
    direct.loc[10, "return_std"] = direct.loc[10, "return_std"] + 0.01

    aligned = align_pipeline_direct(pipeline, direct)
    data = data_comparison(aligned).set_index("metric")
    metric = metric_comparison(aligned).set_index("metric")

    assert data.loc["matched_rows", "value"] == 30
    assert data.loc["max_abs_return_diff_bps", "value"] > 90
    assert metric.loc["total_return", "diff"] != 0


def test_cross_validation_comparison_merges_fold_outputs():
    pipeline_fold = pd.DataFrame({"fold": [0], "total_return": [0.1], "sharpe": [1.0], "max_dd": [-0.1], "hit_rate": [0.6]})
    direct_fold = pd.DataFrame({"fold": [0], "total_return": [0.08], "sharpe": [0.8], "max_dd": [-0.12], "hit_rate": [0.55]})
    pipeline_div = pd.DataFrame({"fold": [0], "pass": [True], "conc": [0.6], "kl": [0.1], "js": [0.05]})
    direct_div = pd.DataFrame({"fold": [0], "pass": [False], "conc": [0.9], "kl": [0.7], "js": [0.4]})

    out = cross_validation_comparison(pipeline_fold, pipeline_div, direct_fold, direct_div)

    assert abs(out.loc[0, "total_return_diff"] - 0.02) < 1e-12
    assert out.loc[0, "pipeline_pass"]
    assert not out.loc[0, "direct_pass"]
