"""Regression tests for equity data-prep invariants."""

import numpy as np
import pandas as pd


def test_equity_adapter_preserves_raw_return_and_flags_clip():
    """Return outlier tagging is a separate stage, not inside prepare().

    prepare() must produce return_raw; apply_return_clip() must flag the spike.
    The raw and canonical returns must survive unchanged by default.
    """
    from adapters.equity_adapter import EquityAdapter

    raw = pd.DataFrame({
        "as_of_date": pd.date_range("2024-01-01", periods=30, freq="B"),
        "symbol": "TEST",
        "raw_close": [100.0 + i * 0.1 for i in range(29)] + [200.0],
        "adj_factor": 1.0,
        "volume": 1_000_000,
        "is_delisted": False,
    })
    adapter = EquityAdapter({
        "vol_window": 5,
        "outlier_k": 3.0,
        "outlier_min_periods": 10,
    })
    df, _ = adapter.prepare(raw)

    assert "return_raw" in df.columns
    # _return_outlier_flag not yet present — clipping is a separate stage
    assert "_return_outlier_flag" not in df.columns

    df = adapter.apply_return_clip(df)

    assert "_return_outlier_flag" in df.columns
    assert df.loc[df.index[-1], "_return_outlier_flag"]
    assert df.loc[df.index[-1], "return_raw"] == df.loc[df.index[-1], "return_std"]
    assert df.loc[df.index[-1], "_return_outlier_policy"] == "tag_only"
    assert "return_winsorized" not in df.columns


def test_dividend_is_folded_into_return_pit_total_return():
    """Ex-dividend price drop is cancelled by a PIT total-return add-back.

    On the ex-date the close drops by the dividend (mechanical, not a real loss).
    return_raw (total) must add the dividend back to ~0; return_price (ex-div) keeps
    the drop; no price_adjustment_warning is raised because dividends are now handled.
    """
    from adapters.equity_adapter import EquityAdapter

    # Flat at 100, then a $1.00 dividend on day 5 with the close dropping to 99.
    closes = [100.0] * 5 + [99.0] + [99.0] * 4
    divs = [0.0] * 5 + [1.0] + [0.0] * 4
    raw = pd.DataFrame({
        "as_of_date": pd.date_range("2024-01-01", periods=len(closes), freq="B"),
        "symbol": "DIV",
        "raw_close": closes,
        "dividend": divs,
        "adj_factor": [0.99] * len(closes),   # provider retro dividend factor (unused now)
        "adj_factor_is_pit": False,
        "volume": 1_000_000,
        "is_delisted": False,
    })
    df, _ = EquityAdapter({"vol_window": 5}).prepare(raw)
    exdiv = df.index[5]

    assert abs(df.loc[exdiv, "return_raw"]) < 1e-9              # total return ≈ 0 (drop cancelled)
    assert abs(df.loc[exdiv, "return_price"] - (-0.01)) < 1e-9  # ex-div price return = -1%
    assert df.loc[exdiv, "return_std"] == df.loc[exdiv, "return_raw"]
    assert not bool(df["price_adjustment_warning"].any())      # dividends handled → no warning
    assert bool(df["dividend_pit_applied"].iloc[0])
    # price level stays the actually-traded (split-adjusted) close, NOT dividend-adjusted
    assert df.loc[exdiv, "price_std"] == 99.0


def test_equity_adapter_can_create_derived_winsorized_return():
    """Opt-in winsorization creates a derived series without mutating return_std."""
    from adapters.equity_adapter import EquityAdapter

    raw = pd.DataFrame({
        "as_of_date": pd.date_range("2024-01-01", periods=30, freq="B"),
        "symbol": "TEST",
        "raw_close": [100.0 + i * 0.1 for i in range(29)] + [200.0],
        "adj_factor": 1.0,
        "volume": 1_000_000,
        "is_delisted": False,
    })
    adapter = EquityAdapter({
        "vol_window": 5,
        "outlier_k": 3.0,
        "outlier_min_periods": 10,
        "outlier_policy": {
            "return_action": "derive_winsorized",
            "derived_return_col": "return_winsorized",
        },
    })
    df, cfg = adapter.prepare(raw)
    df = adapter.apply_return_clip(df)

    last = df.index[-1]
    assert cfg["return_action"] == "derive_winsorized"
    assert df.loc[last, "_return_outlier_flag"]
    assert df.loc[last, "return_raw"] == df.loc[last, "return_std"]
    assert df.loc[last, "return_winsorized"] < df.loc[last, "return_std"]


