"""Bollinger Bands experiment: Janus pipeline data vs direct provider data.

Run:
    python examples/bollinger_pipeline_vs_raw_experiment.py

Default design:
    - Instrument: AAPL
    - Window: 2020-01-01 <= date < 2025-01-01 (five calendar years)
    - Strategy: long-flat Bollinger mean reversion, 20-day SMA, +/- 2 stdev
    - Entry: close crosses below the lower band; position begins on next bar
    - Exit: close crosses back above the middle band, or max holding period
    - Evaluation: common trading dates, transaction costs, purged walk-forward folds
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import metrics, overfitting, regime, splitter  # noqa: E402
from core.config import normalize_config  # noqa: E402
from core.yfinance_compare import fetch_direct_yfinance  # noqa: E402
from run_pipeline import apply_runtime_overrides, load_config, run_pipeline  # noqa: E402


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if pd.isna(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, set):
        return sorted(str(v) for v in value)
    if pd.isna(value):
        return None
    return value


def _to_plain(value: Any) -> Any:
    try:
        return _json_default(value)
    except TypeError:
        return str(value)


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, np.integer, np.floating)) and not pd.isna(value):
        value = float(value)
        return value if math.isfinite(value) else None
    return None


def _market_date(values: pd.Series) -> pd.Series:
    dt = pd.to_datetime(values, errors="coerce", utc=True)
    return dt.dt.tz_convert("America/New_York").dt.date


def _write_json(path: Path, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=_json_default)


def _serialize_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == "object":
            out[col] = out[col].map(_to_plain)
    return out


def _md_table(df: pd.DataFrame, columns: list[str], max_rows: int = 30) -> str:
    if df.empty:
        return "_No rows._"
    view = df.loc[:, columns].head(max_rows).copy()
    for col in view.columns:
        view[col] = view[col].map(_format_md_value)
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    rows = ["| " + " | ".join(str(row[col]) for col in columns) + " |" for _, row in view.iterrows()]
    return "\n".join([header, sep, *rows])


def _format_md_value(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (float, np.floating)):
        if abs(float(value)) >= 10:
            return f"{float(value):.3f}"
        return f"{float(value):.6f}"
    return str(value)


def _prepare_pipeline_frame(summary: dict[str, Any]) -> pd.DataFrame:
    prepared_path = Path(summary["artifacts"]["prepared_csv"])
    df = pd.read_csv(prepared_path)
    df["as_of_date"] = pd.to_datetime(df["as_of_date"], errors="coerce", utc=True)
    df["market_date"] = _market_date(df["as_of_date"])
    out = pd.DataFrame(
        {
            "as_of_date": df["as_of_date"],
            "market_date": df["market_date"],
            "price_signal": pd.to_numeric(df["price_std"], errors="coerce"),
            "asset_return": pd.to_numeric(df["return_std"], errors="coerce"),
            "volume": pd.to_numeric(df.get("volume_std", df.get("volume")), errors="coerce"),
        }
    )
    out["source"] = "janus_pipeline"
    out["price_input"] = "prepared.price_std"
    out["return_input"] = "prepared.return_std"
    return out.sort_values("market_date").drop_duplicates("market_date").reset_index(drop=True)


def _prepare_direct_frame(symbol: str, start: str, end: str) -> pd.DataFrame:
    raw = fetch_direct_yfinance(symbol, start, end)
    if raw.empty:
        raise ValueError(f"Direct provider returned no rows for {symbol} {start}..{end}")

    raw = raw.sort_values(["symbol", "as_of_date"]).reset_index(drop=True)
    price = pd.to_numeric(raw["direct_adj_close"], errors="coerce")
    out = pd.DataFrame(
        {
            "as_of_date": pd.to_datetime(raw["as_of_date"], errors="coerce"),
            "market_date": raw["market_date"],
            "price_signal": price,
            "asset_return": price.pct_change(),
            "volume": pd.to_numeric(raw["volume"], errors="coerce"),
        }
    )
    out["source"] = "direct_provider"
    out["price_input"] = "yfinance.Adj Close"
    out["return_input"] = "pct_change(yfinance.Adj Close)"
    return out.sort_values("market_date").drop_duplicates("market_date").reset_index(drop=True)


def _run_janus_pipeline(
    instrument: str,
    start: str,
    end: str,
    run_id: str,
    n_folds: int,
    purge_bars: int,
    embargo_bars: int,
) -> dict[str, Any]:
    cfg = load_config(instrument)
    cfg = apply_runtime_overrides(
        cfg,
        metrics_mode="diagnostic",
        n_folds=n_folds,
        embargo_bars=embargo_bars,
        progress="none",
    )
    cfg["require_fixed_data_version"] = False
    cfg["cross_validate"] = {}
    cfg["purge_bars"] = int(purge_bars)
    cfg.setdefault("cv", {})["purge_bars"] = int(purge_bars)
    cfg["event_embargo_bars"] = int(embargo_bars)
    cfg.setdefault("cv", {})["event_embargo_bars"] = int(embargo_bars)

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        summary = run_pipeline(cfg, start, end, run_id)
    summary["pipeline_stdout"] = buffer.getvalue()
    return summary


def _filter_common_calendar(frames: list[pd.DataFrame]) -> list[pd.DataFrame]:
    common = set(frames[0]["market_date"].dropna())
    for frame in frames[1:]:
        common &= set(frame["market_date"].dropna())
    if not common:
        raise ValueError("No common trading dates between sources.")
    return [
        frame[frame["market_date"].isin(common)].sort_values("market_date").reset_index(drop=True)
        for frame in frames
    ]


def apply_bollinger_strategy(
    frame: pd.DataFrame,
    *,
    window: int,
    num_std: float,
    max_hold: int,
    cost_bps: float,
) -> pd.DataFrame:
    """Apply a one-bar-lagged long-flat Bollinger mean-reversion rule."""
    df = frame.sort_values("market_date").reset_index(drop=True).copy()
    price = pd.to_numeric(df["price_signal"], errors="coerce")
    middle = price.rolling(window, min_periods=window).mean()
    stdev = price.rolling(window, min_periods=window).std(ddof=0)
    upper = middle + num_std * stdev
    lower = middle - num_std * stdev

    entry_signal = (price.shift(1) >= lower.shift(1)) & (price < lower)
    exit_signal = (price.shift(1) <= middle.shift(1)) & (price > middle)

    desired_after_close: list[int] = []
    state = 0
    holding_days = 0
    exit_reasons: list[str] = []
    for i in range(len(df)):
        reason = ""
        if state == 0:
            if bool(entry_signal.iloc[i]):
                state = 1
                holding_days = 0
                reason = "enter_lower_band_cross"
        else:
            holding_days += 1
            if bool(exit_signal.iloc[i]):
                state = 0
                holding_days = 0
                reason = "exit_middle_band_cross"
            elif holding_days >= max_hold:
                state = 0
                holding_days = 0
                reason = "exit_max_hold"
        desired_after_close.append(state)
        exit_reasons.append(reason)

    desired = pd.Series(desired_after_close, index=df.index, dtype=float)
    position = desired.shift(1).fillna(0.0)
    trades = position.diff().abs().fillna(position.abs())
    cost = trades * (float(cost_bps) / 10000.0)
    gross = position * pd.to_numeric(df["asset_return"], errors="coerce")

    df["bb_middle"] = middle
    df["bb_upper"] = upper
    df["bb_lower"] = lower
    df["entry_signal"] = entry_signal.fillna(False)
    df["exit_signal"] = exit_signal.fillna(False)
    df["signal_action"] = exit_reasons
    df["position"] = position
    df["turnover"] = trades
    df["tx_cost"] = cost
    df["strategy_return_gross"] = gross
    df["strategy_return"] = gross - cost
    df["equity"] = (1 + df["strategy_return"].fillna(0.0)).cumprod()
    df["label_end_date"] = pd.to_datetime(df["as_of_date"], errors="coerce") + pd.offsets.BDay(max_hold)
    return df


def _performance_row(source: str, df: pd.DataFrame, return_col: str, prefix: str = "") -> dict[str, Any]:
    r = pd.to_numeric(df[return_col], errors="coerce").dropna()
    equity = (1 + r).cumprod()
    row: dict[str, Any] = {
        "source": source,
        "stream": prefix or return_col,
        "observations": int(len(r)),
        "start": str(df["market_date"].min()),
        "end": str(df["market_date"].max()),
    }
    if r.empty:
        return row

    row.update(metrics.return_metrics(r))
    row.update(metrics.risk_adjusted(r))
    row.update(metrics.drawdown_metrics(equity))
    tail = metrics.tail_metrics(r)
    hit = metrics.hit_metrics(r)
    row["var_95"] = tail.get("var")
    row["cvar_95"] = tail.get("cvar")
    row["worst_day"] = tail.get("worst_day")
    row["worst_week"] = tail.get("worst_week")
    row["hit_rate"] = hit.get("win_rate")
    row["profit_factor"] = hit.get("profit_factor")
    row["payoff_ratio"] = hit.get("payoff_ratio")
    row["longest_losing_streak"] = hit.get("longest_losing_streak")
    if "position" in df.columns and return_col.startswith("strategy_return"):
        position = pd.to_numeric(df["position"], errors="coerce").fillna(0.0)
        invested = position > 0
        invested_returns = pd.to_numeric(df.loc[invested, return_col], errors="coerce").dropna()
        row["exposure"] = float(position.mean())
        row["invested_observations"] = int(invested.sum())
        row["invested_hit_rate"] = float((invested_returns > 0).mean()) if len(invested_returns) else None
        row["position_changes"] = int((pd.to_numeric(df["turnover"], errors="coerce").fillna(0.0) > 0).sum())
        row["round_trips"] = row["position_changes"] / 2.0
        row["one_way_turnover"] = float(pd.to_numeric(df["turnover"], errors="coerce").fillna(0.0).sum())
        row["total_tx_cost"] = float(pd.to_numeric(df["tx_cost"], errors="coerce").fillna(0.0).sum())
    return row


def aggregate_metrics(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for source, df in frames.items():
        rows.append(_performance_row(source, df, "strategy_return", "bollinger_net"))
        rows.append(_performance_row(source, df, "strategy_return_gross", "bollinger_gross"))
        rows.append(_performance_row(source, df, "asset_return", "buy_hold_asset"))
    return pd.DataFrame(rows)


def _fold_rows(source: str, df: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    eval_df = df.copy().reset_index(drop=True)
    folds = splitter.walk_forward_split(eval_df, cfg)
    folds = splitter.purge_embargo(folds, eval_df, cfg)
    labels = regime.assign_regime_labels(eval_df, cfg)
    diversity = splitter.regime_diversity_gate(folds, labels, cfg)

    div_by_fold = diversity.set_index("fold").to_dict("index") if "fold" in diversity else {}
    rows: list[dict[str, Any]] = []
    for fold_id, (train_idx, val_idx) in enumerate(folds):
        val = eval_df.iloc[val_idx].copy()
        r = pd.to_numeric(val["strategy_return"], errors="coerce").dropna()
        row: dict[str, Any] = {
            "source": source,
            "fold": int(fold_id),
            "train_obs": int(len(train_idx)),
            "val_obs": int(len(val_idx)),
            "train_start": str(eval_df["market_date"].iloc[train_idx].min()) if len(train_idx) else None,
            "train_end": str(eval_df["market_date"].iloc[train_idx].max()) if len(train_idx) else None,
            "val_start": str(val["market_date"].min()) if len(val) else None,
            "val_end": str(val["market_date"].max()) if len(val) else None,
            "fold_exposure": float(val["position"].mean()) if "position" in val else None,
            "fold_trades": int((val.get("turnover", pd.Series(dtype=float)).fillna(0.0) > 0).sum()),
        }
        if len(r) >= 2:
            equity = (1 + r).cumprod()
            row["total_return"] = float((1 + r).prod() - 1)
            row["sharpe"] = metrics.risk_adjusted(r).get("sharpe")
            row["sortino"] = metrics.risk_adjusted(r).get("sortino")
            row["max_dd"] = metrics.drawdown_metrics(equity).get("max_dd")
            row["cvar_95"] = metrics.tail_metrics(r).get("cvar")
            row["hit_rate"] = float((r > 0).mean())
            row["worst_day"] = float(r.min())
        row.update({f"diversity_{k}": _to_plain(v) for k, v in div_by_fold.get(fold_id, {}).items()})
        rows.append(row)

    diversity = diversity.copy()
    diversity.insert(0, "source", source)
    return pd.DataFrame(rows), diversity


def cross_validation(frames: dict[str, pd.DataFrame], cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    fold_frames: list[pd.DataFrame] = []
    diversity_frames: list[pd.DataFrame] = []
    for source, df in frames.items():
        fold, diversity = _fold_rows(source, df, cfg)
        fold_frames.append(fold)
        diversity_frames.append(diversity)
    return pd.concat(fold_frames, ignore_index=True), pd.concat(diversity_frames, ignore_index=True)


def paired_difference(frames: dict[str, pd.DataFrame], *, bootstrap_samples: int = 2000, block: int = 20) -> pd.DataFrame:
    p = frames["janus_pipeline"][["market_date", "strategy_return"]].rename(columns={"strategy_return": "pipeline_return"})
    d = frames["direct_provider"][["market_date", "strategy_return"]].rename(columns={"strategy_return": "direct_return"})
    aligned = p.merge(d, on="market_date", how="inner").dropna()
    aligned["daily_delta"] = aligned["pipeline_return"] - aligned["direct_return"]
    x = aligned["daily_delta"].to_numpy(dtype=float)
    n = len(x)
    row: dict[str, Any] = {
        "observations": n,
        "mean_daily_delta": float(np.mean(x)) if n else None,
        "annualized_mean_delta": float(np.mean(x) * 252.0) if n else None,
        "pipeline_total_return": float((1 + aligned["pipeline_return"]).prod() - 1) if n else None,
        "direct_total_return": float((1 + aligned["direct_return"]).prod() - 1) if n else None,
    }
    if n:
        row["total_return_delta"] = row["pipeline_total_return"] - row["direct_total_return"]

    if n >= block and bootstrap_samples > 0:
        rng = np.random.default_rng(42)
        starts = np.arange(0, n - block + 1)
        means = np.empty(bootstrap_samples)
        for i in range(bootstrap_samples):
            sample: list[float] = []
            while len(sample) < n:
                start = int(rng.choice(starts))
                sample.extend(x[start : start + block])
            means[i] = np.mean(sample[:n])
        row["bootstrap_block"] = block
        row["bootstrap_samples"] = bootstrap_samples
        row["mean_delta_ci_2_5"] = float(np.quantile(means, 0.025))
        row["mean_delta_ci_97_5"] = float(np.quantile(means, 0.975))
        row["two_sided_p_mean_delta_le_0"] = float(2 * min((means <= 0).mean(), (means >= 0).mean()))
    return pd.DataFrame([row])


def robustness_grid(
    base_frames: dict[str, pd.DataFrame],
    *,
    windows: list[int],
    num_stds: list[float],
    cost_bps: float,
    cv_cfg: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    fold_return_matrices: dict[str, list[list[float]]] = {source: [] for source in base_frames}

    for window in windows:
        for num_std in num_stds:
            variant = f"w{window}_k{num_std:g}"
            for source, frame in base_frames.items():
                tested = apply_bollinger_strategy(
                    frame,
                    window=window,
                    num_std=num_std,
                    max_hold=window,
                    cost_bps=cost_bps,
                )
                perf = _performance_row(source, tested, "strategy_return", variant)
                perf["window"] = window
                perf["num_std"] = num_std
                rows.append(perf)

                fold, _ = _fold_rows(
                    source,
                    tested,
                    {
                        **cv_cfg,
                        "label_end_col": "label_end_date",
                        "purge_bars": window + 1,
                        "event_embargo_bars": 1,
                    },
                )
                vals = fold.sort_values("fold")["total_return"].fillna(0.0).astype(float).tolist()
                fold_return_matrices[source].append(vals)

    overfit_rows: list[dict[str, Any]] = []
    n_trials = len(windows) * len(num_stds)
    for source, matrix_rows in fold_return_matrices.items():
        max_cols = max((len(row) for row in matrix_rows), default=0)
        padded = [row + [0.0] * (max_cols - len(row)) for row in matrix_rows]
        ret_matrix = pd.DataFrame(padded)
        pbo = overfitting.prob_backtest_overfitting(ret_matrix) if max_cols >= 2 and len(padded) >= 2 else {}
        primary = next(
            row for row in rows
            if row["source"] == source and row["window"] == 20 and abs(row["num_std"] - 2.0) < 1e-12
        )
        sr = _safe_float(primary.get("sharpe")) or 0.0
        primary_returns = apply_bollinger_strategy(
            base_frames[source],
            window=20,
            num_std=2.0,
            max_hold=20,
            cost_bps=cost_bps,
        )["strategy_return"].dropna()
        dsr = overfitting.deflated_sharpe_ratio(
            sr=sr,
            n_trials=n_trials,
            T=int(len(primary_returns)),
            skew=float(primary_returns.skew()) if len(primary_returns) >= 4 else 0.0,
            kurt=float(primary_returns.kurtosis() + 3.0) if len(primary_returns) >= 4 else 3.0,
        )
        overfit_rows.append(
            {
                "source": source,
                "n_trials": n_trials,
                "primary_sharpe": sr,
                **{f"dsr_{k}": v for k, v in dsr.items()},
                **{f"pbo_{k}": v for k, v in pbo.items()},
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(overfit_rows)


def render_charts(
    tested: dict[str, pd.DataFrame],
    fold_df: pd.DataFrame,
    robustness_df: pd.DataFrame,
    out_dir: Path,
) -> dict[str, str]:
    """Write visual diagnostics for the experiment."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    charts_dir = out_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="whitegrid")

    paths: dict[str, str] = {}

    fig, ax = plt.subplots(figsize=(11, 6))
    for source, df in tested.items():
        dates = pd.to_datetime(df["market_date"])
        ax.plot(dates, df["equity"], label=source.replace("_", " "), linewidth=2)
    ax.set_title("Bollinger Strategy Equity Curve")
    ax.set_ylabel("Growth of $1")
    ax.set_xlabel("Date")
    ax.legend()
    fig.tight_layout()
    path = charts_dir / "equity_curve.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths["chart_equity_curve"] = str(path)

    fig, ax = plt.subplots(figsize=(11, 5))
    for source, df in tested.items():
        dates = pd.to_datetime(df["market_date"])
        equity = pd.to_numeric(df["equity"], errors="coerce")
        drawdown = equity / equity.cummax() - 1.0
        ax.plot(dates, drawdown, label=source.replace("_", " "), linewidth=2)
    ax.set_title("Bollinger Strategy Drawdown")
    ax.set_ylabel("Drawdown")
    ax.set_xlabel("Date")
    ax.yaxis.set_major_formatter(lambda x, _pos: f"{x:.0%}")
    ax.legend()
    fig.tight_layout()
    path = charts_dir / "drawdown.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths["chart_drawdown"] = str(path)

    if not fold_df.empty:
        fig, ax = plt.subplots(figsize=(10, 5))
        plot_df = fold_df.copy()
        plot_df["fold"] = plot_df["fold"].astype(str)
        sns.barplot(data=plot_df, x="fold", y="total_return", hue="source", ax=ax)
        ax.axhline(0.0, color="black", linewidth=1)
        ax.set_title("Out-of-Sample Total Return by Fold")
        ax.set_xlabel("Fold")
        ax.set_ylabel("Total return")
        ax.yaxis.set_major_formatter(lambda x, _pos: f"{x:.0%}")
        fig.tight_layout()
        path = charts_dir / "fold_total_return.png"
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths["chart_fold_total_return"] = str(path)

    primary = robustness_df[robustness_df["stream"].notna()].copy()
    if not primary.empty:
        sources = list(primary["source"].dropna().unique())
        fig, axes = plt.subplots(1, len(sources), figsize=(6 * len(sources), 5), squeeze=False)
        for ax, source in zip(axes[0], sources):
            view = primary[primary["source"] == source]
            heat = view.pivot_table(index="window", columns="num_std", values="sharpe", aggfunc="mean")
            sns.heatmap(heat, annot=True, fmt=".2f", cmap="RdYlGn", center=0.0, ax=ax)
            ax.set_title(f"Robustness Sharpe: {source.replace('_', ' ')}")
            ax.set_xlabel("Band stdev")
            ax.set_ylabel("Window")
        fig.tight_layout()
        path = charts_dir / "robustness_sharpe_heatmap.png"
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths["chart_robustness_sharpe"] = str(path)

    return paths


