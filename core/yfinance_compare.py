"""Compare the equity pipeline against direct yfinance data."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from adapters.equity_adapter import EquityAdapter
from core import metrics, regime, splitter, validators
from ingestion.equity_loader_a import EquityLoaderA


def load_config(instrument_name: str) -> dict[str, Any]:
    """Load instrument config with family defaults."""
    inst_path = Path(f"configs/instruments/{instrument_name}.yaml")
    if not inst_path.exists():
        raise FileNotFoundError(f"Instrument config not found: {inst_path}")

    with open(inst_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    family = cfg.get("family", "equity")
    family_path = Path(f"configs/{family}.yaml")
    if family_path.exists():
        with open(family_path, encoding="utf-8") as f:
            defaults = yaml.safe_load(f)
        for key, value in defaults.items():
            cfg.setdefault(key, value)

    return cfg


def _market_date(values: pd.Series) -> pd.Series:
    dt = pd.to_datetime(values)
    if getattr(dt.dt, "tz", None) is not None:
        dt = dt.dt.tz_convert("America/New_York")
    return dt.dt.date


def _to_python(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if pd.isna(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, (date,)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def _write_json(path: Path, payload: Any) -> None:
    def default(value: Any) -> Any:
        return _to_python(value)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=default)


def _serialize_cell(value: Any) -> Any:
    if isinstance(value, set):
        return ";".join(str(v) for v in sorted(value))
    return _to_python(value)


def _serialize_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == "object":
            out[col] = out[col].map(_serialize_cell)
    return out


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, np.integer, np.floating)) and not pd.isna(value):
        return float(value)
    return None


def fetch_direct_yfinance(symbol: str, start: str, end: str, cache_dir: str | Path = "outputs/cache/yfinance") -> pd.DataFrame:
    """Fetch direct yfinance history without going through the ingestion layer."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ImportError("yfinance is required for direct provider comparison.") from exc

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache_dir))

    hist = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=False)
    if hist.empty:
        return pd.DataFrame()

    df = hist.reset_index()
    df.rename(columns={"Date": "as_of_date"}, inplace=True)
    adj_close = df["Adj Close"] if "Adj Close" in df.columns else df["Close"]

    out = pd.DataFrame(
        {
            "as_of_date": pd.to_datetime(df["as_of_date"]),
            "market_date": _market_date(df["as_of_date"]),
            "symbol": symbol,
            "raw_close": df["Close"].astype(float),
            "direct_adj_close": adj_close.astype(float),
            "adj_factor": (adj_close / df["Close"]).astype(float),
            "volume": df["Volume"].fillna(0).astype(int),
            "provider": "direct_yfinance",
        }
    )
    return out


def prepare_pipeline_frame(symbol: str, cfg: dict[str, Any], start: str, end: str) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    """Fetch through the project loader and prepare through the equity adapter."""
    raw_df = EquityLoaderA().fetch(symbol, start, end)
    if raw_df.empty:
        return raw_df, {}, raw_df

    df, core_cfg = EquityAdapter(cfg).prepare(raw_df)
    df = validators.logical_bounds_check(df, core_cfg)
    df = validators.missing_completeness(df, core_cfg)
    df = validators.outlier_cap(df, core_cfg)
    df = df.copy()
    df["market_date"] = _market_date(df["as_of_date"])
    return df, core_cfg, raw_df


