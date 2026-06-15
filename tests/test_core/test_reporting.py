"""Summary reporting tests."""

import json

import pandas as pd

from core.reporting import build_summary_report, load_run_outputs, write_summary_report


def _sample_summary():
    return {
        "run_id": "unit_report",
        "instrument": "AAPL",
        "family": "equity",
        "date_range": ["2024-01-01", "2024-02-01"],
        "n_rows_raw": 10,
        "n_rows_prepared": 10,
        "n_folds": 2,
        "n_folds_passed": 0,
        "audit_snapshots": [
            {
                "stage": "splitter",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "row_count": 10,
                "schema_hash": "schema_a",
                "data_hash": "data_a",
                "key_stats": {"return_std": {"min": -0.01, "max": 0.02, "mean": 0.001, "null_count": 1}},
                "na_pattern": {"return_std": 1},
            },
            {
                "stage": "metrics",
                "timestamp": "2024-01-01T00:00:01+00:00",
                "row_count": 10,
                "schema_hash": "schema_a",
                "data_hash": "data_a",
                "key_stats": {"return_std": {"min": -0.01, "max": 0.02, "mean": 0.001, "null_count": 1}},
                "na_pattern": {"return_std": 1},
            },
        ],
    }


def test_build_summary_report_creates_visualization_tables():
    per_regime = pd.DataFrame({"regime": ["low_vol"], "sharpe": [-1.0]})
    diversity = pd.DataFrame({"fold": [0, 1], "pass": [False, False], "conc": [1.0, 0.8], "js": [0.6, 0.4]})

    tables = build_summary_report(_sample_summary(), pd.DataFrame(), per_regime, diversity)

    assert set(tables) == {
        "stage_comparison",
        "stage_deltas",
        "column_stats_long",
        "metric_cards",
        "calibration_flags",
    }
    cards = tables["metric_cards"].set_index("card")
    assert cards.loc["metrics_input_data_unchanged", "value"]
    assert cards.loc["fold_pass_rate", "status"] == "warning"
    assert "cross_validation" in set(tables["calibration_flags"]["area"])


def test_write_summary_report_outputs_csv_json_and_markdown(tmp_path):
    paths = write_summary_report(_sample_summary(), outputs_dir=tmp_path)

    assert paths["stage_comparison"].endswith("_stage_comparison.csv")
    assert paths["visualization_json"].endswith("_visualization.json")
    assert paths["markdown"].endswith("_summary_report.md")

    payload = json.loads((tmp_path / "summary_report" / "unit_report_visualization.json").read_text())
    assert payload["stage_comparison"][0]["stage"] == "splitter"
    assert "Pipeline Summary Report" in (tmp_path / "summary_report" / "unit_report_summary_report.md").read_text()


def test_load_run_outputs_treats_empty_csv_as_empty_dataframe(tmp_path):
    (tmp_path / "perf_report").mkdir()
    (tmp_path / "fold_manifest").mkdir()
    (tmp_path / "unit_report_summary.json").write_text(json.dumps(_sample_summary()))
    (tmp_path / "perf_report" / "unit_report_per_fold.csv").write_text("")

    _, per_fold, per_regime, diversity = load_run_outputs("unit_report", tmp_path)

    assert per_fold.empty
    assert per_regime.empty
    assert diversity.empty