def test_pit_mad_clip_rolling_window_passes_earnings_gap_return():
    """Rolling window (63 bar) passes earnings-day spikes that expanding MAD tags.

    Scenario: 100 bars low-vol (1% std), then 63 bars high-vol (4% std, current regime),
    then +15% earnings gap. Expanding MAD anchors to the calm prior period (threshold
    ~11%) and would tag it. Rolling(63) sees only the high-vol regime (threshold ~20%)
    and passes.
    """
    from adapters.equity_adapter import EquityAdapter

    rng = np.random.default_rng(0)
    phase1 = rng.normal(0.001, 0.01, 100).tolist()   # 100 bars, 1% daily std
    phase2 = rng.normal(0.001, 0.04, 63).tolist()    # 63 bars, 4% daily std — current regime
    earnings = [0.15]                                  # genuine +15% earnings gap

    closes = [100.0]
    for r in phase1 + phase2 + earnings:
        closes.append(closes[-1] * (1 + r))

    df_raw = pd.DataFrame({
        "as_of_date": pd.date_range("2020-01-02", periods=len(closes), freq="B"),
        "symbol": "TSLA",
        "raw_close": closes,
        "volume": 5_000_000,
        "is_delisted": False,
    })

    adapter = EquityAdapter({"vol_window": 21, "outlier_k": 5.0, "outlier_min_periods": 20})
    df, _ = adapter.prepare(df_raw)
    df = adapter.apply_return_clip(df)

    last = df.index[-1]
    assert not df.loc[last, "_return_outlier_flag"], (
        f"Earnings +{df.loc[last, 'return_raw']:.1%} wrongly tagged with "
        f"{df.loc[last, 'return_std']:.1%} — rolling(63) should track current regime"
    )


def test_equity_adj_factor_not_treated_as_pit_truth_by_default():
    """Retro-adjusted provider factors are preserved but not used as PIT price truth."""
    from adapters.equity_adapter import EquityAdapter

    raw = pd.DataFrame({
        "as_of_date": pd.date_range("2024-01-01", periods=3, freq="B"),
        "symbol": "TEST",
        "raw_close": [100.0, 102.0, 104.0],
        "adj_factor": [0.5, 0.5, 0.5],
        "adj_factor_source": "yfinance_adj_close_retroactive",
        "volume": 1_000_000,
        "is_delisted": False,
    })

    df, _ = EquityAdapter({"vol_window": 2}).prepare(raw)

    assert df["price_std"].tolist() == [100.0, 102.0, 104.0]
    assert "price_adjustment_warning" in df.columns
    assert df["price_adjustment_warning"].all()


def _make_equity_df(symbol: str, stable_close: float, n_stable: int,
                    spike_close: float, start: str = "2020-01-02") -> pd.DataFrame:
    """Build test DataFrame: n_stable calm days then one spike day."""
    closes = [stable_close + i * 0.01 for i in range(n_stable)] + [spike_close]
    dates = pd.date_range(start, periods=len(closes), freq="B")
    return pd.DataFrame({
        "as_of_date": dates, "symbol": symbol,
        "raw_close": closes, "volume": 1_000_000, "is_delisted": False,
    })


