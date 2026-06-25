"""Panel ablation for Bollinger Bands: Janus vs direct provider definitions.

Run:
    python examples/bollinger_panel_ablation_experiment.py

This experiment is designed to answer a narrower question than the single-name
demo: is Janus-vs-direct performance difference persistent, or mostly a price
definition artifact? It runs a fixed Bollinger rule across a small equity panel,
then swaps only signal price and return construction.
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
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = Path(__file__).resolve().parent
for path in (ROOT, EXAMPLES):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from bollinger_pipeline_vs_raw_experiment import (  # noqa: E402
    _filter_common_calendar,
    _format_md_value,
    _json_default,
    _market_date,
    _md_table,
    _performance_row,
    _prepare_pipeline_frame,
    _safe_float,
    _serialize_frame,
    _write_json,
    apply_bollinger_strategy,
)
from core.config import normalize_config  # noqa: E402
from run_pipeline import apply_runtime_overrides, load_config, run_pipeline  # noqa: E402


DEFAULT_UNIVERSE = [
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "AMZN",
    "GOOGL",
    "META",
    "JPM",
    "XOM",
    "JNJ",
]


PAIR_DEFINITIONS = [
    ("janus_full", "direct_adj_full"),
    ("janus_full", "direct_close_total"),
    ("janus_full", "adj_signal_janus_return"),
    ("janus_full", "janus_signal_adj_return"),
    ("direct_close_total", "direct_adj_full"),
]

DAILY_EPS = 1e-12
TOTAL_EPS = 1e-10


def _run_pipeline_for_ticker(
    ticker: str,
    start: str,
    end: str,
    run_id: str,
    n_folds: int,
    purge_bars: int,
    embargo_bars: int,
) -> dict[str, Any]:
    cfg = load_config(ticker.lower())
    cfg = apply_runtime_overrides(
        cfg,
        ticker=ticker,
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


def fetch_direct_actions(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fetch direct yfinance bars with corporate actions, outside Janus pipeline."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ImportError("yfinance is required for this experiment.") from exc

    cache_dir = Path("outputs/cache/yfinance")
    cache_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache_dir))

    hist = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=False, actions=True)
    if hist.empty:
        return pd.DataFrame()

    df = hist.reset_index().rename(columns={"Date": "as_of_date"})
    close = pd.to_numeric(df["Close"], errors="coerce")
    adj = pd.to_numeric(df["Adj Close"], errors="coerce") if "Adj Close" in df else close
    dividend = pd.to_numeric(df.get("Dividends", 0.0), errors="coerce").fillna(0.0)
    volume = pd.to_numeric(df.get("Volume", 0.0), errors="coerce").fillna(0.0)
    out = pd.DataFrame(
        {
            "as_of_date": pd.to_datetime(df["as_of_date"], errors="coerce", utc=True),
            "market_date": _market_date(df["as_of_date"]),
            "close": close,
            "adj_close": adj,
            "dividend": dividend,
            "volume": volume,
        }
    )
    out["adj_return"] = out["adj_close"].pct_change()
    out["close_price_return"] = out["close"].pct_change()
    out["close_total_return"] = (out["close"] + out["dividend"]) / out["close"].shift(1) - 1.0
    return out.sort_values("market_date").drop_duplicates("market_date").reset_index(drop=True)


def _frame(
    ticker: str,
    source: str,
    dates: pd.Series,
    as_of: pd.Series,
    price: pd.Series,
    returns: pd.Series,
    *,
    price_input: str,
    return_input: str,
    volume: pd.Series | None = None,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ticker,
            "source": source,
            "as_of_date": as_of,
            "market_date": dates,
            "price_signal": pd.to_numeric(price, errors="coerce"),
            "asset_return": pd.to_numeric(returns, errors="coerce"),
            "volume": pd.to_numeric(volume, errors="coerce") if volume is not None else np.nan,
            "price_input": price_input,
            "return_input": return_input,
        }
    )


def build_variant_frames(ticker: str, pipeline_base: pd.DataFrame, direct: pd.DataFrame) -> dict[str, pd.DataFrame]:
    p = pipeline_base.rename(
        columns={
            "price_signal": "janus_price",
            "asset_return": "janus_return",
            "volume": "janus_volume",
            "as_of_date": "janus_as_of_date",
        }
    )
    d = direct.rename(columns={"as_of_date": "direct_as_of_date", "volume": "direct_volume"})
    aligned = p.merge(d, on="market_date", how="inner").sort_values("market_date").reset_index(drop=True)
    if aligned.empty:
        raise ValueError(f"{ticker}: no common Janus/direct dates")

    variants = {
        "janus_full": _frame(
            ticker,
            "janus_full",
            aligned["market_date"],
            aligned["janus_as_of_date"],
            aligned["janus_price"],
            aligned["janus_return"],
            price_input="Janus prepared.price_std",
            return_input="Janus prepared.return_std",
            volume=aligned["janus_volume"],
        ),
        "direct_adj_full": _frame(
            ticker,
            "direct_adj_full",
            aligned["market_date"],
            aligned["direct_as_of_date"],
            aligned["adj_close"],
            aligned["adj_return"],
            price_input="Direct yfinance Adj Close",
            return_input="pct_change(Adj Close)",
            volume=aligned["direct_volume"],
        ),
        "direct_close_total": _frame(
            ticker,
            "direct_close_total",
            aligned["market_date"],
            aligned["direct_as_of_date"],
            aligned["close"],
            aligned["close_total_return"],
            price_input="Direct yfinance Close",
            return_input="(Close + Dividends) / lag(Close) - 1",
            volume=aligned["direct_volume"],
        ),
        "adj_signal_janus_return": _frame(
            ticker,
            "adj_signal_janus_return",
            aligned["market_date"],
            aligned["janus_as_of_date"],
            aligned["adj_close"],
            aligned["janus_return"],
            price_input="Direct yfinance Adj Close",
            return_input="Janus prepared.return_std",
            volume=aligned["direct_volume"],
        ),
        "janus_signal_adj_return": _frame(
            ticker,
            "janus_signal_adj_return",
            aligned["market_date"],
            aligned["janus_as_of_date"],
            aligned["janus_price"],
            aligned["adj_return"],
            price_input="Janus prepared.price_std",
            return_input="pct_change(Adj Close)",
            volume=aligned["janus_volume"],
        ),
    }
    return variants


def _newey_west_mean_test(values: pd.Series, lags: int | None = None) -> dict[str, Any]:
    x = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    n = len(x)
    if n < 5:
        return {"nw_n": n, "nw_mean": None, "nw_t": None, "nw_p": None, "nw_lags": lags}
    if lags is None:
        lags = int(math.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    mu = float(x.mean())
    z = x - mu
    gamma0 = float(np.dot(z, z) / n)
    lrv = gamma0
    for lag in range(1, min(lags, n - 1) + 1):
        weight = 1.0 - lag / (lags + 1.0)
        gamma = float(np.dot(z[lag:], z[:-lag]) / n)
        lrv += 2.0 * weight * gamma
    se = math.sqrt(max(lrv, 0.0) / n)
    t_stat = mu / se if se > 0 else 0.0
    p_value = 2.0 * (1.0 - stats.norm.cdf(abs(t_stat)))
    return {"nw_n": n, "nw_mean": mu, "nw_se": se, "nw_t": t_stat, "nw_p": p_value, "nw_lags": lags}


def _moving_block_bootstrap(values: pd.Series, *, block: int, samples: int, seed: int = 42) -> dict[str, Any]:
    x = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    n = len(x)
    if n < block or samples <= 0:
        return {"mbb_samples": samples, "mbb_block": block, "mbb_ci_2_5": None, "mbb_ci_97_5": None, "mbb_p": None}
    rng = np.random.default_rng(seed)
    starts = np.arange(0, n - block + 1)
    means = np.empty(samples)
    for i in range(samples):
        sample: list[float] = []
        while len(sample) < n:
            start = int(rng.choice(starts))
            sample.extend(x[start : start + block])
        means[i] = np.mean(sample[:n])
    return {
        "mbb_samples": samples,
        "mbb_block": block,
        "mbb_ci_2_5": float(np.quantile(means, 0.025)),
        "mbb_ci_97_5": float(np.quantile(means, 0.975)),
        "mbb_p": float(min(1.0, 2 * min((means <= 0).mean(), (means >= 0).mean()))),
    }


def _asset_level_test(asset_effects: pd.DataFrame, pair: str) -> dict[str, Any]:
    vals = pd.to_numeric(asset_effects["total_return_delta"], errors="coerce").dropna()
    vals = vals.mask(vals.abs() < TOTAL_EPS, 0.0)
    nonzero = vals[vals.abs() >= TOTAL_EPS]
    n = len(vals)
    row: dict[str, Any] = {
        "asset_n": n,
        "asset_nonzero_n": int(len(nonzero)),
        "asset_mean_total_delta": float(vals.mean()) if n else None,
        "asset_median_total_delta": float(vals.median()) if n else None,
        "asset_positive_share": float((vals > 0).mean()) if n else None,
    }
    if n >= 2:
        if len(nonzero) == 0:
            row["asset_ttest_t"] = 0.0
            row["asset_ttest_p"] = 1.0
        else:
            t = stats.ttest_1samp(vals, popmean=0.0)
            row["asset_ttest_t"] = float(t.statistic)
            row["asset_ttest_p"] = float(t.pvalue)
    if n:
        positives = int((nonzero > 0).sum())
        if len(nonzero) == 0:
            row["asset_sign_p"] = 1.0
        else:
            binom = stats.binomtest(positives, n=len(nonzero), p=0.5, alternative="two-sided")
            row["asset_sign_p"] = float(binom.pvalue)
    return row


def compare_pairs(tested: dict[str, pd.DataFrame], *, bootstrap_block: int, bootstrap_samples: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    asset_rows: list[dict[str, Any]] = []
    panel_rows: list[dict[str, Any]] = []

    for left, right in PAIR_DEFINITIONS:
        pair = f"{left}_minus_{right}"
        left_df = tested[left][["ticker", "market_date", "strategy_return", "equity", "position"]].rename(
            columns={"strategy_return": "left_return", "equity": "left_equity", "position": "left_position"}
        )
        right_df = tested[right][["ticker", "market_date", "strategy_return", "equity", "position"]].rename(
            columns={"strategy_return": "right_return", "equity": "right_equity", "position": "right_position"}
        )
        joined = left_df.merge(right_df, on=["ticker", "market_date"], how="inner").dropna(subset=["left_return", "right_return"])
        joined["daily_delta"] = joined["left_return"] - joined["right_return"]
        joined.loc[joined["daily_delta"].abs() < DAILY_EPS, "daily_delta"] = 0.0
        joined["position_diff"] = joined["left_position"] - joined["right_position"]

        for ticker, g in joined.groupby("ticker"):
            left_total = float((1.0 + g["left_return"]).prod() - 1.0)
            right_total = float((1.0 + g["right_return"]).prod() - 1.0)
            total_delta = left_total - right_total
            if abs(total_delta) < TOTAL_EPS:
                total_delta = 0.0
            mean_delta = float(g["daily_delta"].mean())
            if abs(mean_delta) < DAILY_EPS:
                mean_delta = 0.0
            abs_sum = float(g["daily_delta"].abs().sum())
            max_abs = float(g["daily_delta"].abs().max()) if len(g) else 0.0
            asset_rows.append(
                {
                    "pair": pair,
                    "left": left,
                    "right": right,
                    "ticker": ticker,
                    "observations": int(len(g)),
                    "left_total_return": left_total,
                    "right_total_return": right_total,
                    "total_return_delta": total_delta,
                    "mean_daily_delta": mean_delta,
                    "annualized_mean_delta": float(mean_delta * 252.0),
                    "position_diff_days": int((g["position_diff"].abs() > 1e-12).sum()),
                    "max_abs_daily_delta": max_abs,
                    "max_abs_delta_share": max_abs / abs_sum if abs_sum > 0 else 0.0,
                }
            )

        date_delta = joined.groupby("market_date")["daily_delta"].mean().sort_index()
        date_delta = date_delta.mask(date_delta.abs() < DAILY_EPS, 0.0)
        panel_mean = float(date_delta.mean()) if len(date_delta) else None
        if panel_mean is not None and abs(panel_mean) < DAILY_EPS:
            panel_mean = 0.0
        panel_row = {
            "pair": pair,
            "left": left,
            "right": right,
            "panel_dates": int(len(date_delta)),
            "panel_asset_days": int(len(joined)),
            "panel_mean_daily_delta": panel_mean,
            "panel_annualized_mean_delta": None if panel_mean is None else float(panel_mean * 252.0),
            "panel_total_delta_proxy": float(date_delta.sum()) if len(date_delta) else None,
            "position_diff_asset_days": int((joined["position_diff"].abs() > 1e-12).sum()),
        }
        panel_row.update(_newey_west_mean_test(date_delta))
        panel_row.update(_moving_block_bootstrap(date_delta, block=bootstrap_block, samples=bootstrap_samples))
        panel_rows.append(panel_row)

    asset_df = pd.DataFrame(asset_rows)
    panel_df = pd.DataFrame(panel_rows)
    if not asset_df.empty:
        enriched = []
        for pair, g in asset_df.groupby("pair"):
            row = panel_df[panel_df["pair"] == pair].iloc[0].to_dict()
            row.update(_asset_level_test(g, pair))
            enriched.append(row)
        panel_df = pd.DataFrame(enriched)
    return asset_df, panel_df


def summarize_metrics(tested: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for source, df in tested.items():
        for ticker, sub in df.groupby("ticker"):
            row = _performance_row(source, sub, "strategy_return", "bollinger_net")
            row["ticker"] = ticker
            row["price_input"] = sub["price_input"].iloc[0] if "price_input" in sub else None
            row["return_input"] = sub["return_input"].iloc[0] if "return_input" in sub else None
            rows.append(row)
    return pd.DataFrame(rows)


def render_charts(asset_df: pd.DataFrame, panel_df: pd.DataFrame, out_dir: Path) -> dict[str, str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    charts_dir = out_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="whitegrid")
    paths: dict[str, str] = {}

    primary = "janus_full_minus_direct_adj_full"
    view = asset_df[asset_df["pair"] == primary].copy()
    if not view.empty:
        view = view.sort_values("total_return_delta")
        fig, ax = plt.subplots(figsize=(11, 5))
        colors = ["#b55a5a" if v < 0 else "#4f79b7" for v in view["total_return_delta"]]
        ax.bar(view["ticker"], view["total_return_delta"], color=colors)
        ax.axhline(0.0, color="black", linewidth=1)
        ax.set_title("Janus Full minus Direct Adj: Total Return Delta by Asset")
        ax.set_ylabel("Total return delta")
        ax.yaxis.set_major_formatter(lambda x, _pos: f"{x:.0%}")
        fig.tight_layout()
        path = charts_dir / "asset_total_delta_janus_vs_direct_adj.png"
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths["chart_asset_total_delta"] = str(path)

    heat = asset_df.pivot_table(index="ticker", columns="pair", values="total_return_delta", aggfunc="mean")
    if not heat.empty:
        fig, ax = plt.subplots(figsize=(13, max(5, 0.45 * len(heat))))
        sns.heatmap(heat, annot=True, fmt=".2%", cmap="RdYlGn", center=0.0, ax=ax)
        ax.set_title("Ablation Total Return Delta by Asset")
        ax.set_xlabel("Comparison pair")
        ax.set_ylabel("Ticker")
        fig.tight_layout()
        path = charts_dir / "ablation_delta_heatmap.png"
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths["chart_ablation_delta_heatmap"] = str(path)

    if not panel_df.empty:
        fig, ax = plt.subplots(figsize=(11, 5))
        plot = panel_df.sort_values("panel_annualized_mean_delta")
        ax.barh(plot["pair"], plot["panel_annualized_mean_delta"], color="#5f8fbc")
        ax.axvline(0.0, color="black", linewidth=1)
        ax.set_title("Panel Annualized Mean Daily Delta")
        ax.set_xlabel("Annualized delta")
        ax.xaxis.set_major_formatter(lambda x, _pos: f"{x:.1%}")
        fig.tight_layout()
        path = charts_dir / "panel_annualized_delta.png"
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths["chart_panel_annualized_delta"] = str(path)

    return paths


def render_report(
    args: argparse.Namespace,
    paths: dict[str, str],
    panel_df: pd.DataFrame,
    asset_df: pd.DataFrame,
    metric_df: pd.DataFrame,
    failures: list[dict[str, Any]],
) -> str:
    panel_cols = [
        "pair",
        "panel_asset_days",
        "panel_annualized_mean_delta",
        "nw_t",
        "nw_p",
        "mbb_ci_2_5",
        "mbb_ci_97_5",
        "mbb_p",
        "asset_n",
        "asset_nonzero_n",
        "asset_mean_total_delta",
        "asset_positive_share",
        "asset_ttest_p",
        "asset_sign_p",
    ]
    primary_cols = [
        "ticker",
        "left_total_return",
        "right_total_return",
        "total_return_delta",
        "annualized_mean_delta",
        "position_diff_days",
        "max_abs_delta_share",
    ]
    primary = asset_df[asset_df["pair"] == "janus_full_minus_direct_adj_full"][primary_cols]
    metric_view = metric_df[
        metric_df["source"].isin(["janus_full", "direct_adj_full", "direct_close_total"])
    ][["ticker", "source", "total_return", "sharpe", "max_dd", "exposure", "round_trips"]]

    lines = [
        f"# Bollinger Panel Ablation: {args.run_id}",
        "",
        "## Experimental Setup",
        "",
        f"- Universe: `{', '.join(args.tickers)}`",
        f"- Window: `{args.start}` inclusive to `{args.end}` exclusive",
        f"- Strategy: Bollinger long-flat mean reversion, window `{args.window}`, band width `{args.num_std}`, max hold `{args.max_hold}`",
        f"- Costs: `{args.cost_bps}` bps per one-way full-notional position change",
        "- Primary contrast: `janus_full - direct_adj_full`",
        "- Closest apples-to-apples ablation: `janus_full - direct_close_total`",
        "- Statistical tests: date-clustered Newey-West mean test, moving-block bootstrap, asset-level t-test, asset-level sign test",
        "",
        "## Panel Tests",
        "",
        _md_table(panel_df[panel_cols], panel_cols, max_rows=20),
        "",
        "## Primary Asset Effects",
        "",
        _md_table(primary, primary_cols, max_rows=30),
        "",
        "## Variant Metrics",
        "",
        _md_table(metric_view, list(metric_view.columns), max_rows=60),
        "",
        "## Interpretation Guardrail",
        "",
        "If `janus_full - direct_adj_full` looks positive but `janus_full - direct_close_total` is near zero, the effect is mainly the adjusted-close signal definition, not Janus validation alpha.",
        "If asset-level tests and date-clustered tests disagree, treat the result as unstable rather than significant.",
        "",
        "## Failures",
        "",
        _md_table(pd.DataFrame(failures), ["ticker", "stage", "error"], max_rows=30) if failures else "_No skipped tickers._",
        "",
        "## Artifacts",
    ]
    for name, path in paths.items():
        lines.append(f"- {name}: `{path}`")
    lines.append("")
    return "\n".join(lines)


def run_experiment(args: argparse.Namespace) -> dict[str, str]:
    out_dir = Path(args.outputs_dir) / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    tested_by_variant: dict[str, list[pd.DataFrame]] = {}
    pipeline_summaries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for ticker in args.tickers:
        ticker = ticker.upper()
        try:
            summary = _run_pipeline_for_ticker(
                ticker,
                args.start,
                args.end,
                f"{args.run_id}_{ticker}_janus",
                args.n_folds,
                args.max_hold + 1,
                args.embargo_bars,
            )
            pipeline_base = _prepare_pipeline_frame(summary)
            pipeline_base["ticker"] = ticker
            direct = fetch_direct_actions(ticker, args.start, args.end)
            if direct.empty:
                raise ValueError("direct provider returned no rows")

            variants = build_variant_frames(ticker, pipeline_base, direct)
            variants = {name: _filter_common_calendar([frame])[0] for name, frame in variants.items()}
            for name, frame in variants.items():
                bt = apply_bollinger_strategy(
                    frame,
                    window=args.window,
                    num_std=args.num_std,
                    max_hold=args.max_hold,
                    cost_bps=args.cost_bps,
                )
                bt["ticker"] = ticker
                tested_by_variant.setdefault(name, []).append(bt)
            pipeline_summaries.append(
                {
                    "ticker": ticker,
                    "output_dir": summary.get("output_dir"),
                    "prepared_csv": summary.get("artifacts", {}).get("prepared_csv"),
                    "guard_status": summary.get("guard_status"),
                }
            )
            print(f"[ok] {ticker}")
        except Exception as exc:
            failures.append({"ticker": ticker, "stage": "experiment", "error": str(exc)})
            print(f"[skip] {ticker}: {exc}")

    if not tested_by_variant:
        raise ValueError("No tickers completed; cannot summarize experiment.")

    tested = {name: pd.concat(frames, ignore_index=True) for name, frames in tested_by_variant.items()}
    metric_df = summarize_metrics(tested)
    asset_df, panel_df = compare_pairs(
        tested,
        bootstrap_block=args.bootstrap_block,
        bootstrap_samples=args.bootstrap_samples,
    )

    paths: dict[str, str] = {}
    frame_outputs = {
        "variant_daily_returns": pd.concat(tested.values(), ignore_index=True),
        "variant_metrics": metric_df,
        "asset_pair_effects": asset_df,
        "panel_pair_tests": panel_df,
    }
    for name, frame in frame_outputs.items():
        path = out_dir / f"{name}.csv"
        _serialize_frame(frame).to_csv(path, index=False)
        paths[name] = str(path)

    chart_paths = render_charts(asset_df, panel_df, out_dir)
    paths.update(chart_paths)

    setup = {
        "args": vars(args),
        "pair_definitions": PAIR_DEFINITIONS,
        "pipeline_summaries": pipeline_summaries,
        "failures": failures,
        "paths": paths,
    }
    setup_path = out_dir / "experiment_payload.json"
    _write_json(setup_path, setup)
    paths["experiment_payload"] = str(setup_path)

    report_path = out_dir / "ablation_summary_report.md"
    report_path.write_text(render_report(args, paths, panel_df, asset_df, metric_df, failures), encoding="utf-8")
    paths["summary_report"] = str(report_path)

    print(json.dumps(paths, indent=2, default=_json_default))
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a panel Bollinger ablation/significance experiment.")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_UNIVERSE)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2025-01-01")
    parser.add_argument("--run-id", default="bollinger_panel_ablation_2020_2024")
    parser.add_argument("--outputs-dir", default="outputs/bollinger_panel_ablation")
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
    parser = build_parser()
    args = parser.parse_args()
    args.tickers = [ticker.upper() for ticker in args.tickers]
    run_experiment(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