def render_experimental_setup(args: argparse.Namespace, symbol: str, summary: dict[str, Any]) -> str:
    return f"""# Experimental Setup

## Objective

Compare the same Bollinger Bands trading rule on two input pipelines:

1. **Janus pipeline data**: provider data passes through Janus ingestion, contract checks, coverage checks, equity adapter, PIT dividend return construction, validators, CDC/audit, and the exported `prepared.csv`.
2. **Direct provider data**: yfinance history is read directly and `Adj Close` is used as the naive price/return stream, without Janus validation or provenance controls.

## Dataset

- Instrument: `{symbol}`
- Requested window: `{args.start}` inclusive to `{args.end}` exclusive.
- Intended sample length: five calendar years.
- Calendar control: only dates present in both data sources are evaluated.
- Pipeline run directory: `{summary.get("output_dir")}`
- Pipeline prepared file: `{summary.get("artifacts", {}).get("prepared_csv")}`
- Raw-data caveat: this example sets `require_fixed_data_version=False`; results are exploratory because yfinance data can be revised.

## Strategy

The primary strategy is a pre-registered long-flat Bollinger Bands mean-reversion rule.

- Middle band: {args.window}-day simple moving average of the source-specific price.
- Band width: middle band +/- `{args.num_std}` rolling standard deviations.
- Entry: after the close on day `t`, enter long if price crosses from above/equal the lower band to below the lower band.
- Exit: after the close on day `t`, exit if price crosses from below/equal the middle band to above the middle band.
- Time stop: exit after `{args.max_hold}` trading days if the middle-band exit has not occurred.
- Execution: signals observed after the close are applied with a one-bar lag; return on `t+1` uses the position decided at `t`.
- Positioning: no leverage, no shorting, position is either 0 or 1.
- Transaction cost: `{args.cost_bps}` bps per one-way full-notional position change.

## Cross-Validation

- Fold design: expanding walk-forward validation.
- Number of folds: `{args.n_folds}`. This is chosen for a five-year daily sample so validation windows are roughly annual while retaining a meaningful expanding train history.
- Purge: label horizon is set to `as_of_date + max_hold`; train observations whose holding horizon overlaps validation are removed.
- Embargo: `{args.embargo_bars}` bar after the purge boundary.
- Regime gate: Janus rule-based volatility-regime labels are used with the configured diversity gate. Failed folds are reported rather than silently removed.

## Research Controls

- The primary parameters are fixed before evaluation: window `{args.window}`, band width `{args.num_std}`, max hold `{args.max_hold}`.
- Both sources use identical trading dates, strategy logic, transaction costs, fold count, and metrics.
- A robustness grid over windows `[10, 20, 50]` and band widths `[1.5, 2.0, 2.5]` is reported for sensitivity only.
- Deflated Sharpe Ratio uses the nine-grid trial count. PBO is estimated from fold-level returns across the robustness grid.
- A moving-block bootstrap on paired daily return differences is included to avoid treating autocorrelated strategy returns as iid.
"""


