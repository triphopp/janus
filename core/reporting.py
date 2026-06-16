"""Visualization-ready summary reports for pipeline runs."""

from __future__ import annotations

import argparse
import html
import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd


STAGE_ORDER = {
    "ingestion": 0,
    "adapter": 1,
    "validators": 2,
    "stability": 3,
    "splitter": 4,
    "metrics": 5,
}


def safe_path_part(value: Any, fallback: str = "unknown", max_len: int = 80) -> str:
    """Return a compact filesystem-safe path segment."""
    text = str(value if value not in (None, "") else fallback)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._")
    return (text or fallback)[:max_len]


def run_folder_name(
    run_id: str,
    instrument: str,
    family: str,
    start: str,
    end: str,
) -> str:
    """Build a readable, stable folder name for one pipeline run."""
    return "__".join([
        safe_path_part(run_id),
        safe_path_part(instrument),
        safe_path_part(family),
        f"{safe_path_part(start)}_to_{safe_path_part(end)}",
    ])


def run_output_dir(
    outputs_dir: str | Path,
    run_id: str,
    instrument: str,
    family: str,
    start: str,
    end: str,
) -> Path:
    """Return the run-scoped output directory."""
    return Path(outputs_dir) / "runs" / run_folder_name(run_id, instrument, family, start, end)


def locate_run_output_dir(run_id: str, outputs_dir: str | Path = "outputs") -> Path:
    """Find a run folder by run_id, falling back to legacy flat output layout."""
    outputs_dir = Path(outputs_dir)
    runs_dir = outputs_dir / "runs"
    safe_run = safe_path_part(run_id)
    if runs_dir.exists():
        matches = sorted(path for path in runs_dir.iterdir() if path.is_dir() and path.name.startswith(f"{safe_run}__"))
        if matches:
            return matches[-1]
    return outputs_dir


def _stage_sort_key(snapshot: dict[str, Any]) -> tuple[int, str]:
    stage = snapshot.get("stage", "")
    return STAGE_ORDER.get(stage, 999), str(stage)


def _total_nulls(snapshot: dict[str, Any]) -> int:
    return int(sum(snapshot.get("na_pattern", {}).values()))


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


def stage_comparison(snapshots: list[dict[str, Any]]) -> pd.DataFrame:
    """One row per pipeline stage for dashboard timelines."""
    rows: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None

    for idx, snap in enumerate(sorted(snapshots, key=_stage_sort_key)):
        row_count = int(snap.get("row_count", 0))
        total_nulls = _total_nulls(snap)
        row = {
            "stage_order": idx,
            "stage": snap.get("stage"),
            "timestamp": snap.get("timestamp"),
            "row_count": row_count,
            "row_delta_from_previous": None if previous is None else row_count - int(previous.get("row_count", 0)),
            "total_nulls": total_nulls,
            "null_delta_from_previous": None if previous is None else total_nulls - _total_nulls(previous),
            "schema_hash": snap.get("schema_hash"),
            "data_hash": snap.get("data_hash"),
            "schema_changed_from_previous": False if previous is None else snap.get("schema_hash") != previous.get("schema_hash"),
            "data_changed_from_previous": False if previous is None else snap.get("data_hash") != previous.get("data_hash"),
            "tracked_columns": len(snap.get("key_stats", {})),
        }
        rows.append(row)
        previous = snap

    return pd.DataFrame(rows)


def stage_deltas(snapshots: list[dict[str, Any]]) -> pd.DataFrame:
    """Pairwise deltas between adjacent stages."""
    ordered = sorted(snapshots, key=_stage_sort_key)
    rows: list[dict[str, Any]] = []

    for before, after in zip(ordered, ordered[1:]):
        before_na = before.get("na_pattern", {})
        after_na = after.get("na_pattern", {})
        cols = set(before_na) | set(after_na)
        new_nulls = sum(max(int(after_na.get(c, 0)) - int(before_na.get(c, 0)), 0) for c in cols)
        resolved_nulls = sum(max(int(before_na.get(c, 0)) - int(after_na.get(c, 0)), 0) for c in cols)

        stat_changes = 0
        for col, after_stats in after.get("key_stats", {}).items():
            before_stats = before.get("key_stats", {}).get(col, {})
            for metric, after_value in after_stats.items():
                if before_stats.get(metric) != after_value:
                    stat_changes += 1

        before_rows = int(before.get("row_count", 0))
        after_rows = int(after.get("row_count", 0))
        rows.append(
            {
                "stage_from": before.get("stage"),
                "stage_to": after.get("stage"),
                "stage_pair": f"{before.get('stage')} -> {after.get('stage')}",
                "row_delta": after_rows - before_rows,
                "row_delta_pct": None if before_rows == 0 else (after_rows - before_rows) / before_rows,
                "schema_changed": after.get("schema_hash") != before.get("schema_hash"),
                "data_changed": after.get("data_hash") != before.get("data_hash"),
                "new_nulls_total": new_nulls,
                "resolved_nulls_total": resolved_nulls,
                "key_stat_changes": stat_changes,
            }
        )

    return pd.DataFrame(rows)


def column_stats_long(snapshots: list[dict[str, Any]]) -> pd.DataFrame:
    """Long-format column stats for charts and heatmaps."""
    rows: list[dict[str, Any]] = []
    for idx, snap in enumerate(sorted(snapshots, key=_stage_sort_key)):
        for column, stats in snap.get("key_stats", {}).items():
            for metric, value in stats.items():
                numeric_value = _num(value)
                rows.append(
                    {
                        "stage_order": idx,
                        "stage": snap.get("stage"),
                        "column": column,
                        "metric": metric,
                        "value_numeric": numeric_value,
                        "value_text": None if numeric_value is not None else value,
                    }
                )

    return pd.DataFrame(rows)


def _lookup_stage(stage_df: pd.DataFrame, stage: str) -> pd.Series | None:
    if stage_df.empty or "stage" not in stage_df:
        return None
    rows = stage_df[stage_df["stage"] == stage]
    if rows.empty:
        return None
    return rows.iloc[0]


def metric_cards(
    summary: dict[str, Any],
    per_fold: pd.DataFrame | None,
    per_regime: pd.DataFrame | None,
    diversity: pd.DataFrame | None,
    stage_df: pd.DataFrame,
) -> pd.DataFrame:
    """Small KPI cards for dashboards."""
    rows: list[dict[str, Any]] = []

    def add(card: str, group: str, value: Any, status: str, viz_hint: str, note: str = "") -> None:
        rows.append(
            {
                "card": card,
                "group": group,
                "value": value,
                "status": status,
                "viz_hint": viz_hint,
                "note": note,
            }
        )

    n_folds = int(summary.get("n_folds", 0) or 0)
    n_passed = int(summary.get("n_folds_passed", 0) or 0)
    pass_rate = None if n_folds == 0 else n_passed / n_folds

    add("raw_rows", "data", summary.get("n_rows_raw", 0), "ok", "big_number")
    add("prepared_rows", "data", summary.get("n_rows_prepared", 0), "ok", "big_number")
    add("folds_total", "validation", n_folds, "ok" if n_folds > 0 else "warning", "big_number")
    strategy_metrics_available = bool(summary.get("strategy_metrics_available", False))
    add(
        "strategy_metrics_available",
        "metrics_contract",
        strategy_metrics_available,
        "ok" if strategy_metrics_available else "warning",
        "boolean",
        "" if strategy_metrics_available else "Stage 4 is reporting market diagnostics, not strategy performance.",
    )
    add(
        "metrics_input",
        "metrics_contract",
        summary.get("metrics_input", "unknown"),
        "ok" if strategy_metrics_available else "warning",
        "label",
    )
    guards = summary.get("guard_status", {}) or {}
    pit_status = guards.get("pit_timing", {})
    if isinstance(pit_status, dict):
        pit_value = pit_status.get("status", "unknown")
        pit_note = pit_status.get("reason", "")
    else:
        pit_value = pit_status
        pit_note = ""
    add(
        "pit_timing_guard",
        "guards",
        pit_value,
        "ok" if pit_value == "pass" else "warning",
        "label",
        pit_note,
    )
    cache_status = guards.get("cache_version_fixed", "unknown")
    if isinstance(cache_status, dict):
        cache_value = cache_status.get("status", "unknown")
        cache_note = cache_status.get("reason", "Backtests should use fixed raw data versions for reproducibility.")
    else:
        cache_value = cache_status
        cache_note = "Backtests should use fixed raw data versions for reproducibility."
    add(
        "cache_version_fixed",
        "guards",
        cache_value,
        "ok" if cache_value == "pass" else "warning",
        "label",
        cache_note,
    )
    fold_note = ""
    if pass_rate == 0:
        fold_note = "Zero pass rate usually means the sample is too short or regime diversity thresholds need calibration."
    elif pass_rate is not None and pass_rate < 1:
        fold_note = "Some folds failed the diversity gate; inspect fold_diversity before using fold-level performance."
    add(
        "fold_pass_rate",
        "validation",
        pass_rate,
        "ok" if pass_rate and pass_rate > 0 else "warning",
        "gauge",
        fold_note,
    )

    splitter = _lookup_stage(stage_df, "splitter")
    metrics = _lookup_stage(stage_df, "metrics")
    if splitter is not None and metrics is not None:
        row_delta = int(metrics["row_count"]) - int(splitter["row_count"])
        schema_same = metrics["schema_hash"] == splitter["schema_hash"]
        data_same = metrics["data_hash"] == splitter["data_hash"]
        add("metrics_input_row_delta", "metrics_boundary", row_delta, "ok" if row_delta == 0 else "warning", "delta")
        add("metrics_input_schema_unchanged", "metrics_boundary", schema_same, "ok" if schema_same else "warning", "boolean")
        add("metrics_input_data_unchanged", "metrics_boundary", data_same, "ok" if data_same else "warning", "boolean")

    if per_regime is not None and not per_regime.empty:
        add("regime_count", "regime", len(per_regime), "ok", "big_number")
        if "sharpe" in per_regime:
            worst = per_regime.sort_values("sharpe").iloc[0]
            best = per_regime.sort_values("sharpe").iloc[-1]
            add("worst_regime_sharpe", "regime", float(worst["sharpe"]), "warning" if worst["sharpe"] < 0 else "ok", "bar", str(worst["regime"]))
            add("best_regime_sharpe", "regime", float(best["sharpe"]), "ok" if best["sharpe"] > 0 else "warning", "bar", str(best["regime"]))

    if diversity is not None and not diversity.empty:
        if "conc" in diversity:
            add("mean_fold_concentration", "fold_diversity", float(diversity["conc"].mean()), "warning", "bar")
        if "js" in diversity:
            add("mean_js_divergence", "fold_diversity", float(diversity["js"].mean()), "warning", "bar")

    return pd.DataFrame(rows)