def prepare_direct_frame(direct_raw: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Create the same core columns from direct yfinance adjusted close."""
    if direct_raw.empty:
        return direct_raw, {}

    df = direct_raw.copy()
    df = df.sort_values(["symbol", "as_of_date"]).reset_index(drop=True)
    df["price_std"] = df["direct_adj_close"]
    df["return_std"] = df.groupby("symbol")["price_std"].pct_change()
    vol_window = cfg.get("vol_window", 21)
    df["vol_std"] = df.groupby("symbol")["return_std"].transform(lambda x: x.rolling(vol_window, min_periods=5).std())
    df["volume_std"] = df["volume"]

    core_cfg = {
        **cfg,
        "price_col": "price_std",
        "vol_col": "vol_std",
        "return_col": "return_std",
        "vol_window": vol_window,
        "trend_window": cfg.get("trend_window", 126),
        "regime_axes": cfg.get("regime_axes", ["vol_regime"]),
        "event_flags": [],
    }
    return df, core_cfg


def align_pipeline_direct(pipeline_df: pd.DataFrame, direct_df: pd.DataFrame) -> pd.DataFrame:
    """Align prepared pipeline and direct frames by market date."""
    p_cols = ["market_date", "raw_close", "adj_factor", "price_std", "return_std", "vol_std", "volume"]
    d_cols = ["market_date", "raw_close", "adj_factor", "price_std", "return_std", "vol_std", "volume"]

    p = pipeline_df[p_cols].rename(columns={c: f"pipeline_{c}" for c in p_cols if c != "market_date"})
    d = direct_df[d_cols].rename(columns={c: f"direct_{c}" for c in d_cols if c != "market_date"})
    aligned = p.merge(d, on="market_date", how="outer", indicator=True).sort_values("market_date").reset_index(drop=True)

    for col in ["raw_close", "adj_factor", "price_std", "return_std", "vol_std"]:
        aligned[f"{col}_diff"] = aligned[f"pipeline_{col}"] - aligned[f"direct_{col}"]
        aligned[f"{col}_abs_diff"] = aligned[f"{col}_diff"].abs()

    aligned["price_diff_pct"] = aligned["price_std_diff"] / aligned["direct_price_std"].abs()
    aligned["return_diff_bps"] = aligned["return_std_diff"] * 10000.0
    aligned["pipeline_equity"] = (1 + aligned["pipeline_return_std"].fillna(0)).cumprod()
    aligned["direct_equity"] = (1 + aligned["direct_return_std"].fillna(0)).cumprod()
    return aligned


def data_comparison(aligned: pd.DataFrame) -> pd.DataFrame:
    """Summarize data-level parity between pipeline and direct frames."""
    common = aligned[aligned["_merge"] == "both"]

    def metric(name: str, value: Any, note: str = "") -> dict[str, Any]:
        return {"metric": name, "value": _to_python(value), "note": note}

    return pd.DataFrame(
        [
            metric("pipeline_rows", (aligned["_merge"] != "right_only").sum()),
            metric("direct_rows", (aligned["_merge"] != "left_only").sum()),
            metric("matched_rows", len(common)),
            metric("pipeline_only_rows", (aligned["_merge"] == "left_only").sum()),
            metric("direct_only_rows", (aligned["_merge"] == "right_only").sum()),
            metric("first_common_date", common["market_date"].min() if not common.empty else None),
            metric("last_common_date", common["market_date"].max() if not common.empty else None),
            metric("max_abs_raw_close_diff", common["raw_close_abs_diff"].max() if not common.empty else None),
            metric("mean_abs_raw_close_diff", common["raw_close_abs_diff"].mean() if not common.empty else None),
            metric("max_abs_price_std_diff", common["price_std_abs_diff"].max() if not common.empty else None),
            metric("mean_abs_price_std_diff", common["price_std_abs_diff"].mean() if not common.empty else None),
            metric("max_abs_price_diff_pct", common["price_diff_pct"].abs().max() if not common.empty else None),
            metric("mean_abs_price_diff_pct", common["price_diff_pct"].abs().mean() if not common.empty else None),
            metric("max_abs_return_diff_bps", common["return_diff_bps"].abs().max() if not common.empty else None),
            metric("mean_abs_return_diff_bps", common["return_diff_bps"].abs().mean() if not common.empty else None),
            metric(
                "rows_return_diff_gt_1bp",
                (common["return_diff_bps"].abs() > 1.0).sum() if not common.empty else None,
                "Often caused by pipeline return clipping or adjusted-price definition differences.",
            ),
        ]
    )


def aggregate_backtest_metrics(returns: pd.Series) -> dict[str, Any]:
    """Buy-and-hold baseline metrics from a daily return series."""
    r = returns.dropna()
    if r.empty:
        return {}
    equity = (1 + r).cumprod()
    out: dict[str, Any] = {"observations": len(r)}
    out.update(metrics.return_metrics(r))
    out.update(metrics.risk_adjusted(r))
    out.update(metrics.drawdown_metrics(equity))
    tail = metrics.tail_metrics(r)
    out["var_95"] = tail.get("var")
    out["cvar_95"] = tail.get("cvar")
    out["worst_day"] = tail.get("worst_day")
    out["worst_week"] = tail.get("worst_week")
    hit = metrics.hit_metrics(r)
    out["hit_rate"] = hit.get("win_rate")
    out["profit_factor"] = hit.get("profit_factor")
    out["payoff_ratio"] = hit.get("payoff_ratio")
    out["longest_losing_streak"] = hit.get("longest_losing_streak")
    return out


def metric_comparison(aligned: pd.DataFrame) -> pd.DataFrame:
    """Compare buy-and-hold metrics on aligned common dates."""
    common = aligned[aligned["_merge"] == "both"].copy()
    p = aggregate_backtest_metrics(common["pipeline_return_std"])
    d = aggregate_backtest_metrics(common["direct_return_std"])

    rows: list[dict[str, Any]] = []
    for metric_name in sorted(set(p) | set(d)):
        p_val = p.get(metric_name)
        d_val = d.get(metric_name)
        p_num = _safe_float(p_val)
        d_num = _safe_float(d_val)
        diff = p_num - d_num if p_num is not None and d_num is not None else None
        diff_pct = diff / abs(d_num) if diff is not None and d_num not in (None, 0.0) else None
        rows.append(
            {
                "metric": metric_name,
                "pipeline": _to_python(p_val),
                "direct_yfinance": _to_python(d_val),
                "diff": diff,
                "diff_pct": diff_pct,
            }
        )
    return pd.DataFrame(rows)


def _fold_metrics(df: pd.DataFrame, folds: list[tuple[np.ndarray, np.ndarray]], labels: pd.Series, cfg: dict[str, Any]) -> pd.DataFrame:
    return_col = cfg.get("return_col", "return_std")
    rows: list[dict[str, Any]] = []
    for fold_id, (_, val_idx) in enumerate(folds):
        r = df[return_col].iloc[val_idx].dropna()
        if r.empty:
            total_return = sharpe = max_dd = hit_rate = None
        else:
            total_return = (1 + r).prod() - 1
            sharpe = metrics.risk_adjusted(r).get("sharpe")
            max_dd = metrics.drawdown_metrics((1 + r).cumprod()).get("max_dd")
            hit_rate = float((r > 0).mean())

        val_dates = df["market_date"].iloc[val_idx]
        label_counts = labels.iloc[val_idx].value_counts(normalize=True)
        rows.append(
            {
                "fold": fold_id,
                "val_start": val_dates.min(),
                "val_end": val_dates.max(),
                "val_obs": len(val_idx),
                "dominant_regime": label_counts.index[0] if not label_counts.empty else None,
                "dominant_regime_share": label_counts.iloc[0] if not label_counts.empty else None,
                "total_return": total_return,
                "sharpe": sharpe,
                "max_dd": max_dd,
                "hit_rate": hit_rate,
            }
        )
    return pd.DataFrame(rows)


def cross_validation_outputs(df: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build fold metrics and diversity gate output for one prepared frame."""
    folds = splitter.walk_forward_split(df, cfg)
    folds = splitter.purge_embargo(folds, df, cfg)
    labels = regime.assign_regime_labels(df, cfg)
    diversity = splitter.regime_diversity_gate(folds, labels, cfg)
    fold_metrics = _fold_metrics(df, folds, labels, cfg)
    return fold_metrics, diversity


def cross_validation_comparison(
    pipeline_fold: pd.DataFrame,
    pipeline_diversity: pd.DataFrame,
    direct_fold: pd.DataFrame,
    direct_diversity: pd.DataFrame,
) -> pd.DataFrame:
    """One row per fold comparing CV results."""
    p = pipeline_fold.merge(pipeline_diversity, on="fold", how="left", suffixes=("", "_div")).add_prefix("pipeline_")
    d = direct_fold.merge(direct_diversity, on="fold", how="left", suffixes=("", "_div")).add_prefix("direct_")
    out = p.merge(d, left_on="pipeline_fold", right_on="direct_fold", how="outer")
    out["fold"] = out["pipeline_fold"].fillna(out["direct_fold"])
    for col in ["total_return", "sharpe", "max_dd", "hit_rate", "conc", "kl", "js"]:
        p_col = f"pipeline_{col}"
        d_col = f"direct_{col}"
        if p_col in out.columns and d_col in out.columns:
            out[f"{col}_diff"] = out[p_col] - out[d_col]
    first_cols = ["fold"]
    return out[first_cols + [c for c in out.columns if c not in first_cols]]


def cv_summary(source: str, fold: pd.DataFrame, diversity: pd.DataFrame) -> pd.DataFrame:
    """Small CV summary table for cards."""
    n_folds = len(fold)
    n_passed = int(diversity["pass"].sum()) if "pass" in diversity else 0

    rows = [
        {"source": source, "metric": "n_folds", "value": n_folds},
        {"source": source, "metric": "n_passed", "value": n_passed},
        {"source": source, "metric": "pass_rate", "value": None if n_folds == 0 else n_passed / n_folds},
    ]
    for metric_name in ["total_return", "sharpe", "max_dd", "hit_rate"]:
        if metric_name in fold:
            rows.append({"source": source, "metric": f"mean_fold_{metric_name}", "value": fold[metric_name].mean()})
            rows.append({"source": source, "metric": f"min_fold_{metric_name}", "value": fold[metric_name].min()})
            rows.append({"source": source, "metric": f"max_fold_{metric_name}", "value": fold[metric_name].max()})
    for metric_name in ["conc", "kl", "js"]:
        if metric_name in diversity:
            rows.append({"source": source, "metric": f"mean_{metric_name}", "value": diversity[metric_name].mean()})
            rows.append({"source": source, "metric": f"max_{metric_name}", "value": diversity[metric_name].max()})
    return pd.DataFrame(rows)


def render_markdown(
    run_id: str,
    symbol: str,
    start: str,
    end: str,
    data_df: pd.DataFrame,
    metric_df: pd.DataFrame,
    cv_summary_df: pd.DataFrame,
    paths: dict[str, str],
) -> str:
    def md_table(df: pd.DataFrame, columns: list[str], max_rows: int = 16) -> str:
        if df.empty:
            return "_No rows._"
        view = df[columns].head(max_rows).fillna("")
        header = "| " + " | ".join(columns) + " |"
        sep = "| " + " | ".join(["---"] * len(columns)) + " |"
        rows = ["| " + " | ".join(str(row[c]) for c in columns) + " |" for _, row in view.iterrows()]
        return "\n".join([header, sep, *rows])

    key_metrics = metric_df[metric_df["metric"].isin(["total_return", "cagr", "ann_vol", "sharpe", "max_dd", "hit_rate", "cvar_95"])]
    lines = [
        f"# Pipeline vs Direct yfinance Report: {run_id}",
        "",
        "## Scope",
        f"- Symbol: {symbol}",
        f"- Requested range: {start} to {end}",
        "- Backtest definition: buy-and-hold adjusted daily returns.",
        "- Direct baseline: raw yfinance history with adjusted close.",
        "",
        "## Data Parity",
        md_table(data_df, ["metric", "value", "note"]),
        "",
        "## Backtest Metric Comparison",
        md_table(key_metrics, ["metric", "pipeline", "direct_yfinance", "diff", "diff_pct"]),
        "",
        "## Cross-Validation Summary",
        md_table(cv_summary_df, ["source", "metric", "value"], max_rows=60),
        "",
        "## Notes For Calibration",
        "- Large price differences usually mean adjustment or alignment issues.",
        "- Large return differences with near-zero price differences usually mean pipeline transformations such as clipping.",
        "- Low fold pass rate means the current CV regime thresholds or sample design need calibration before performance conclusions.",
        "",
        "## Visualization Files",
    ]
    for name, path in paths.items():
        lines.append(f"- {name}: `{path}`")
    lines.append("")
    return "\n".join(lines)


def run_comparison(
    instrument: str,
    start: str,
    end: str,
    run_id: str,
    outputs_dir: str | Path = "outputs",
) -> dict[str, str]:
    cfg = load_config(instrument)
    symbol = cfg.get("symbol", {}).get("ticker")
    if not symbol:
        raise ValueError("This comparison requires an equity ticker symbol in config.")

    pipeline_df, pipeline_cfg, raw_pipeline = prepare_pipeline_frame(symbol, cfg, start, end)
    direct_raw = fetch_direct_yfinance(symbol, start, end)
    direct_df, direct_cfg = prepare_direct_frame(direct_raw, cfg)

    if pipeline_df.empty or direct_df.empty:
        raise ValueError("Pipeline or direct yfinance data is empty; cannot compare.")

    aligned = align_pipeline_direct(pipeline_df, direct_df)
    data_df = data_comparison(aligned)
    metric_df = metric_comparison(aligned)

    pipeline_fold, pipeline_diversity = cross_validation_outputs(pipeline_df, pipeline_cfg)
    direct_fold, direct_diversity = cross_validation_outputs(direct_df, direct_cfg)
    cv_compare = cross_validation_comparison(pipeline_fold, pipeline_diversity, direct_fold, direct_diversity)
    cv_summary_df = pd.concat(
        [cv_summary("pipeline", pipeline_fold, pipeline_diversity), cv_summary("direct_yfinance", direct_fold, direct_diversity)],
        ignore_index=True,
    )

    out_dir = Path(outputs_dir) / "comparison"
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, str] = {}
    frames = {
        "aligned_returns": aligned,
        "data_comparison": data_df,
        "metric_comparison": metric_df,
        "cv_comparison": cv_compare,
        "cv_summary": cv_summary_df,
        "pipeline_fold_metrics": pipeline_fold,
        "direct_fold_metrics": direct_fold,
        "pipeline_diversity": pipeline_diversity,
        "direct_diversity": direct_diversity,
    }

    frames = {name: _serialize_frame(frame) for name, frame in frames.items()}

    for name, frame in frames.items():
        path = out_dir / f"{run_id}_{name}.csv"
        frame.to_csv(path, index=False)
        paths[name] = str(path)

    payload = {
        "run_id": run_id,
        "instrument": instrument,
        "symbol": symbol,
        "requested_range": [start, end],
        "pipeline_raw_rows": len(raw_pipeline),
        "pipeline_prepared_rows": len(pipeline_df),
        "direct_rows": len(direct_df),
        "tables": {name: json.loads(frame.to_json(orient="records", date_format="iso")) for name, frame in frames.items()},
    }
    json_path = out_dir / f"{run_id}_visualization.json"
    _write_json(json_path, payload)
    paths["visualization_json"] = str(json_path)

    md_path = out_dir / f"{run_id}_summary_report.md"
    paths["markdown"] = str(md_path)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(run_id, symbol, start, end, data_df, metric_df, cv_summary_df, paths))

    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare pipeline output against direct yfinance data.")
    parser.add_argument("--instrument", default="aapl")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--outputs-dir", default="outputs")
    args = parser.parse_args()

    paths = run_comparison(args.instrument, args.start, args.end, args.run_id, args.outputs_dir)
    print(json.dumps(paths, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