def render_summary_report(
    args: argparse.Namespace,
    symbol: str,
    paths: dict[str, str],
    summary: dict[str, Any],
    metrics_df: pd.DataFrame,
    fold_df: pd.DataFrame,
    paired_df: pd.DataFrame,
    overfit_df: pd.DataFrame,
) -> str:
    key = metrics_df[
        (metrics_df["stream"] == "bollinger_net")
    ][
        [
            "source",
            "observations",
            "total_return",
            "cagr",
            "ann_vol",
            "sharpe",
            "sortino",
            "max_dd",
            "cvar_95",
            "hit_rate",
            "invested_hit_rate",
            "exposure",
            "position_changes",
            "round_trips",
            "total_tx_cost",
        ]
    ]
    fold_view = fold_df[
        [
            "source",
            "fold",
            "train_obs",
            "val_obs",
            "val_start",
            "val_end",
            "total_return",
            "sharpe",
            "max_dd",
            "hit_rate",
            "diversity_pass",
        ]
    ]
    lines = [
        f"# Bollinger Pipeline-vs-Raw Backtest: {args.run_id}",
        "",
        f"- Instrument: `{symbol}`",
        f"- Window: `{args.start}` inclusive to `{args.end}` exclusive",
        f"- Pipeline output: `{summary.get('output_dir')}`",
        "",
        "## Primary Net Strategy Metrics",
        "",
        _md_table(key, list(key.columns), max_rows=10),
        "",
        "## Paired Daily Difference",
        "",
        _md_table(paired_df, list(paired_df.columns), max_rows=5),
        "",
        "## Per-Fold Net Strategy Metrics",
        "",
        _md_table(fold_view, list(fold_view.columns), max_rows=20),
        "",
        "## Overfitting Diagnostics",
        "",
        _md_table(overfit_df, list(overfit_df.columns), max_rows=10),
        "",
        "## Charts",
        "",
        "- Equity curve: `charts/equity_curve.png`",
        "- Drawdown: `charts/drawdown.png`",
        "- Fold total return: `charts/fold_total_return.png`",
        "- Robustness Sharpe heatmap: `charts/robustness_sharpe_heatmap.png`",
        "",
        "## Artifacts",
    ]
    for name, path in paths.items():
        lines.append(f"- {name}: `{path}`")
    lines.append("")
    return "\n".join(lines)


