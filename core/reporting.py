"""Visualization-ready summary reports for pipeline runs."""

from __future__ import annotations

import argparse
import json
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
    report_dir = outputs_dir / "summary_report"
    report_dir.mkdir(parents=True, exist_ok=True)

    run_id = str(summary.get("run_id", "unknown"))
    tables = build_summary_report(summary, per_fold, per_regime, diversity)

    paths: dict[str, str] = {}
    for name, df in tables.items():
        path = report_dir / f"{run_id}_{name}.csv"
        df.to_csv(path, index=False)
        paths[name] = str(path)

    viz_path = report_dir / f"{run_id}_visualization.json"
    with open(viz_path, "w", encoding="utf-8") as f:
        json.dump({name: _records(df) for name, df in tables.items()}, f, indent=2)
    paths["visualization_json"] = str(viz_path)

    md_path = report_dir / f"{run_id}_summary_report.md"
    paths["markdown"] = str(md_path)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(summary, tables, paths))

    return paths


def load_run_outputs(run_id: str, outputs_dir: str | Path = "outputs") -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    outputs_dir = Path(outputs_dir)
    with open(outputs_dir / f"{run_id}_summary.json", encoding="utf-8") as f:
        summary = json.load(f)

    def read_optional(path: Path) -> pd.DataFrame:
        if not path.exists() or path.stat().st_size == 0:
            return pd.DataFrame()
        try:
            return pd.read_csv(path)
        except pd.errors.EmptyDataError:
            return pd.DataFrame()

    per_fold = read_optional(outputs_dir / "perf_report" / f"{run_id}_per_fold.csv")
    per_regime = read_optional(outputs_dir / "perf_report" / f"{run_id}_per_regime.csv")
    diversity = read_optional(outputs_dir / "fold_manifest" / f"{run_id}_diversity.csv")
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
    report_dir = outputs_dir / "summary_report"
    report_dir.mkdir(parents=True, exist_ok=True)

    run_id = str(summary.get("run_id", "unknown"))
    tables = build_summary_report(summary, per_fold, per_regime, diversity)
    stage_rows = _records(tables["stage_comparison"])
    flag_rows = _records(tables["calibration_flags"])
    regime_rows = _records(per_regime) if per_regime is not None and not per_regime.empty else []
    diversity_rows = _records(diversity) if diversity is not None and not diversity.empty else []

    _LIST_KEYS = {"date_range"}
    data_json = json.dumps({
        "run_id": run_id,
        "summary": {
            k: v if isinstance(v, (int, float, bool, list, type(None))) or k in _LIST_KEYS
               else str(v)
            for k, v in summary.items() if k != "audit_snapshots"
        },
        "stability": stability_results,
        "stages": stage_rows,
        "flags": flag_rows,
        "per_regime": regime_rows,
        "diversity": diversity_rows,
    }, default=str)

    html = _HTML_TEMPLATE.replace("__DATA_JSON__", data_json).replace("__RUN_ID__", run_id)
    out_path = report_dir / f"{run_id}_report.html"
    out_path.write_text(html, encoding="utf-8")
    return str(out_path)


_HTML_TEMPLATE = r"""<!DOCTYPE html>
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


def write_summary_report_from_files(run_id: str, outputs_dir: str | Path = "outputs") -> dict[str, str]:
    summary, per_fold, per_regime, diversity = load_run_outputs(run_id, outputs_dir)
    return write_summary_report(summary, per_fold, per_regime, diversity, outputs_dir)


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