def calibration_flags(
    summary: dict[str, Any],
    per_fold: pd.DataFrame | None,
    per_regime: pd.DataFrame | None,
    diversity: pd.DataFrame | None,
    stage_df: pd.DataFrame,
) -> pd.DataFrame:
    """Human-readable calibration notes with suggested next action."""
    rows: list[dict[str, Any]] = []

    def add(area: str, severity: str, signal: str, interpretation: str, suggested_action: str) -> None:
        rows.append(
            {
                "area": area,
                "severity": severity,
                "signal": signal,
                "interpretation": interpretation,
                "suggested_action": suggested_action,
            }
        )

    n_folds = int(summary.get("n_folds", 0) or 0)
    n_passed = int(summary.get("n_folds_passed", 0) or 0)
    if n_folds > 0 and n_passed == 0:
        add(
            "cross_validation",
            "high",
            "0 folds passed regime diversity gate",
            "Current sample or thresholds do not provide validation folds with enough regime diversity.",
            "Use a longer date range, review regime labels, or loosen concentration/KL/JS thresholds for early calibration.",
        )

    if per_fold is not None and per_fold.empty:
        add(
            "performance",
            "medium",
            "per-fold performance report is empty",
            "No validation fold survived the diversity gate, so fold-level performance is not calibratable yet.",
            "Fix fold diversity first, then compare Sharpe, drawdown, and hit rate by fold.",
        )

    if not summary.get("strategy_metrics_available", False):
        add(
            "metrics_contract",
            "high",
            "strategy P&L/return data is absent",
            "Sharpe, fold breakdowns, and regime breakdowns are market diagnostics, not strategy performance.",
            "Add signal, position, and P&L tables before using Stage 4 as a backtest approval metric.",
        )

    if per_regime is not None and not per_regime.empty and "sharpe" in per_regime and (per_regime["sharpe"] < 0).all():
        add(
            "regime",
            "medium",
            "all observed regime Sharpe values are negative",
            "The simple benchmark return stream is weak across regimes in this smoke window.",
            "Treat this as a pipeline test, not a strategy approval; test longer windows and strategy-specific signals.",
        )

    splitter = _lookup_stage(stage_df, "splitter")
    metrics = _lookup_stage(stage_df, "metrics")
    if splitter is not None and metrics is not None:
        if metrics["data_hash"] == splitter["data_hash"] and metrics["schema_hash"] == splitter["schema_hash"]:
            add(
                "metrics_boundary",
                "info",
                "metrics stage did not mutate input data",
                "Metrics are calculated as reports while the prepared data contract remains stable.",
                "Keep this as a regression test and visualize metric outputs separately from data lineage.",
            )

    if diversity is not None and not diversity.empty and "conc" in diversity and diversity["conc"].max() >= 1.0:
        add(
            "fold_diversity",
            "medium",
            "some folds are single-regime concentrated",
            "A validation fold dominated by one regime can overstate or understate strategy robustness.",
            "Increase sample length or tune fold size and max_concentration.",
        )

    return pd.DataFrame(rows)


def build_summary_report(
    summary: dict[str, Any],
    per_fold: pd.DataFrame | None = None,
    per_regime: pd.DataFrame | None = None,
    diversity: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    snapshots = list(summary.get("audit_snapshots", []))
    stages = stage_comparison(snapshots)
    return {
        "stage_comparison": stages,
        "stage_deltas": stage_deltas(snapshots),
        "column_stats_long": column_stats_long(snapshots),
        "metric_cards": metric_cards(summary, per_fold, per_regime, diversity, stages),
        "calibration_flags": calibration_flags(summary, per_fold, per_regime, diversity, stages),
    }


def _markdown_table(df: pd.DataFrame, columns: list[str], max_rows: int = 12) -> str:
    if df.empty:
        return "_No rows._"
    view = df[columns].head(max_rows).fillna("")
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    rows = ["| " + " | ".join(str(row[c]) for c in columns) + " |" for _, row in view.iterrows()]
    return "\n".join([header, sep, *rows])


def render_markdown(summary: dict[str, Any], tables: dict[str, pd.DataFrame], paths: dict[str, str]) -> str:
    run_id = summary.get("run_id", "unknown")
    stage_df = tables["stage_comparison"]
    delta_df = tables["stage_deltas"]
    card_df = tables["metric_cards"]
    flag_df = tables["calibration_flags"]

    lines = [
        f"# Pipeline Summary Report: {run_id}",
        "",
        "## Run",
        f"- Instrument: {summary.get('instrument')}",
        f"- Family: {summary.get('family')}",
        f"- Date range: {summary.get('date_range')}",
        f"- Raw rows: {summary.get('n_rows_raw')}",
        f"- Prepared rows: {summary.get('n_rows_prepared')}",
        f"- Folds passed: {summary.get('n_folds_passed')} / {summary.get('n_folds')}",
        f"- Metrics input: {summary.get('metrics_input', 'unknown')}",
        f"- Strategy metrics available: {summary.get('strategy_metrics_available', False)}",
        "",
        "## Stage Comparison",
        _markdown_table(
            stage_df,
            [
                "stage",
                "row_count",
                "row_delta_from_previous",
                "total_nulls",
                "schema_changed_from_previous",
                "data_changed_from_previous",
            ],
        ),
        "",
        "## Stage Deltas",
        _markdown_table(
            delta_df,
            ["stage_pair", "row_delta", "schema_changed", "data_changed", "new_nulls_total", "resolved_nulls_total"],
        ),
        "",
        "## Dashboard Cards",
        _markdown_table(card_df, ["group", "card", "value", "status", "note"]),
        "",
        "## Calibration Flags",
        _markdown_table(flag_df, ["area", "severity", "signal", "suggested_action"]),
        "",
        "## Visualization Files",
    ]

    for name, path in paths.items():
        lines.append(f"- {name}: `{path}`")

    lines.append("")
    return "\n".join(lines)


def write_summary_report(
    summary: dict[str, Any],
    per_fold: pd.DataFrame | None = None,
    per_regime: pd.DataFrame | None = None,
    diversity: pd.DataFrame | None = None,
    outputs_dir: str | Path = "outputs",
) -> dict[str, str]:
    """Write Markdown, CSV, and JSON report artifacts for a pipeline run."""
    outputs_dir = Path(outputs_dir)
    report_dir = outputs_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)

    run_id = str(summary.get("run_id", "unknown"))
    tables = build_summary_report(summary, per_fold, per_regime, diversity)

    paths: dict[str, str] = {}
    for name, df in tables.items():
        path = report_dir / f"{name}.csv"
        df.to_csv(path, index=False)
        paths[name] = str(path)

    viz_path = report_dir / "visualization.json"
    with open(viz_path, "w", encoding="utf-8") as f:
        json.dump({name: _records(df) for name, df in tables.items()}, f, indent=2)
    paths["visualization_json"] = str(viz_path)

    md_path = report_dir / "summary_report.md"
    paths["markdown"] = str(md_path)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(summary, tables, paths))

    return paths


def load_run_outputs(run_id: str, outputs_dir: str | Path = "outputs") -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base_dir = Path(outputs_dir)
    run_dir = locate_run_output_dir(run_id, base_dir)

    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        summary_path = base_dir / f"{run_id}_summary.json"

    with open(summary_path, encoding="utf-8") as f:
        summary = json.load(f)

    def read_optional(path: Path) -> pd.DataFrame:
        if not path.exists() or path.stat().st_size == 0:
            return pd.DataFrame()
        try:
            return pd.read_csv(path)
        except pd.errors.EmptyDataError:
            return pd.DataFrame()

    per_fold = read_optional(run_dir / "tables" / "per_fold.csv")
    per_regime = read_optional(run_dir / "tables" / "per_regime.csv")
    diversity = read_optional(run_dir / "tables" / "diversity.csv")

    if per_fold.empty and per_regime.empty and diversity.empty and run_dir == base_dir:
        per_fold = read_optional(base_dir / "perf_report" / f"{run_id}_per_fold.csv")
        per_regime = read_optional(base_dir / "perf_report" / f"{run_id}_per_regime.csv")
        diversity = read_optional(base_dir / "fold_manifest" / f"{run_id}_diversity.csv")
    return summary, per_fold, per_regime, diversity


def write_html_report(
    summary: dict[str, Any],
    stability_results: dict[str, Any],
    per_fold: "pd.DataFrame | None" = None,
    per_regime: "pd.DataFrame | None" = None,
    diversity: "pd.DataFrame | None" = None,
    outputs_dir: str | Path = "outputs",
) -> str:
    """Render a self-contained HTML report and return its path.

    Args:
        summary: pipeline run summary dict (from run_pipeline)
        stability_results: dict with keys adf, kpss, arch, vr, ljung_box,
            jarque_bera, hurst, psi_returns, psi_iv, iv_stats, stage_stats
        per_fold, per_regime, diversity: optional pipeline outputs
        outputs_dir: base output directory

    Returns:
        Absolute path to the written HTML file
    """
    outputs_dir = Path(outputs_dir)
    report_dir = outputs_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)

    run_id = str(summary.get("run_id", "unknown"))
    tables = build_summary_report(summary, per_fold, per_regime, diversity)
    stage_rows = _records(tables["stage_comparison"])
    flag_rows = _records(tables["calibration_flags"])
    regime_rows = _records(per_regime) if per_regime is not None and not per_regime.empty else []
    diversity_rows = _records(diversity) if diversity is not None and not diversity.empty else []

    html_text = _render_static_final_report(
        run_id=run_id,
        summary=summary,
        stability_results=stability_results,
        stage_rows=stage_rows,
        flag_rows=flag_rows,
        regime_rows=regime_rows,
        diversity_rows=diversity_rows,
    )

    out_path = report_dir / "final_report.html"
    out_path.write_text(html_text, encoding="utf-8")
    return str(out_path)


def _h(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _fmt_num(value: Any, digits: int = 4) -> str:
    try:
        if pd.isna(value):
            return "-"
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_pct(value: Any, digits: int = 2) -> str:
    try:
        if pd.isna(value):
            return "-"
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "-"


def _status_class(value: Any) -> str:
    text = str(value).lower()
    if text in {"pass", "ok", "true", "stationary", "stable", "none", "homoskedastic"}:
        return "ok"
    if text in {"fail", "false", "shift", "non_stationary"}:
        return "fail"
    if text in {"warning", "warn", "mixed", "not_checked", "partial"}:
        return "warn"
    return "info"


def _badge(value: Any, label: Any | None = None) -> str:
    text = value if label is None else label
    return f'<span class="status {_status_class(value)}">{_h(text)}</span>'


def _kv(rows: list[tuple[str, str]]) -> str:
    return '<div class="ledger">' + ''.join(
        f'<div>{_h(k)}</div><div>{v}</div>' for k, v in rows
    ) + '</div>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = ''.join(f'<th>{_h(h)}</th>' for h in headers)
    if rows:
        body = ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in row) + '</tr>' for row in rows)
    else:
        body = f'<tr><td colspan="{len(headers)}" class="small">No rows.</td></tr>'
    return f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _box(span: int, body: str) -> str:
    return f'<div class="box span-{span}">{body}</div>'