def test_validate_clips_unclips_genuine_event():
    """validate_clips must confirm rows where both providers agree on a large return.

    Scenario: COVID crash. Both yfinance and Stooq show KO -9.7%.
    pit_mad tags it (low historical vol -> tight threshold).
    validate_clips sees agreement -> genuine event, not bad tick.
    """
    from adapters.equity_adapter import EquityAdapter

    # 25 stable days warmup (≈1% drift) then crash: 55→49.8 ≈ -9.5%
    df_primary = _make_equity_df("KO", stable_close=55.0, n_stable=25, spike_close=49.8)
    adapter = EquityAdapter({"vol_window": 5, "outlier_k": 3.0, "outlier_min_periods": 10})
    df, _ = adapter.prepare(df_primary)
    df = adapter.apply_return_clip(df)
    assert df.iloc[-1]["_return_outlier_flag"], "spike must be flagged before validation"

    # Stooq also shows the same crash — same closes
    val_df = df_primary.rename(columns={}).copy()
    val_df["provider"] = "stooq"
    val_df = val_df[["as_of_date", "symbol", "raw_close", "provider"]]

    df_val = adapter.validate_clips(df, val_df, agree_tol=0.02)
    last = df_val.index[-1]
    assert df_val.loc[last, "_return_outlier_flag"], "genuine crash should remain tagged as unusual"
    assert df_val.loc[last, "return_std"] == df_val.loc[last, "return_raw"], \
        "return_std must remain canonical/raw"
    assert df_val.loc[last, "_return_outlier_reason"] == "cross_provider_validated"
    assert df_val.loc[last, "_return_validation_status"] == "provider_confirmed"


def test_validate_clips_keeps_clip_on_provider_conflict():
    """When providers disagree on a flagged return, keep it tagged provider_conflict.

    Scenario: bad tick in yfinance — 100→180 (+79%). Stooq shows calm 100→100.4.
    Providers disagree -> needs review, label provider_conflict.
    """
    from adapters.equity_adapter import EquityAdapter

    df_primary = _make_equity_df("TEST", stable_close=100.0, n_stable=25, spike_close=180.0)
    adapter = EquityAdapter({"vol_window": 5, "outlier_k": 3.0, "outlier_min_periods": 10})
    df, _ = adapter.prepare(df_primary)
    df = adapter.apply_return_clip(df)
    assert df.iloc[-1]["_return_outlier_flag"], "bad tick must be flagged"

    # Stooq shows a calm day — providers disagree
    val_df = _make_equity_df("TEST", stable_close=100.0, n_stable=25, spike_close=100.4)
    val_df = val_df[["as_of_date", "symbol", "raw_close"]].assign(provider="stooq")

    df_val = adapter.validate_clips(df, val_df, agree_tol=0.02)
    last = df_val.index[-1]
    assert df_val.loc[last, "_return_outlier_flag"], "conflict -> tag must be kept"
    assert "provider_conflict" in str(df_val.loc[last, "_return_outlier_reason"])
    assert df_val.loc[last, "_return_validation_status"] == "needs_review"


def test_validate_clips_is_conservative_when_second_provider_missing():
    """No second-provider data -> keep tag unchanged."""
    from adapters.equity_adapter import EquityAdapter

    df_primary = _make_equity_df("TEST", stable_close=100.0, n_stable=25, spike_close=180.0)
    adapter = EquityAdapter({"vol_window": 5, "outlier_k": 3.0, "outlier_min_periods": 10})
    df, _ = adapter.prepare(df_primary)
    df = adapter.apply_return_clip(df)
    assert df.iloc[-1]["_return_outlier_flag"]

    df_val = adapter.validate_clips(df, pd.DataFrame(), agree_tol=0.02)
    assert df_val.iloc[-1]["_return_outlier_flag"], "no second provider -> keep tag"


def test_equity_nested_config_reaches_core_cfg():
    """Nested validation/cv/performance/stability config should be available flat."""
    from adapters.equity_adapter import EquityAdapter

    raw = pd.DataFrame({
        "as_of_date": pd.date_range("2024-01-01", periods=10, freq="B"),
        "symbol": "TEST",
        "raw_close": np.linspace(100, 101, 10),
        "volume": 1_000_000,
        "is_delisted": False,
    })
    _, cfg = EquityAdapter({
        "validation": {"min_volume": 50_000, "outlier_k": 7.0},
        "cv": {"n_folds": 3, "purge_bars": 2},
        "performance": {"n_trials": 17},
        "stability": {"psi_threshold": 0.42},
    }).prepare(raw)

    assert cfg["min_volume"] == 50_000
    assert cfg["outlier_k"] == 7.0
    assert cfg["n_folds"] == 3
    assert cfg["purge_bars"] == 2
    assert cfg["n_trials"] == 17
    assert cfg["psi_threshold"] == 0.42