def run_experiment(args: argparse.Namespace) -> dict[str, str]:
    pipeline_summary = _run_janus_pipeline(
        args.instrument,
        args.start,
        args.end,
        f"{args.run_id}_janus_pipeline",
        args.n_folds,
        args.window + 1,
        args.embargo_bars,
    )
    cfg = normalize_config(load_config(args.instrument))
    symbol = cfg.get("symbol", {}).get("ticker") or args.instrument.upper()

    pipeline_base = _prepare_pipeline_frame(pipeline_summary)
    direct_base = _prepare_direct_frame(symbol, args.start, args.end)
    pipeline_base, direct_base = _filter_common_calendar([pipeline_base, direct_base])
    base_frames = {"janus_pipeline": pipeline_base, "direct_provider": direct_base}

    tested = {
        source: apply_bollinger_strategy(
            frame,
            window=args.window,
            num_std=args.num_std,
            max_hold=args.max_hold,
            cost_bps=args.cost_bps,
        )
        for source, frame in base_frames.items()
    }

    cv_cfg = {
        **cfg,
        "date_col": "as_of_date",
        "return_col": "strategy_return",
        "vol_col": "vol_std",
        "n_folds": args.n_folds,
        "purge_bars": args.window + 1,
        "event_embargo_bars": args.embargo_bars,
        "label_end_col": "label_end_date",
        "regime_axes": ["vol_regime"],
    }
    metrics_df = aggregate_metrics(tested)
    fold_df, diversity_df = cross_validation(tested, cv_cfg)
    paired_df = paired_difference(tested, bootstrap_samples=args.bootstrap_samples, block=args.bootstrap_block)
    robustness_df, overfit_df = robustness_grid(
        base_frames,
        windows=[10, 20, 50],
        num_stds=[1.5, 2.0, 2.5],
        cost_bps=args.cost_bps,
        cv_cfg=cv_cfg,
    )

    out_dir = Path(args.outputs_dir) / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    chart_paths = render_charts(tested, fold_df, robustness_df, out_dir)

    combined_daily = pd.concat(tested.values(), ignore_index=True)
    paths: dict[str, str] = {}
    frames = {
        "daily_strategy": combined_daily,
        "metrics_summary": metrics_df,
        "per_fold_metrics": fold_df,
        "fold_diversity": diversity_df,
        "paired_difference": paired_df,
        "robustness_grid": robustness_df,
        "overfitting_diagnostics": overfit_df,
    }
    for name, frame in frames.items():
        path = out_dir / f"{name}.csv"
        _serialize_frame(frame).to_csv(path, index=False)
        paths[name] = str(path)
    paths.update(chart_paths)

    setup_path = out_dir / "experimental_setup.md"
    setup_path.write_text(render_experimental_setup(args, symbol, pipeline_summary), encoding="utf-8")
    paths["experimental_setup"] = str(setup_path)

    summary_path = out_dir / "summary_report.md"
    summary_path.write_text(
        render_summary_report(args, symbol, paths, pipeline_summary, metrics_df, fold_df, paired_df, overfit_df),
        encoding="utf-8",
    )
    paths["summary_report"] = str(summary_path)

    payload_path = out_dir / "experiment_payload.json"
    _write_json(
        payload_path,
        {
            "args": vars(args),
            "symbol": symbol,
            "pipeline_summary": pipeline_summary,
            "artifacts": paths,
            "primary_metrics": metrics_df.to_dict(orient="records"),
            "paired_difference": paired_df.to_dict(orient="records"),
            "overfitting_diagnostics": overfit_df.to_dict(orient="records"),
        },
    )
    paths["experiment_payload"] = str(payload_path)

    print(json.dumps(paths, indent=2))
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Bollinger Bands Janus-vs-direct data experiment.")
    parser.add_argument("--instrument", default="aapl", help="Instrument config name or equity ticker.")
    parser.add_argument("--start", default="2020-01-01", help="Inclusive start date.")
    parser.add_argument("--end", default="2025-01-01", help="Exclusive provider end date.")
    parser.add_argument("--run-id", default="bollinger_aapl_2020_2024", help="Output run id.")
    parser.add_argument("--outputs-dir", default="outputs/bollinger_experiment")
    parser.add_argument("--window", type=int, default=20)
    parser.add_argument("--num-std", type=float, default=2.0)
    parser.add_argument("--max-hold", type=int, default=20)
    parser.add_argument("--cost-bps", type=float, default=5.0)
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument("--embargo-bars", type=int, default=1)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--bootstrap-block", type=int, default=20)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run_experiment(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