def _metric(label: str, value: str, note: str = "") -> str:
    suffix = f'<div class="metric-note">{_h(note)}</div>' if note else ""
    return f'<div class="metric-label">{_h(label)}</div><div class="metric-value">{_h(value)}</div>{suffix}'


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _return_distribution_svg(return_stats: dict[str, Any]) -> str:
    mean = _as_float(return_stats.get("mean")) or 0.0
    std = _as_float(return_stats.get("std"))
    if std is None or std <= 0:
        return '<p class="small">Return distribution graph unavailable because daily volatility is missing or zero.</p>'

    skew = _as_float(return_stats.get("skew")) or 0.0
    kurt = _as_float(return_stats.get("kurtosis"))
    excess_kurt = (kurt - 3.0) if kurt is not None else 0.0
    max_gain = _as_float(return_stats.get("max_gain"))
    max_loss = _as_float(return_stats.get("max_loss"))
    lo = min(x for x in [mean - 4.0 * std, max_loss] if x is not None)
    hi = max(x for x in [mean + 4.0 * std, max_gain] if x is not None)
    if lo >= hi:
        lo, hi = mean - 4.0 * std, mean + 4.0 * std

    width, height = 720, 260
    left, right, top, bottom = 56, 20, 18, 42
    plot_w = width - left - right
    plot_h = height - top - bottom
    bins = 37
    dx = (hi - lo) / bins

    def normal_pdf(x: float) -> float:
        z = (x - mean) / std
        return math.exp(-0.5 * z * z) / (std * math.sqrt(2.0 * math.pi))

    points: list[tuple[float, float, float]] = []
    for i in range(bins):
        x = lo + (i + 0.5) * dx
        z = (x - mean) / std
        base = normal_pdf(x)
        # Gram-Charlier moment fit: good for visual diagnostics, not a replacement for raw histograms.
        adjusted = base * (
            1.0
            + (skew / 6.0) * (z**3 - 3.0 * z)
            + (excess_kurt / 24.0) * (z**4 - 6.0 * z**2 + 3.0)
        )
        points.append((x, base, max(0.0, adjusted)))

    y_max = max(max(p[1], p[2]) for p in points) or 1.0

    def sx(x: float) -> float:
        return left + ((x - lo) / (hi - lo)) * plot_w

    def sy(y: float) -> float:
        return top + plot_h - (y / y_max) * plot_h

    bar_gap = 2.0
    bar_w = max(1.0, plot_w / bins - bar_gap)
    bars = []
    for x, _, adjusted in points:
        cx = sx(x)
        y = sy(adjusted)
        bars.append(
            f'<rect class="dist-bar" x="{cx - bar_w / 2:.2f}" y="{y:.2f}" '
            f'width="{bar_w:.2f}" height="{top + plot_h - y:.2f}" />'
        )

    line_points = " ".join(f"{sx(x):.2f},{sy(base):.2f}" for x, base, _ in points)
    zero_x = sx(0.0) if lo <= 0.0 <= hi else None
    mean_x = sx(mean)
    ticks = [lo, mean, hi]
    tick_labels = "".join(
        f'<g><line class="dist-tick" x1="{sx(t):.2f}" y1="{top + plot_h:.2f}" x2="{sx(t):.2f}" y2="{top + plot_h + 4:.2f}" />'
        f'<text class="dist-text" x="{sx(t):.2f}" y="{height - 14}" text-anchor="middle">{_fmt_pct(t, 1)}</text></g>'
        for t in ticks
    )
    zero_line = (
        f'<line class="dist-zero" x1="{zero_x:.2f}" y1="{top}" x2="{zero_x:.2f}" y2="{top + plot_h}" />'
        if zero_x is not None
        else ""
    )

    return f"""
<div class="dist-figure" role="img" aria-label="Return distribution graph">
  <svg class="dist-svg" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet">
    <line class="dist-axis" x1="{left}" y1="{top + plot_h}" x2="{width - right}" y2="{top + plot_h}" />
    <line class="dist-axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" />
    {zero_line}
    {''.join(bars)}
    <polyline class="dist-normal" points="{line_points}" />
    <line class="dist-mean" x1="{mean_x:.2f}" y1="{top}" x2="{mean_x:.2f}" y2="{top + plot_h}" />
    {tick_labels}
    <text class="dist-text" x="{left}" y="12">Density</text>
    <text class="dist-text" x="{width - right}" y="{height - 14}" text-anchor="end">Daily return</text>
  </svg>
  <div class="dist-legend"><span><i class="legend-bar"></i>moment-fit distribution</span><span><i class="legend-line"></i>normal reference</span><span><i class="legend-mean"></i>mean</span></div>
  <p class="small">Moment-fit proxy from summary statistics; use prepared data for a raw-return histogram.</p>
</div>"""


def _guard_value(value: Any) -> tuple[str, str]:
    if isinstance(value, dict):
        return str(value.get("status", "unknown")), str(value.get("reason") or value.get("data_version") or "")
    return str(value or "unknown"), ""


