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