def _render_static_final_report(
    *,
    run_id: str,
    summary: dict[str, Any],
    stability_results: dict[str, Any],
    stage_rows: list[dict[str, Any]],
    flag_rows: list[dict[str, Any]],
    regime_rows: list[dict[str, Any]],
    diversity_rows: list[dict[str, Any]],
) -> str:
    s = summary
    st = stability_results or {}
    instrument = s.get("instrument", "unknown")
    family = s.get("family", "unknown")
    date_range = s.get("date_range") or ["?", "?"]
    date_text = " to ".join(str(x) for x in date_range) if isinstance(date_range, list) else str(date_range)
    strategy_ready = bool(s.get("strategy_metrics_available"))
    metric_input = str(s.get("metrics_input", "unknown"))
    pass_rate = None
    if s.get("n_folds"):
        pass_rate = float(s.get("n_folds_passed", 0)) / float(s.get("n_folds"))

    ret = st.get("return_stats", {}) or {}
    iv = st.get("iv_stats", {}) or {}
    adf = st.get("adf", {}) or {}
    arch = st.get("arch", {}) or {}
    vr = st.get("variance_ratio", {}) or {}
    lb = st.get("ljung_box", {}) or {}
    jb = st.get("jarque_bera", {}) or {}
    hurst_obj = st.get("hurst", {}) or {}
    hurst = hurst_obj.get("hurst") if isinstance(hurst_obj, dict) else hurst_obj

    abstract = (
        "Strategy return data is present; reported metrics are strategy-level outputs."
        if strategy_ready
        else "This run is a market diagnostic ledger, not a strategy-performance approval. "
             "Strategy P&L/return columns are absent, so Stage 4 diagnostics use the market return stream."
    )

    bill = ''.join([
        _box(3, _metric("raw rows", f"{int(s.get('n_rows_raw', 0)):,}", "provider boundary")),
        _box(3, _metric("prepared rows", f"{int(s.get('n_rows_prepared', 0)):,}", "adapter output")),
        _box(3, _metric("fold pass rate", "-" if pass_rate is None else f"{pass_rate * 100:.1f}%", f"{s.get('n_folds_passed', 0)}/{s.get('n_folds', 0)} folds")),
        _box(3, _metric("metrics input", metric_input, "strategy returns" if strategy_ready else "market diagnostic")),
        _box(6, '<h2>Run bill</h2>' + _kv([
            ("Instrument", _h(instrument)),
            ("Family", _h(family)),
            ("Date range", _h(date_text)),
            ("Output directory", f'<span class="mono">{_h(s.get("output_dir", "-"))}</span>'),
            ("Cache mode", f'<span class="mono">{_h(s.get("data_cache_mode", "-"))}</span>'),
        ])),
        _box(6, '<h2>Return ledger</h2>' + _kv([
            ("Trading days", f'<span class="num">{_h(st.get("trading_days", "-"))}</span>'),
            ("Mean return/day", f'<span class="num">{_fmt_pct(ret.get("mean"), 4)}</span>'),
            ("Daily volatility", f'<span class="num">{_fmt_pct(ret.get("std"), 4)}</span>'),
            ("Max gain / loss", f'<span class="num">{_fmt_pct(ret.get("max_gain"), 2)} / {_fmt_pct(ret.get("max_loss"), 2)}</span>'),
            ("IV null rate", f'<span class="num">{_fmt_num(iv.get("null_pct"), 2)}%</span>' if iv else '<span class="num">-</span>'),
        ])),
    ])

    guards = s.get("guard_status", {}) or {}
    pit_value, pit_note = _guard_value(guards.get("pit_timing"))
    pnl_value, _ = _guard_value(guards.get("strategy_pnl_present"))
    cache_value, cache_note = _guard_value(guards.get("cache_version_fixed"))
    guard_section = ''.join([
        _box(12, '<h2>Guard status</h2><p class="small">Contract-level controls. A warning here means the report should be read with the stated limitation.</p>'),
        _box(4, f'<h3>PIT timing</h3>{_badge(pit_value)}<p class="small">{_h(pit_note or "as_of_date / available_at / decision_time contract")}</p>'),
        _box(4, f'<h3>Strategy P&amp;L</h3>{_badge(pnl_value)}<p class="small">{_h("strategy return stream present" if strategy_ready else "missing signal/position/P&L layer")}</p>'),
        _box(4, f'<h3>Cache version</h3>{_badge(cache_value)}<p class="small">{_h(cache_note or "fixed raw data version preferred")}</p>'),
    ])

    tests = [
        ["ADF + KPSS", "stationarity", _badge(adf.get("consensus", "unknown")), f'ADF p={_fmt_num(adf.get("adf_pval"))}; KPSS p={_fmt_num(adf.get("kpss_pval"))}', _h(adf.get("consensus", "unknown"))],
        ["ARCH-LM", "volatility clustering", _badge("warning" if str(arch.get("has_arch_effects")).lower() == "true" else "pass", "ARCH present" if str(arch.get("has_arch_effects")).lower() == "true" else "none"), f'p={_fmt_num(arch.get("lm_pval"))}', "use vol/regime controls" if str(arch.get("has_arch_effects")).lower() == "true" else "no ARCH flag"],
        ["Variance ratio", "random walk", _badge(vr.get("interpretation", "unknown")), f'VR={_fmt_num(vr.get("vr_stat"))}; z={_fmt_num(vr.get("z_stat"), 3)}', _h(vr.get("interpretation", "not measured"))],
        ["Ljung-Box", "autocorrelation", _badge("warning" if str(lb.get("has_autocorr")).lower() == "true" else "pass", "autocorr" if str(lb.get("has_autocorr")).lower() == "true" else "none"), f'p={_fmt_num(lb.get("lb_pval"))}', "review purge/embargo" if str(lb.get("has_autocorr")).lower() == "true" else "no autocorrelation flag"],
        ["Jarque-Bera", "normality", _badge("pass" if str(jb.get("is_normal")).lower() == "true" else "info", "normal" if str(jb.get("is_normal")).lower() == "true" else "fat tails"), f'p={_fmt_num(jb.get("jb_pval"))}; skew={_fmt_num(jb.get("skew"), 3)}; kurt={_fmt_num(jb.get("kurtosis"), 3)}', "prefer tail-aware risk metrics"],
        ["Hurst exponent", "memory", _badge("pass", _fmt_num(hurst, 3)), f'H={_fmt_num(hurst, 3)}', "near random walk" if hurst is not None and 0.45 <= float(hurst) <= 0.65 else "directional memory present"],
    ]
    test_section = _box(12, '<h2>Statistical test ledger</h2>' + _table(["Test", "Question", "Result", "Statistic", "Reading"], [[_h(a), _h(b), c, _h(d), _h(e)] for a, b, c, d, e in tests]))

    lineage_rows = [
        [
            _h(r.get("stage")),
            f'<span class="num">{int(r.get("row_count", 0)):,}</span>',
            f'<span class="num">{_h("-" if r.get("row_delta_from_previous") is None else int(r.get("row_delta_from_previous", 0)))}</span>',
            f'<span class="num">{int(r.get("total_nulls", 0)):,}</span>',
            _badge("warning" if r.get("schema_changed_from_previous") else "pass", "changed" if r.get("schema_changed_from_previous") else "stable"),
            _badge("warning" if r.get("data_changed_from_previous") else "pass", "changed" if r.get("data_changed_from_previous") else "stable"),
        ]
        for r in stage_rows
    ]
    lineage = _box(12, '<h2>Data lineage</h2><p class="small">Rows and schema hashes by processing stage.</p>' + _table(["Stage", "Rows", "Delta rows", "Nulls", "Schema", "Data"], lineage_rows))

    psi_returns = st.get("psi_returns", {}) or {}
    worst_psi = psi_returns.get("worst", psi_returns) if isinstance(psi_returns, dict) else {}
    psi_threshold = psi_returns.get("psi_threshold", st.get("psi_threshold", 0.25)) if isinstance(psi_returns, dict) else 0.25
    psi_value = worst_psi.get("psi") if isinstance(worst_psi, dict) else None
    regime_table_rows = [
        [_h(r.get("regime")), f'<span class="num">{_fmt_num(r.get("sharpe"), 3)}</span>', f'<span class="num">{_fmt_num(r.get("sortino"), 3)}</span>', f'<span class="num">{_fmt_pct(r.get("max_dd"), 2)}</span>', f'<span class="num">{_h(r.get("n_obs", r.get("n", "-")))}</span>']
        for r in regime_rows[:12]
    ]
    diversity_table_rows = [
        [f'<span class="num">{_h(r.get("fold"))}</span>', _badge("pass" if r.get("pass") else "fail"), f'<span class="num">{_fmt_num(r.get("conc"), 3)}</span>', f'<span class="num">{_fmt_num(r.get("kl"), 3)}</span>', f'<span class="num">{_fmt_num(r.get("js"), 3)}</span>']
        for r in diversity_rows[:12]
    ]
    distribution = ''.join([
        _box(5, '<h2>Distribution shift</h2>' + _kv([
            ("Return PSI", f'<span class="num">{_fmt_num(psi_value, 4)}</span> {_badge("pass" if psi_value is not None and float(psi_value) <= float(psi_threshold) else "fail" if psi_value is not None else "unknown", "below threshold" if psi_value is not None and float(psi_value) <= float(psi_threshold) else "above threshold" if psi_value is not None else "unknown")}'),
            ("PSI threshold", f'<span class="num">{_fmt_num(psi_threshold, 3)}</span>'),
            ("KS statistic", f'<span class="num">{_fmt_num(worst_psi.get("ks_stat") if isinstance(worst_psi, dict) else None, 4)}</span>'),
            ("Worst fold", f'<span class="num">{_h(worst_psi.get("fold", "-") if isinstance(worst_psi, dict) else "-")}</span>'),
        ])),
        _box(7, '<h2>Regime performance</h2><p class="small">First 12 regime rows.</p>' + _table(["Regime", "Sharpe", "Sortino", "Max DD", "N"], regime_table_rows)),
        _box(12, '<h2>Return distribution</h2>' + _return_distribution_svg(ret)),
        _box(12, '<h2>Fold diversity gate</h2>' + _table(["Fold", "Pass", "Concentration", "KL", "JS"], diversity_table_rows)),
    ])

    flags = _box(12, '<h2>Calibration flags</h2>' + _table(
        ["Severity", "Area", "Signal", "Suggested action"],
        [[_badge(f.get("severity", "info")), _h(f.get("area", "")), _h(f.get("signal", "")), _h(f.get("suggested_action", ""))] for f in flag_rows]
    ))

    artifact_entries = []
    for source in (s.get("artifacts", {}) or {}, s.get("summary_report", {}) or {}):
        if isinstance(source, dict):
            artifact_entries.extend((k, v) for k, v in source.items() if v)
    if s.get("html_report"):
        artifact_entries.append(("html_report", s["html_report"]))
    artifacts = _box(12, '<h2>Artifact index</h2><p class="small">Run-scoped output paths. The folder name is the primary identifier.</p>' + _table(
        ["Artifact", "Path"],
        [[_h(k), f'<span class="mono">{_h(v)}</span>'] for k, v in artifact_entries]
    ))

    css = """
  *, *::before, *::after { box-sizing: border-box; }
  :root { --paper:#fbfaf7; --ink:#161513; --muted:#6d6860; --rule:#d8d1c3; --rule-strong:#8f8778; --wash:#f1eee7; --ok:#1f6f50; --warn:#986b13; --fail:#9b2f2f; --info:#315f8c; --mono:"Cascadia Mono","SF Mono",Consolas,monospace; --serif:"Cambria","Georgia","Times New Roman",serif; --sans:"Segoe UI",Arial,sans-serif; }
  body { margin:0; background:#e7e2d7; color:var(--ink); font-family:var(--serif); font-size:13px; line-height:1.42; }
  .sheet { width:min(1120px, calc(100vw - 28px)); margin:18px auto; background:var(--paper); border:1px solid var(--rule); box-shadow:0 18px 50px rgba(60,46,21,.16); }
  .inner { padding:28px 34px 34px; }
  .masthead { border-bottom:2px solid var(--ink); padding-bottom:14px; display:grid; grid-template-columns:1fr auto; gap:20px; align-items:end; }
  .label-top,.metric-label,h3,th,.footer { font-family:var(--sans); }
  .label-top,.metric-label,h3,th { text-transform:uppercase; letter-spacing:.06em; color:var(--muted); font-size:10px; }
  h1 { font-size:28px; line-height:1.05; margin:5px 0 0; }
  h2 { font-size:15px; margin:0 0 8px; padding-bottom:5px; border-bottom:1px solid var(--rule); }
  h3 { margin:0 0 6px; }
  .small,.metric-note { font-size:11px; color:var(--muted); }
  .run-stamp { font-family:var(--mono); font-size:11px; text-align:right; color:var(--muted); white-space:nowrap; }
  .abstract { margin-top:13px; padding:10px 12px; background:var(--wash); border-left:4px solid var(--rule-strong); font-size:12px; }
  .toc { font-family:var(--sans); display:flex; gap:12px; flex-wrap:wrap; margin-top:14px; padding-bottom:10px; border-bottom:1px solid var(--rule); }
  .toc a { color:var(--ink); text-decoration:none; border-bottom:1px dotted var(--rule-strong); }
  .grid { display:grid; grid-template-columns:repeat(12, 1fr); gap:12px; margin-top:16px; }
  .box { border:1px solid var(--rule); background:rgba(255,255,255,.28); padding:11px 12px; min-width:0; overflow-wrap:anywhere; }
  .span-3{grid-column:span 3}.span-4{grid-column:span 4}.span-5{grid-column:span 5}.span-6{grid-column:span 6}.span-7{grid-column:span 7}.span-12{grid-column:span 12}
  .metric-value { font-family:var(--mono); font-size:20px; font-weight:700; margin-top:2px; overflow-wrap:anywhere; }
  table { width:100%; border-collapse:collapse; table-layout:fixed; }
  th { text-align:left; border-bottom:1px solid var(--rule-strong); padding:5px 6px; }
  td { border-bottom:1px solid var(--rule); padding:5px 6px; vertical-align:top; overflow-wrap:anywhere; word-break:break-word; }
  tr:last-child td { border-bottom:0; }
  .num,.mono { font-family:var(--mono); overflow-wrap:anywhere; word-break:break-word; }
  .ledger { display:grid; grid-template-columns:190px minmax(0,1fr); border-top:1px solid var(--rule); }
  .ledger div { padding:5px 6px; border-bottom:1px solid var(--rule); min-width:0; overflow-wrap:anywhere; }
  .ledger div:nth-child(odd) { color:var(--muted); font-family:var(--sans); font-size:11px; }
  .status { display:inline-block; font-family:var(--sans); font-size:10px; font-weight:700; border:1px solid currentColor; padding:1px 6px; border-radius:2px; white-space:nowrap; }
  .ok{color:var(--ok)}.warn{color:var(--warn)}.fail{color:var(--fail)}.info{color:var(--info)}
  .dist-figure { margin-top:3px; }
  .dist-svg { display:block; width:100%; height:auto; border:1px solid var(--rule); background:#fffdf8; }
  .dist-bar { fill:#c9b98d; opacity:.62; }
  .dist-normal { fill:none; stroke:#315f8c; stroke-width:2; stroke-dasharray:6 5; }
  .dist-mean { stroke:#9b2f2f; stroke-width:1.5; }
  .dist-zero { stroke:#8f8778; stroke-width:1; stroke-dasharray:2 4; }
  .dist-axis,.dist-tick { stroke:#6d6860; stroke-width:1; }
  .dist-text { fill:#6d6860; font-family:var(--mono); font-size:10px; }
  .dist-legend { display:flex; flex-wrap:wrap; gap:12px; margin-top:6px; font-family:var(--sans); font-size:11px; color:var(--muted); }
  .dist-legend i { display:inline-block; width:16px; height:8px; margin-right:5px; vertical-align:middle; }
  .legend-bar { background:#c9b98d; opacity:.62; }
  .legend-line { border-top:2px dashed #315f8c; height:0 !important; }
  .legend-mean { border-top:2px solid #9b2f2f; height:0 !important; }
  .footer { margin-top:18px; padding-top:8px; border-top:1px solid var(--rule); font-size:10px; color:var(--muted); display:flex; justify-content:space-between; gap:12px; }
  @media(max-width:820px){.inner{padding:20px 16px 24px}.masthead{grid-template-columns:1fr}.run-stamp{text-align:left;white-space:normal}.span-3,.span-4,.span-5,.span-6,.span-7{grid-column:span 12}}
  @media print{body{background:white}.sheet{width:100%;margin:0;box-shadow:none;border:0}.inner{padding:18mm}}
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Janus Final Report - {_h(run_id)}</title>
<style>{css}</style>
</head>
<body>
<main class="sheet"><div class="inner">
<header class="masthead"><div><div class="label-top">Janus Quant Pipeline</div><h1>Final Results Ledger: {_h(instrument)}</h1><div class="small">{_h(family)} · {_h(date_text)}</div></div><div class="run-stamp">Run ID<br><strong>{_h(run_id)}</strong></div></header>
<section class="abstract"><strong>Result status.</strong> {_h(abstract)}</section>
<nav class="toc"><a href="#bill">Result bill</a><a href="#guards">Guard status</a><a href="#tests">Statistical tests</a><a href="#lineage">Data lineage</a><a href="#distribution">Distribution</a><a href="#flags">Flags</a><a href="#artifacts">Artifacts</a></nav>
<section id="bill" class="grid">{bill}</section>
<section id="guards" class="grid">{guard_section}</section>
<section id="tests" class="grid">{test_section}</section>
<section id="lineage" class="grid">{lineage}</section>
<section id="distribution" class="grid">{distribution}</section>
<section id="flags" class="grid">{flags}</section>
<section id="artifacts" class="grid">{artifacts}</section>
<footer class="footer"><span>Generated by Janus reporting module. Values are rendered from pipeline summary artifacts.</span><span>{_h(run_id)}</span></footer>
</div></main>
</body>
</html>
"""


_LEGACY_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pipeline Report — __RUN_ID__</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #fff; --bg2: #f7f7f5; --bg3: #f0eeea;
    --text: #1a1a18; --text2: #6b6b67; --text3: #9b9b97;
    --border: rgba(0,0,0,0.10); --border2: rgba(0,0,0,0.18);
    --green: #1D9E75; --green-bg: #E1F5EE; --green-text: #085041;
    --amber: #BA7517; --amber-bg: #FAEEDA; --amber-text: #633806;
    --red: #A32D2D; --red-bg: #FCEBEB; --red-text: #501313;
    --blue: #185FA5; --blue-bg: #E6F1FB; --blue-text: #042C53;
    --radius: 10px; --radius-sm: 6px;
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --mono: "SF Mono", "Fira Code", "Cascadia Code", monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1c1c1a; --bg2: #252523; --bg3: #2e2e2b;
      --text: #e8e8e4; --text2: #a0a09c; --text3: #6b6b67;
      --border: rgba(255,255,255,0.10); --border2: rgba(255,255,255,0.20);
      --green: #5DCAA5; --green-bg: #04342C; --green-text: #9FE1CB;
      --amber: #EF9F27; --amber-bg: #412402; --amber-text: #FAC775;
      --red: #F09595; --red-bg: #501313; --red-text: #F7C1C1;
      --blue: #85B7EB; --blue-bg: #042C53; --blue-text: #B5D4F4;
    }
  }
  body { font-family: var(--font); background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.6; }
  .page { max-width: 1060px; margin: 0 auto; padding: 32px 24px; }
  h1 { font-size: 22px; font-weight: 600; }
  h2 { font-size: 15px; font-weight: 600; margin-bottom: 12px; }
  .meta { font-size: 12px; color: var(--text2); margin-top: 4px; }
  .kpi-grid { display: grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap: 10px; margin: 20px 0; }
  .kpi { background: var(--bg2); border-radius: var(--radius-sm); padding: 12px; text-align: center; }
  .kpi-label { font-size: 11px; color: var(--text2); margin-bottom: 5px; }
  .kpi-value { font-size: 22px; font-weight: 600; }
  .tabs { display: flex; border-bottom: 1px solid var(--border); margin-bottom: 20px; gap: 0; }
  .tab { background: none; border: none; border-bottom: 2px solid transparent; padding: 9px 18px; font-size: 13px; cursor: pointer; color: var(--text2); font-family: var(--font); }
  .tab.on { color: var(--text); border-bottom-color: var(--text); font-weight: 500; }
  .panel { display: none; } .panel.on { display: block; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .card { background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; }
  .row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
  .row:last-child { border-bottom: none; }
  .label { color: var(--text2); }
  .val { font-family: var(--mono); font-size: 12px; }
  .badge { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 500; }
  .ok   { background: var(--green-bg); color: var(--green-text); }
  .warn { background: var(--amber-bg); color: var(--amber-text); }
  .fail { background: var(--red-bg);   color: var(--red-text);   }
  .info { background: var(--blue-bg);  color: var(--blue-text);  }
  .note { font-size: 11px; padding: 6px 10px; border-radius: var(--radius-sm); margin-top: 10px; }
  .bar-wrap { background: var(--bg3); border-radius: 3px; height: 6px; overflow: hidden; margin-top: 8px; }
  .bar-fill { height: 6px; border-radius: 3px; }
  .bar-labels { display: flex; justify-content: space-between; font-size: 10px; color: var(--text3); margin-top: 3px; }
  .stage-flow { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }
  .stage-box { background: var(--bg2); border-radius: var(--radius-sm); padding: 10px 14px; text-align: center; min-width: 110px; }
  .stage-name { font-size: 11px; color: var(--text2); margin-bottom: 4px; }
  .stage-count { font-size: 16px; font-weight: 600; }
  .stage-sub { font-size: 10px; color: var(--text2); margin-top: 2px; }
  .arrow { color: var(--text3); font-size: 18px; }
  .flag-row { border-left: 3px solid; padding: 10px 14px; border-radius: 0 var(--radius-sm) var(--radius-sm) 0; margin-bottom: 10px; }
  .flag-high   { border-color: var(--red);   background: var(--red-bg); }
  .flag-medium { border-color: var(--amber); background: var(--amber-bg); }
  .flag-info   { border-color: var(--blue);  background: var(--blue-bg); }
  .flag-signal { font-weight: 600; font-size: 12px; margin-bottom: 4px; }
  .flag-action { font-size: 11px; color: var(--text2); margin-top: 4px; }
  .chart-wrap { position: relative; width: 100%; height: 200px; margin-top: 14px; }
</style>
</head>
<body>
<div class="page">

<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
  <div>
    <h1 id="title">Pipeline Report</h1>
    <div class="meta" id="meta"></div>
  </div>
  <div style="font-size:11px;color:var(--text3);padding-top:6px" id="run-id-label"></div>
</div>

<div class="kpi-grid" id="kpi-grid"></div>

<div class="tabs">
  <button class="tab on" onclick="showTab('pipeline')">Pipeline flow</button>
  <button class="tab" onclick="showTab('stability')">Stability tests</button>
  <button class="tab" onclick="showTab('distribution')">Distribution</button>
  <button class="tab" onclick="showTab('flags')">Flags</button>
</div>

<div id="panel-pipeline" class="panel on"></div>
<div id="panel-stability" class="panel"></div>
<div id="panel-distribution" class="panel"></div>
<div id="panel-flags" class="panel"></div>

</div>

<script>
const D = __DATA_JSON__;
const isDark = matchMedia('(prefers-color-scheme: dark)').matches;
const gc = isDark ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.06)';
const tc = isDark ? '#888' : '#aaa';

function showTab(id) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('on'));
  document.getElementById('panel-' + id).classList.add('on');
  event.target.classList.add('on');
}

function badge(cls, txt) { return `<span class="badge ${cls}">${txt}</span>`; }
function row(label, valHtml) { return `<div class="row"><span class="label">${label}</span><span class="val">${valHtml}</span></div>`; }
function note(cls, txt) { return `<div class="note ${cls}">${txt}</div>`; }
function barWrap(pct, color, lo, hi) {
  return `<div class="bar-wrap"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
          <div class="bar-labels"><span>${lo}</span><span>${hi}</span></div>`;
}
function card(title, inner) { return `<div class="card"><h2>${title}</h2>${inner}</div>`; }
function pval(v) { return v < 0.001 ? v.toExponential(2) : v.toFixed(4); }

// ── Header ──────────────────────────────────────────────
const s = D.summary;
document.getElementById('title').textContent =
  `Pipeline Report — ${s.instrument || '?'} (${s.family || '?'})`;
document.getElementById('meta').textContent =
  `${(s.date_range||['?','?']).join(' → ')}  ·  ${Number(s.n_rows_raw||0).toLocaleString()} raw rows`;
document.getElementById('run-id-label').textContent = D.run_id;

// ── KPI cards ────────────────────────────────────────────
const st = D.stability || {};
const adf = st.adf || {};
const arch = st.arch || {};
const jb = st.jarque_bera || {};
const hurstObj = (st.hurst && typeof st.hurst === 'object') ? st.hurst : {};
const hurst = typeof st.hurst === 'number' ? st.hurst : (typeof hurstObj.hurst === 'number' ? hurstObj.hurst : null);
const psiRet = st.psi_returns || {};
const ivStats = st.iv_stats || {};

const kpis = [
  { label: 'trading days', value: st.trading_days || '—' },
  { label: 'null IV', value: (ivStats.null_pct != null ? ivStats.null_pct.toFixed(1) + '%' : '—'), color: ivStats.null_pct === 0 ? 'var(--green)' : 'var(--amber)' },
  { label: 'stationarity', value: adf.consensus || '—', color: adf.consensus === 'stationary' ? 'var(--green)' : 'var(--red)' },
  { label: 'ARCH effects', value: arch.has_arch_effects ? 'YES' : (arch.has_arch_effects === false ? 'NO' : '—'), color: arch.has_arch_effects ? 'var(--amber)' : 'var(--green)' },
  { label: 'PSI (returns)', value: psiRet.psi != null ? psiRet.psi.toFixed(3) : '—', color: (psiRet.psi||0) < 0.25 ? 'var(--green)' : 'var(--red)' },
];
document.getElementById('kpi-grid').innerHTML = kpis.map(k =>
  `<div class="kpi"><div class="kpi-label">${k.label}</div>
   <div class="kpi-value" style="${k.color ? 'color:' + k.color : ''}">${k.value}</div></div>`
).join('');

// ── Panel 1: Pipeline flow ────────────────────────────────
(function() {
  const stages = D.stages || [];
  const stageColor = s => s.schema_changed_from_previous ? '#185FA5' : 'var(--text2)';

  let flowHtml = '<div class="stage-flow">';
  stages.forEach((s, i) => {
    if (i > 0) flowHtml += '<div class="arrow">→</div>';
    const delta = s.row_delta_from_previous != null ? (s.row_delta_from_previous === 0 ? '' : ` (${s.row_delta_from_previous > 0 ? '+' : ''}${s.row_delta_from_previous})`) : '';
    const schemaNote = s.schema_changed_from_previous ? '<br><span style="font-size:9px;color:var(--blue)">schema +cols</span>' : '';
    flowHtml += `<div class="stage-box">
      <div class="stage-name">${s.stage}</div>
      <div class="stage-count">${Number(s.row_count||0).toLocaleString()}</div>
      <div class="stage-sub">${delta}${schemaNote}</div>
    </div>`;
  });
  flowHtml += '</div>';

  const sampleStage = D.stages.find(s => s.stage === 'validators') || {};
  const ivSurf = st.iv_stats || {};
  const nullPct = ivSurf.null_pct != null ? ivSurf.null_pct.toFixed(1) + '%' : '—';

  const qualityCards = [];
  if (Object.keys(ivSurf).length > 0 && ivSurf.null_pct != null) {
    qualityCards.push(card('IV quality (after /100 normalize)', `
    ${row('null', `${nullPct} &nbsp;${nullPct === '0.0%' ? badge('ok','complete') : badge('warn','partial')}`)}
    ${row('min', (ivSurf.min != null ? (ivSurf.min*100).toFixed(1) + '%' : '—'))}
    ${row('median ATM', (ivSurf.median != null ? (ivSurf.median*100).toFixed(1) + '%' : '—'))}
    ${row('mean', (ivSurf.mean != null ? (ivSurf.mean*100).toFixed(1) + '%' : '—'))}
    ${row('p95', (ivSurf.p95 != null ? (ivSurf.p95*100).toFixed(1) + '%' : '—'))}
    ${row('max', (ivSurf.max != null ? (ivSurf.max*100).toFixed(1) + '%' : '—'))}
    ${row('&gt;200% (deep OTM noise)', ivSurf.deep_otm_count != null ? ivSurf.deep_otm_count.toLocaleString() + ' rows' : '—')}
    ${note('info', 'IV diagnostics measured from available IV rows')}
  `));
  }

  if (ivSurf.delta_mean != null) {
    qualityCards.push(card('Delta quality', `
    ${row('range', '0.000 – 1.000')}
    ${row('mean', ivSurf.delta_mean.toFixed(3))}
    ${note('info', 'Delta diagnostics measured from computed option Greeks')}
  `));
  }

  document.getElementById('panel-pipeline').innerHTML =
    flowHtml + (qualityCards.length ? '<div class="grid2" style="margin-top:14px">' + qualityCards.join('') + '</div>' : '');
})();

// ── Panel 2: Stability tests ──────────────────────────────
(function() {
  const vr = st.variance_ratio || {};
  const lb = st.ljung_box || {};

  function testCard(title, subtitle, badgeCls, badgeTxt, rows, noteText, noteCls) {
    return `<div class="card">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px">
        <div><h2 style="margin-bottom:2px">${title}</h2><div style="font-size:11px;color:var(--text2)">${subtitle}</div></div>
        ${badge(badgeCls, badgeTxt)}
      </div>
      ${rows.map(r => row(r[0], r[1])).join('')}
      ${note(noteCls, noteText)}
    </div>`;
  }

  const adfConsensus = adf.consensus || 'unknown';
  const adfBadge = adfConsensus === 'stationary' ? 'ok' : adfConsensus === 'non_stationary' ? 'fail' : 'warn';

  const vrInterp = vr.interpretation || '—';
  const vrKind = vr.input_kind || 'price_level';
  const vrBadge = vrInterp === 'random_walk' ? 'ok' : vrInterp === 'mean_reverting' ? 'info' : 'warn';
  const vrPct = vr.vr_stat != null ? Math.min(vr.vr_stat * 100, 200) : 50;

  const hurstVal = hurst != null ? hurst.toFixed(3) : '—';
  const hurstBadge = hurst == null ? 'info' : hurst < 0.45 ? 'info' : hurst > 0.65 ? 'warn' : 'ok';
  const hurstLabel = hurst == null ? '—' : hurst < 0.45 ? 'mean-reverting' : hurst > 0.65 ? 'trending' : 'near random walk';

  const archKnown = arch.has_arch_effects != null;
  const lbKnown = lb.has_autocorr != null;
  const jbKnown = jb.is_normal != null;
  const lbBadge = !lbKnown ? 'info' : lb.has_autocorr ? 'warn' : 'ok';
  const jbBadge = !jbKnown ? 'info' : jb.is_normal ? 'ok' : 'info';

  const html = `<div class="grid2">
    ${testCard(
      'ADF + KPSS — Stationarity', 'H0(ADF): unit root · H0(KPSS): stationary',
      adfBadge, adfConsensus.toUpperCase(),
      [
        ['ADF statistic', adf.adf_stat != null ? adf.adf_stat.toFixed(3) : '—'],
        ['ADF p-value', adf.adf_pval != null ? pval(adf.adf_pval) + ' ' + (adf.adf_pval < 0.05 ? badge('ok','reject H0') : badge('warn','fail to reject')) : '—'],
        ['KPSS statistic', adf.kpss_stat != null ? adf.kpss_stat.toFixed(3) : '—'],
        ['KPSS p-value', adf.kpss_pval != null ? '≥' + pval(adf.kpss_pval) + ' ' + (adf.kpss_pval > 0.05 ? badge('ok','fail to reject') : badge('warn','reject')) : '—'],
        ['consensus', badge(adfBadge, adfConsensus)],
      ],
      adfConsensus === 'stationary'
        ? '✓ ADF and KPSS agree — return series is stationary'
        : '⚠ Mixed signals — check for structural breaks before modelling',
      adfConsensus === 'stationary' ? 'ok' : 'warn'
    )}
    ${testCard(
      'ARCH-LM — Volatility clustering', 'H0: homoskedastic (no ARCH effects)',
      !archKnown ? 'info' : arch.has_arch_effects ? 'info' : 'ok',
      !archKnown ? 'UNKNOWN' : arch.has_arch_effects ? 'ARCH PRESENT' : 'HOMOSKEDASTIC',
      [
        ['LM statistic', arch.lm_stat != null ? arch.lm_stat.toFixed(2) : '—'],
        ['p-value', arch.lm_pval != null ? pval(arch.lm_pval) + ' ' + (arch.has_arch_effects ? badge('fail','reject H0') : badge('ok','pass')) : '—'],
        ['ARCH effects', arch.has_arch_effects != null ? badge(arch.has_arch_effects ? 'fail':'ok', arch.has_arch_effects ? 'YES':'NO') : '—'],
      ],
      !archKnown
        ? 'ARCH-LM not measured for this series'
        : arch.has_arch_effects
        ? 'Vol clustering detected — consider volatility-regime or residual diagnostics.'
        : '✓ No ARCH effects — residuals are homoskedastic',
      !archKnown ? 'info' : arch.has_arch_effects ? 'info' : 'ok'
    )}
    ${testCard(
      'Variance Ratio — Random walk', `VR(2) on ${vrKind.replace('_',' ')}: 1.0 = random walk`,
      vrBadge, vrInterp.replace('_', ' ').toUpperCase(),
      [
        ['VR statistic', vr.vr_stat != null ? vr.vr_stat.toFixed(4) : '—'],
        ['Z statistic', vr.z_stat != null ? vr.z_stat.toFixed(3) : '—'],
        ['interpretation', badge(vrBadge, vrInterp.replace('_',' '))],
      ],
      (vrInterp === 'random_walk'
        ? (vrKind === 'return_series'
          ? '✓ Aggregated returns are consistent with random-walk increments'
          : '✓ Price path consistent with random walk — no exploitable autocorrelation in levels')
        : vrInterp === 'mean_reverting'
          ? (vrKind === 'return_series'
            ? 'VR < 0.9 — negative autocorrelation in return increments.'
            : 'VR < 0.9 — mean reversion in price levels.')
          : 'VR > 1.1 — momentum present.') +
        `${barWrap(Math.min(vrPct, 100), '#378ADD', '0 (mean-rev)', '1.0 (rw)')}`,
      vrBadge
    )}
    ${testCard(
      'Ljung-Box — Autocorrelation', 'H0: no autocorrelation up to lag 10',
      lbBadge, !lbKnown ? 'UNKNOWN' : lb.has_autocorr ? 'AUTOCORR' : 'NONE',
      [
        ['LB statistic (lag 10)', lb.lb_stat != null ? lb.lb_stat.toFixed(3) : '—'],
        ['p-value', lb.lb_pval != null ? pval(lb.lb_pval) + ' ' + (lb.has_autocorr ? badge('warn','reject H0') : badge('ok','pass')) : '—'],
        ['autocorrelation', lb.has_autocorr != null ? badge(lb.has_autocorr ? 'warn':'ok', lb.has_autocorr ? 'YES (mild)':'NO') : '—'],
      ],
      !lbKnown
        ? 'Ljung-Box not measured for this series'
        : lb.has_autocorr
        ? 'Mild autocorrelation — likely driven by vol clustering. Purge/embargo window should cover.'
        : '✓ No significant autocorrelation in returns',
      !lbKnown ? 'info' : lb.has_autocorr ? 'warn' : 'ok'
    )}
    ${testCard(
      'Jarque-Bera — Normality', 'H0: normally distributed',
      jbBadge, !jbKnown ? 'UNKNOWN' : jb.is_normal ? 'NORMAL' : 'FAT TAILS',
      [
        ['JB statistic', jb.jb_stat != null ? jb.jb_stat.toFixed(2) : '—'],
        ['p-value', jb.jb_pval != null ? pval(jb.jb_pval) + ' ' + (jb.is_normal ? badge('ok','pass') : badge('info','reject H0')) : '—'],
        ['skewness', jb.skew != null ? jb.skew.toFixed(3) + (Math.abs(jb.skew) > 0.5 ? ' ' + badge('warn','skewed') : '') : '—'],
        ['excess kurtosis', jb.kurtosis != null ? jb.kurtosis.toFixed(3) + (jb.kurtosis > 3 ? ' ' + badge('info','fat tails') : '') : '—'],
      ],
      !jbKnown
        ? 'Jarque-Bera not measured for this series'
        : jb.is_normal
        ? '✓ Returns are approximately normal'
        : 'Fat tails + skew — use CVaR/ES for risk sizing, not σ alone',
      !jbKnown ? 'info' : jb.is_normal ? 'ok' : 'info'
    )}
    ${testCard(
      'Hurst Exponent', 'R/S analysis · H ≈ 0.5 = random walk',
      hurstBadge, hurstLabel.toUpperCase(),
      [
        ['H', hurstVal + (hurst != null ? ' ' + badge(hurstBadge, hurstLabel) : '')],
      ],
      (hurst != null
        ? (hurst < 0.45 ? 'H < 0.5 — anti-persistent. Mean-reversion strategies may work.'
           : hurst > 0.65 ? 'H > 0.65 — persistent trend. Momentum strategies may work.'
           : '✓ H ≈ 0.5 — return series behaves as random walk')
        : 'Hurst not computed') +
        (hurst != null ? `${barWrap(hurst * 100, '#1D9E75', '0 (mean-rev)', '0.5 — 1.0 (trend)')}` : ''),
      hurstBadge
    )}
  </div>`;

  document.getElementById('panel-stability').innerHTML = html;
})();

// ── Panel 3: Distribution ─────────────────────────────────
(function() {
  const psiIV = st.psi_iv || {};
  const retStats = st.return_stats || {};

  function psiCard(title, subtitle, psiData) {
    const measured = psiData.worst || psiData || {};
    const threshold = psiData.psi_threshold || st.psi_threshold || 0.25;
    const psiVal = measured.psi;
    const cls = psiVal == null ? 'info' : psiVal < threshold * 0.4 ? 'ok' : psiVal < threshold ? 'warn' : 'fail';
    const label = psiVal == null ? '—' : psiVal < threshold * 0.4 ? 'STABLE' : psiVal < threshold ? 'MINOR SHIFT' : 'SHIFT';
    const pct = psiVal != null ? Math.min((psiVal / threshold) * 100, 100) : 0;
    const color = cls === 'ok' ? '#1D9E75' : cls === 'warn' ? '#BA7517' : '#A32D2D';
    return `<div class="card">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px">
        <div><h2 style="margin-bottom:2px">${title}</h2><div style="font-size:11px;color:var(--text2)">${subtitle}</div></div>
        ${badge(cls, label)}
      </div>
      ${row('PSI', psiVal != null ? psiVal.toFixed(4) + ' ' + badge(cls, (psiVal < threshold ? `< ${threshold} ok` : `> ${threshold} flag`)) : '—')}
      ${row('KS statistic', measured.ks_stat != null ? measured.ks_stat.toFixed(4) : '—')}
      ${row('KS p-value', measured.ks_pval != null ? pval(measured.ks_pval) + ' ' + (measured.ks_pval < 0.05 ? badge('warn','significant') : badge('ok','pass')) : '—')}
      ${row('Wasserstein', measured.wasserstein != null ? measured.wasserstein.toFixed(5) : '—')}
      ${row('worst fold', measured.fold != null ? measured.fold : '—')}
      <div class="bar-wrap" style="margin-top:10px"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
      <div class="bar-labels"><span>0 (identical)</span><span>${threshold} (flag threshold)</span></div>
      ${measured.has_shift ? note('warn', 'Distribution shifted between train and validation period') : note('ok', 'Distribution stable between periods')}
    </div>`;
  }

  const mu = retStats.mean || 0, sigma = retStats.std || 0.02;
  const skew = retStats.skew || 0, kurt = retStats.kurtosis || 0;
  const xs = [], hist = [], normalData = [];
  for (let i = -18; i <= 18; i++) {
    const x = i * 0.5;
    const u = (x / 100 - mu) / sigma;
    const pdf = Math.exp(-0.5 * u * u) / (sigma * Math.sqrt(2 * Math.PI));
    const gram = pdf * (1 + (skew / 6) * (u**3 - 3*u) + (kurt / 24) * (u**4 - 6*u**2 + 3));
    xs.push(x.toFixed(1) + '%');
    hist.push(Math.max(0, gram * 100 * 0.005));
    normalData.push(pdf * 100 * 0.005);
  }

  document.getElementById('panel-distribution').innerHTML = `
    <div class="grid2">
      ${psiCard('PSI — Return distribution', 'walk-forward train vs validation folds', psiRet)}
      ${psiCard('PSI — IV distribution', 'IV levels train vs val', psiIV)}
    </div>
    <div class="card" style="margin-top:14px">
      <h2>Return distribution (${retStats.n || '—'} trading days)</h2>
      <div class="grid2" style="margin-bottom:14px">
        <div>
          ${row('mean/day', retStats.mean != null ? (retStats.mean*100).toFixed(4) + '%' : '—')}
          ${row('daily σ', retStats.std != null ? (retStats.std*100).toFixed(4) + '%' : '—')}
          ${row('annualized vol', retStats.std != null ? (retStats.std*Math.sqrt(252)*100).toFixed(1) + '%' : '—')}
        </div>
        <div>
          ${row('skewness', retStats.skew != null ? retStats.skew.toFixed(4) + (Math.abs(retStats.skew)>0.5 ? ' '+badge('warn','skewed'):'') : '—')}
          ${row('excess kurtosis', retStats.kurtosis != null ? retStats.kurtosis.toFixed(4) + (retStats.kurtosis>3?' '+badge('info','fat tails'):'') : '—')}
          ${row('max gain / loss', retStats.max_gain != null ? '+'+( retStats.max_gain*100).toFixed(2)+'% / '+(retStats.max_loss*100).toFixed(2)+'%' : '—')}
        </div>
      </div>
      <div class="chart-wrap"><canvas id="retChart" role="img" aria-label="Return distribution histogram"></canvas></div>
    </div>`;

  new Chart(document.getElementById('retChart'), {
    type: 'bar',
    data: {
      labels: xs,
      datasets: [
        { label: 'empirical', data: hist, backgroundColor: isDark ? 'rgba(55,138,221,0.45)' : 'rgba(55,138,221,0.35)', borderWidth: 0, barPercentage: 1, categoryPercentage: 1 },
        { label: 'normal', data: normalData, type: 'line', borderColor: isDark ? '#F09595' : '#D85A30', borderWidth: 1.5, pointRadius: 0, fill: false, borderDash: [4,3] },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: tc, font: { size: 10 }, maxTicksLimit: 10, autoSkip: true }, grid: { color: gc } },
        y: { ticks: { color: tc, font: { size: 10 } }, grid: { color: gc } }
      }
    }
  });
})();

// ── Panel 4: Flags ────────────────────────────────────────
(function() {
  const flags = D.flags || [];
  if (!flags.length) {
    document.getElementById('panel-flags').innerHTML = '<div class="card"><p style="color:var(--text2)">No calibration flags.</p></div>';
    return;
  }
  const html = flags.map(f => `
    <div class="flag-row flag-${f.severity}">
      <div class="flag-signal">[${f.severity.toUpperCase()}] ${f.signal}</div>
      <div style="font-size:12px;margin-top:4px">${f.interpretation || ''}</div>
      <div class="flag-action">→ ${f.suggested_action || ''}</div>
    </div>
  `).join('');
  document.getElementById('panel-flags').innerHTML = html;
})();
</script>
</body>
</html>
"""


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Janus Final Report — __RUN_ID__</title>
<style>
  *, *::before, *::after { box-sizing: border-box; }
  :root {
    --paper: #fbfaf7;
    --ink: #161513;
    --muted: #6d6860;
    --faint: #9c968b;
    --rule: #d8d1c3;
    --rule-strong: #8f8778;
    --wash: #f1eee7;
    --wash-2: #ebe6dc;
    --ok: #1f6f50;
    --warn: #986b13;
    --fail: #9b2f2f;
    --info: #315f8c;
    --mono: "Cascadia Mono", "SF Mono", Consolas, monospace;
    --serif: "Cambria", "Georgia", "Times New Roman", serif;
    --sans: "Segoe UI", Arial, sans-serif;
  }
  body {
    margin: 0;
    background: #e7e2d7;
    color: var(--ink);
    font-family: var(--serif);
    font-size: 13px;
    line-height: 1.42;
  }
  .sheet {
    width: min(1120px, calc(100vw - 28px));
    margin: 18px auto;
    background: var(--paper);
    border: 1px solid var(--rule);
    box-shadow: 0 18px 50px rgba(60, 46, 21, 0.16);
  }
  .inner { padding: 28px 34px 34px; }
  .masthead {
    border-bottom: 2px solid var(--ink);
    padding-bottom: 14px;
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 20px;
    align-items: end;
  }
  .label-top {
    font-family: var(--sans);
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--muted);
    font-size: 10px;
    font-weight: 700;
  }
  h1 {
    font-size: 28px;
    line-height: 1.05;
    font-weight: 700;
    margin: 5px 0 0;
  }
  h2 {
    font-size: 15px;
    margin: 0 0 8px;
    padding-bottom: 5px;
    border-bottom: 1px solid var(--rule);
  }
  h3 {
    font-size: 12px;
    margin: 0 0 6px;
    font-family: var(--sans);
    text-transform: uppercase;
    letter-spacing: .06em;
    color: var(--muted);
  }
  .run-stamp {
    font-family: var(--mono);
    font-size: 11px;
    text-align: right;
    color: var(--muted);
    white-space: nowrap;
  }
  .abstract {
    margin-top: 13px;
    padding: 10px 12px;
    background: var(--wash);
    border-left: 4px solid var(--rule-strong);
    font-size: 12px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(12, 1fr);
    gap: 12px;
    margin-top: 16px;
  }
  .box {
    border: 1px solid var(--rule);
    background: rgba(255,255,255,.28);
    padding: 11px 12px;
    min-width: 0;
  }
  .span-3 { grid-column: span 3; }
  .span-4 { grid-column: span 4; }
  .span-5 { grid-column: span 5; }
  .span-6 { grid-column: span 6; }
  .span-7 { grid-column: span 7; }
  .span-8 { grid-column: span 8; }
  .span-12 { grid-column: span 12; }
  .metric-label {
    font-family: var(--sans);
    text-transform: uppercase;
    letter-spacing: .06em;
    color: var(--muted);
    font-size: 10px;
  }
  .metric-value {
    font-family: var(--mono);
    font-size: 20px;
    font-weight: 700;
    margin-top: 2px;
  }
  .metric-note { color: var(--muted); font-size: 11px; margin-top: 1px; }
  table {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
  }
  th {
    font-family: var(--sans);
    text-transform: uppercase;
    letter-spacing: .04em;
    font-size: 10px;
    color: var(--muted);
    text-align: left;
    border-bottom: 1px solid var(--rule-strong);
    padding: 5px 6px;
    vertical-align: bottom;
  }
  td {
    border-bottom: 1px solid var(--rule);
    padding: 5px 6px;
    vertical-align: top;
    word-wrap: break-word;
  }
  tr:last-child td { border-bottom: 0; }
  .num, .mono {
    font-family: var(--mono);
    overflow-wrap: anywhere;
    word-break: break-word;
  }
  .right { text-align: right; }
  .center { text-align: center; }
  .status {
    display: inline-block;
    font-family: var(--sans);
    font-size: 10px;
    font-weight: 700;
    border: 1px solid currentColor;
    padding: 1px 6px;
    border-radius: 2px;
    white-space: nowrap;
  }
  .ok { color: var(--ok); }
  .warn { color: var(--warn); }
  .fail { color: var(--fail); }
  .info { color: var(--info); }
  .ledger {
    display: grid;
    grid-template-columns: 190px 1fr;
    border-top: 1px solid var(--rule);
  }
  .ledger div {
    padding: 5px 6px;
    border-bottom: 1px solid var(--rule);
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .ledger div:nth-child(odd) {
    color: var(--muted);
    font-family: var(--sans);
    font-size: 11px;
  }
  .callout {
    border: 1px solid var(--rule);
    background: var(--wash);
    padding: 10px 12px;
    margin-top: 10px;
  }
  .callout strong { font-family: var(--sans); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
  .bar { height: 6px; background: var(--wash-2); border: 1px solid var(--rule); margin-top: 4px; }
  .bar > span { display: block; height: 100%; background: var(--info); }
  .toc {
    font-family: var(--sans);
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    margin-top: 14px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--rule);
  }
  .toc a { color: var(--ink); text-decoration: none; border-bottom: 1px dotted var(--rule-strong); }
  .small { font-size: 11px; color: var(--muted); }
  .footer {
    margin-top: 18px;
    padding-top: 8px;
    border-top: 1px solid var(--rule);
    font-size: 10px;
    color: var(--muted);
    font-family: var(--sans);
    display: flex;
    justify-content: space-between;
    gap: 12px;
  }
  @media (max-width: 820px) {
    .inner { padding: 20px 16px 24px; }
    .masthead { grid-template-columns: 1fr; }
    .run-stamp { text-align: left; white-space: normal; }
    .span-3, .span-4, .span-5, .span-6, .span-7, .span-8 { grid-column: span 12; }
  }
  @media print {
    body { background: white; }
    .sheet { width: 100%; margin: 0; box-shadow: none; border: 0; }
    .inner { padding: 18mm; }
  }
</style>
</head>
<body>
<main class="sheet">
  <div class="inner">
    <header class="masthead">
      <div>
        <div class="label-top">Janus Quant Pipeline</div>
        <h1 id="title">Final Results Ledger</h1>
        <div id="subtitle" class="small"></div>
      </div>
      <div class="run-stamp" id="run-stamp"></div>
    </header>

    <section class="abstract" id="abstract"></section>

    <nav class="toc">
      <a href="#bill">Result bill</a>
      <a href="#guards">Guard status</a>
      <a href="#tests">Statistical tests</a>
      <a href="#lineage">Data lineage</a>
      <a href="#distribution">Distribution</a>
      <a href="#flags">Flags</a>
      <a href="#artifacts">Artifacts</a>
    </nav>

    <section id="bill" class="grid"></section>
    <section id="guards" class="grid"></section>
    <section id="tests" class="grid"></section>
    <section id="lineage" class="grid"></section>
    <section id="distribution" class="grid"></section>
    <section id="flags" class="grid"></section>
    <section id="artifacts" class="grid"></section>

    <footer class="footer">
      <span>Generated by Janus reporting module. Values are rendered from pipeline summary artifacts.</span>
      <span id="footer-run"></span>
    </footer>
  </div>
</main>

<script>
const D = __DATA_JSON__;
const s = D.summary || {};
const st = D.stability || {};

function esc(v) {
  if (v === null || v === undefined) return "";
  return String(v).replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));
}
function fmt(v, digits = 4) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(digits);
}
function pct(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return (Number(v) * 100).toFixed(digits) + "%";
}
function int(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toLocaleString();
}
function statusClass(value) {
  const text = String(value || "").toLowerCase();
  if (["pass", "ok", "true", "stationary", "stable", "none", "homoskedastic"].includes(text)) return "ok";
  if (["fail", "false", "shift", "non_stationary"].includes(text)) return "fail";
  if (["warning", "warn", "mixed", "not_checked", "partial"].includes(text)) return "warn";
  return "info";
}
function badge(value, label) {
  const cls = statusClass(value);
  return `<span class="status ${cls}">${esc(label ?? value)}</span>`;
}
function box(cls, html) { return `<div class="box ${cls}">${html}</div>`; }
function metric(label, value, note = "") {
  return `<div class="metric-label">${esc(label)}</div><div class="metric-value">${esc(value)}</div>${note ? `<div class="metric-note">${esc(note)}</div>` : ""}`;
}
function kv(rows) {
  return `<div class="ledger">${rows.map(([k,v]) => `<div>${esc(k)}</div><div>${v}</div>`).join("")}</div>`;
}
function table(headers, rows) {
  return `<table><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${
    rows.length ? rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${headers.length}" class="small">No rows.</td></tr>`
  }</tbody></table>`;
}
function sectionTitle(title, note = "") {
  return `<h2>${esc(title)}</h2>${note ? `<p class="small">${esc(note)}</p>` : ""}`;
}
function guardStatus(obj) {
  if (obj && typeof obj === "object") {
    return { value: obj.status || "unknown", note: obj.reason || obj.data_version || "" };
  }
  return { value: obj || "unknown", note: "" };
}
function pval(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v) < 0.001 ? Number(v).toExponential(2) : Number(v).toFixed(4);
}

const instrument = s.instrument || "unknown";
const family = s.family || "unknown";
const dates = Array.isArray(s.date_range) ? s.date_range.join(" to ") : "unknown range";
document.getElementById("title").textContent = `Final Results Ledger: ${instrument}`;
document.getElementById("subtitle").textContent = `${family} · ${dates}`;
document.getElementById("run-stamp").innerHTML = `Run ID<br><strong>${esc(D.run_id || s.run_id || "unknown")}</strong>`;
document.getElementById("footer-run").textContent = D.run_id || s.run_id || "";

const strategyReady = !!s.strategy_metrics_available;
const metricInput = s.metrics_input || "unknown";
document.getElementById("abstract").innerHTML = strategyReady
  ? `<strong>Result status.</strong> Strategy return data is present; reported metrics are strategy-level outputs.`
  : `<strong>Result status.</strong> This run is a market diagnostic ledger, not a strategy-performance approval. Strategy P&L/return columns are absent, so Stage 4 diagnostics use the market return stream.`;

const passRate = s.n_folds ? Number(s.n_folds_passed || 0) / Number(s.n_folds) : null;
const returnStats = st.return_stats || {};
const ivStats = st.iv_stats || {};
document.getElementById("bill").innerHTML = [
  box("span-3", metric("raw rows", int(s.n_rows_raw), "provider boundary")),
  box("span-3", metric("prepared rows", int(s.n_rows_prepared), "adapter output")),
  box("span-3", metric("fold pass rate", passRate === null ? "—" : pct(passRate, 1), `${s.n_folds_passed || 0}/${s.n_folds || 0} folds`)),
  box("span-3", metric("metrics input", metricInput, strategyReady ? "strategy returns" : "market diagnostic")),
  box("span-6", sectionTitle("Run bill") + kv([
    ["Instrument", esc(instrument)],
    ["Family", esc(family)],
    ["Date range", esc(dates)],
    ["Output directory", `<span class="mono">${esc(s.output_dir || "—")}</span>`],
    ["Cache mode", `<span class="mono">${esc(s.data_cache_mode || "—")}</span>`],
  ])),
  box("span-6", sectionTitle("Return ledger") + kv([
    ["Trading days", `<span class="num">${int(st.trading_days)}</span>`],
    ["Mean return/day", `<span class="num">${pct(returnStats.mean, 4)}</span>`],
    ["Daily volatility", `<span class="num">${pct(returnStats.std, 4)}</span>`],
    ["Max gain / loss", `<span class="num">${pct(returnStats.max_gain, 2)} / ${pct(returnStats.max_loss, 2)}</span>`],
    ["IV null rate", `<span class="num">${ivStats.null_pct !== undefined ? fmt(ivStats.null_pct, 2) + "%" : "—"}</span>`],
  ])),
].join("");

const guards = s.guard_status || {};
const pit = guardStatus(guards.pit_timing);
const cache = guardStatus(guards.cache_version_fixed);
const pnl = guardStatus(guards.strategy_pnl_present);
document.getElementById("guards").innerHTML = [
  box("span-12", sectionTitle("Guard status", "These are contract-level controls. A warning here means the report should be read with the stated limitation.")),
  box("span-4", `<h3>PIT timing</h3>${badge(pit.value)}<p class="small">${esc(pit.note || "as_of_date / available_at / decision_time contract")}</p>`),
  box("span-4", `<h3>Strategy P&L</h3>${badge(pnl.value)}<p class="small">${strategyReady ? "strategy return stream present" : "missing signal/position/P&L layer"}</p>`),
  box("span-4", `<h3>Cache version</h3>${badge(cache.value)}<p class="small">${esc(cache.note || "fixed raw data version preferred")}</p>`),
].join("");

const adf = st.adf || {};
const arch = st.arch || {};
const vr = st.variance_ratio || {};
const lb = st.ljung_box || {};
const jb = st.jarque_bera || {};
const hurstObj = (st.hurst && typeof st.hurst === "object") ? st.hurst : {};
const hurst = typeof st.hurst === "number" ? st.hurst : hurstObj.hurst;
const stationarityRows = [
  ["ADF + KPSS", "stationarity", badge(adf.consensus || "unknown"), `ADF p=${pval(adf.adf_pval)}; KPSS p=${pval(adf.kpss_pval)}`, adf.consensus === "stationary" ? "series accepted as stationary" : "inspect structural breaks"],
  ["ARCH-LM", "volatility clustering", badge(arch.has_arch_effects ? "warning" : arch.has_arch_effects === false ? "pass" : "unknown", arch.has_arch_effects ? "ARCH present" : arch.has_arch_effects === false ? "none" : "unknown"), `p=${pval(arch.lm_pval)}`, arch.has_arch_effects ? "use vol/regime controls" : "no ARCH flag"],
  ["Variance ratio", "random walk", badge(vr.interpretation || "unknown"), `VR=${fmt(vr.vr_stat, 4)}; z=${fmt(vr.z_stat, 3)}`, vr.interpretation || "not measured"],
  ["Ljung-Box", "autocorrelation", badge(lb.has_autocorr ? "warning" : lb.has_autocorr === false ? "pass" : "unknown", lb.has_autocorr ? "autocorr" : lb.has_autocorr === false ? "none" : "unknown"), `p=${pval(lb.lb_pval)}`, lb.has_autocorr ? "review purge/embargo" : "no autocorrelation flag"],
  ["Jarque-Bera", "normality", badge(jb.is_normal ? "pass" : jb.is_normal === false ? "info" : "unknown", jb.is_normal ? "normal" : jb.is_normal === false ? "fat tails" : "unknown"), `p=${pval(jb.jb_pval)}; skew=${fmt(jb.skew, 3)}; kurt=${fmt(jb.kurtosis, 3)}`, jb.is_normal ? "normal approximation acceptable" : "prefer tail-aware risk metrics"],
  ["Hurst exponent", "memory", badge(hurst > 0.65 ? "warning" : hurst < 0.45 ? "info" : hurst === undefined ? "unknown" : "pass", hurst === undefined ? "unknown" : fmt(hurst, 3)), `H=${fmt(hurst, 3)}`, hurst > 0.65 ? "persistent trend" : hurst < 0.45 ? "anti-persistent" : "near random walk"],
];
document.getElementById("tests").innerHTML = [
  box("span-12", sectionTitle("Statistical test ledger") + table(
    ["Test", "Question", "Result", "Statistic", "Reading"],
    stationarityRows.map(r => r.map(escExceptHtml))
  )),
].join("");

function escExceptHtml(x) {
  const text = String(x ?? "");
  return text.includes("<span") ? text : esc(text);
}

const stages = D.stages || [];
document.getElementById("lineage").innerHTML = [
  box("span-12", sectionTitle("Data lineage", "Rows and schema hashes by processing stage.") + table(
    ["Stage", "Rows", "Δ rows", "Nulls", "Schema", "Data"],
    stages.map(r => [
      esc(r.stage),
      `<span class="num">${int(r.row_count)}</span>`,
      `<span class="num">${r.row_delta_from_previous === null || r.row_delta_from_previous === undefined ? "—" : int(r.row_delta_from_previous)}</span>`,
      `<span class="num">${int(r.total_nulls)}</span>`,
      r.schema_changed_from_previous ? badge("warning", "changed") : badge("pass", "stable"),
      r.data_changed_from_previous ? badge("warning", "changed") : badge("pass", "stable"),
    ])
  )),
].join("");

const psiReturns = st.psi_returns || {};
const worstPsi = psiReturns.worst || psiReturns;
const psiThreshold = psiReturns.psi_threshold || st.psi_threshold || 0.25;
const psiValue = worstPsi ? worstPsi.psi : null;
const psiRatio = psiValue === null || psiValue === undefined ? 0 : Math.min(100, Math.max(0, Number(psiValue) / Number(psiThreshold) * 100));
const regimeRows = (D.per_regime || []).slice(0, 12).map(r => [
  esc(r.regime),
  `<span class="num">${fmt(r.sharpe, 3)}</span>`,
  `<span class="num">${fmt(r.sortino, 3)}</span>`,
  `<span class="num">${pct(r.max_dd, 2)}</span>`,
  `<span class="num">${int(r.n_obs ?? r.n)}</span>`,
]);
const diversityRows = (D.diversity || []).slice(0, 12).map(r => [
  `<span class="num">${esc(r.fold)}</span>`,
  badge(r.pass ? "pass" : "fail", r.pass ? "pass" : "fail"),
  `<span class="num">${fmt(r.conc, 3)}</span>`,
  `<span class="num">${fmt(r.kl, 3)}</span>`,
  `<span class="num">${fmt(r.js, 3)}</span>`,
]);
document.getElementById("distribution").innerHTML = [
  box("span-5", sectionTitle("Distribution shift") + kv([
    ["Return PSI", `<span class="num">${fmt(psiValue, 4)}</span> ${psiValue !== null && psiValue !== undefined ? badge(Number(psiValue) <= Number(psiThreshold) ? "pass" : "fail", Number(psiValue) <= Number(psiThreshold) ? "below threshold" : "above threshold") : badge("unknown")}`],
    ["PSI threshold", `<span class="num">${fmt(psiThreshold, 3)}</span>`],
    ["KS statistic", `<span class="num">${fmt(worstPsi && worstPsi.ks_stat, 4)}</span>`],
    ["Worst fold", `<span class="num">${worstPsi && worstPsi.fold !== undefined ? esc(worstPsi.fold) : "—"}</span>`],
  ]) + `<div class="bar"><span style="width:${psiRatio}%"></span></div>`),
  box("span-7", sectionTitle("Regime performance", "First 12 regime rows.") + table(["Regime", "Sharpe", "Sortino", "Max DD", "N"], regimeRows)),
  box("span-12", sectionTitle("Fold diversity gate") + table(["Fold", "Pass", "Concentration", "KL", "JS"], diversityRows)),
].join("");

const flags = D.flags || [];
document.getElementById("flags").innerHTML = [
  box("span-12", sectionTitle("Calibration flags") + (
    flags.length
      ? table(["Severity", "Area", "Signal", "Suggested action"], flags.map(f => [
          badge(f.severity || "info", f.severity || "info"),
          esc(f.area || ""),
          esc(f.signal || ""),
          esc(f.suggested_action || ""),
        ]))
      : `<p class="small">No calibration flags.</p>`
  )),
].join("");

const artifacts = s.artifacts || {};
const reportPaths = s.summary_report || {};
const artifactRows = [
  ...Object.entries(artifacts).filter(([,v]) => v).map(([k,v]) => [esc(k), `<span class="mono">${esc(v)}</span>`]),
  ...Object.entries(reportPaths).filter(([,v]) => v).map(([k,v]) => [`report:${esc(k)}`, `<span class="mono">${esc(v)}</span>`]),
];
if (s.html_report) artifactRows.push(["report:html", `<span class="mono">${esc(s.html_report)}</span>`]);
document.getElementById("artifacts").innerHTML = [
  box("span-12", sectionTitle("Artifact index", "Run-scoped output paths. The folder name is the primary identifier.") + table(["Artifact", "Path"], artifactRows)),
].join("");
</script>
</body>
</html>
"""


def write_summary_report_from_files(run_id: str, outputs_dir: str | Path = "outputs") -> dict[str, str]:
    summary, per_fold, per_regime, diversity = load_run_outputs(run_id, outputs_dir)
    target_dir = locate_run_output_dir(run_id, outputs_dir)
    return write_summary_report(summary, per_fold, per_regime, diversity, target_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate visualization-ready summary report for a pipeline run.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--outputs-dir", default="outputs")
    args = parser.parse_args()

    paths = write_summary_report_from_files(args.run_id, args.outputs_dir)
    print(json.dumps(paths, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
