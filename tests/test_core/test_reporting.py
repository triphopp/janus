"""Summary reporting tests."""

import json

import pandas as pd

from core.reporting import (
    build_summary_report,
    load_run_outputs,
    run_output_dir,
    write_html_report,
    write_summary_report,
)


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

    assert paths["stage_comparison"].endswith("stage_comparison.csv")
    assert paths["visualization_json"].endswith("visualization.json")
    assert paths["markdown"].endswith("summary_report.md")

    payload = json.loads((tmp_path / "report" / "visualization.json").read_text())
    assert payload["stage_comparison"][0]["stage"] == "splitter"
    assert "Pipeline Summary Report" in (tmp_path / "report" / "summary_report.md").read_text()


def test_run_output_dir_is_named_for_human_navigation(tmp_path):
    path = run_output_dir(tmp_path, "run-01", "MSFT", "equity", "2024-01-01", "2024-12-31")

    assert path.name == "run-01"
    assert path.parent.name == "MSFT"
    assert path.parent.parent.name == "runs"


def test_write_html_report_outputs_academic_final_report(tmp_path):
    summary = {
        **_sample_summary(),
        "artifacts": {"per_fold": "tables/per_fold.csv"},
        "summary_report": {"markdown": "report/summary_report.md"},
    }
    path = write_html_report(
        summary,
        {
            "trading_days": 20,
            "adf": {"consensus": "stationary", "adf_pval": 0.01, "kpss_pval": 0.1},
            "arch": {"has_arch_effects": False, "lm_pval": 0.5},
            "variance_ratio": {"interpretation": "random_walk", "vr_stat": 1.0},
            "ljung_box": {"has_autocorr": False, "lb_pval": 0.4},
            "jarque_bera": {"is_normal": False, "jb_pval": 0.01, "skew": 0.2, "kurtosis": 4.0},
            "hurst": {"hurst": 0.5},
            "return_stats": {"mean": 0.001, "std": 0.01, "max_gain": 0.02, "max_loss": -0.03},
            "return_distribution": {
                "kind": "empirical_histogram",
                "n": 20,
                "markers": {
                    "mean": 0.001,
                    "median": 0.0005,
                    "var_95": -0.02,
                    "cvar_95": -0.025,
                    "p01": -0.03,
                    "p05": -0.02,
                    "p95": 0.018,
                    "p99": 0.02,
                    "min": -0.03,
                    "max": 0.02,
                },
                "bins": [
                    {"lo": -0.03, "hi": -0.01, "mid": -0.02, "count": 3, "density": 7.5, "cum_pct": 0.15},
                    {"lo": -0.01, "hi": 0.01, "mid": 0.0, "count": 12, "density": 30.0, "cum_pct": 0.75},
                    {"lo": 0.01, "hi": 0.02, "mid": 0.015, "count": 5, "density": 25.0, "cum_pct": 1.0},
                ],
            },
            "iv_stats": {"null_pct": 0.0},
            "psi_returns": {"worst": {"psi": 0.1, "ks_stat": 0.2, "fold": 0}, "psi_threshold": 0.25},
        },
        pd.DataFrame(
            {
                "fold": [1],
                "date_range": ["(Timestamp('2024-01-15'), Timestamp('2024-02-01'))"],
                "total_return": [0.12],
                "sharpe": [0.7],
                "sortino": [1.1],
                "max_dd": [-0.08],
                "cvar_95": [-0.03],
                "hit_rate": [0.55],
                "worst_day": [-0.02],
            }
        ),
        pd.DataFrame({"regime": ["low"], "sharpe": [1.2], "sortino": [1.5], "max_dd": [-0.1], "n_obs": [20]}),
        pd.DataFrame({"fold": [0, 1], "pass": [False, True], "conc": [0.9, 0.5], "kl": [0.8, 0.1], "js": [0.2, 0.05]}),
        outputs_dir=tmp_path,
    )

    html_path = tmp_path / "report" / "final_report.html"
    html = html_path.read_text(encoding="utf-8")
    assert path == str(html_path)
    assert "Final Results Ledger" in html
    assert "Return distribution" in html
    assert "data-dist-mode=\"cdf\"" in html
    assert "return-dist-payload" in html
    assert "Empirical daily-return histogram" in html
    assert "dist-x-label" in html
    assert "Fold diversity gate + return diagnostics" in html
    assert "not strategy P&amp;L" in html
    assert "2024-01-15 to 2024-02-01" in html
    assert "12.00%" in html
    assert "0.700" in html
    assert "55.0%" in html
    assert "<svg" in html
    assert "tables/per_fold.csv" in html
    assert "report/summary_report.md" in html
    assert "const D =" not in html


def test_return_distribution_svg_avoids_axis_and_marker_overlap():
    from core.reporting import _return_distribution_panel

    html = _return_distribution_panel(
        {"mean": 0.001, "std": 0.01, "n": 20},
        {
            "kind": "empirical_histogram",
            "n": 20,
            "markers": {
                "mean": 0.001,
                "median": 0.001,
                "var_95": -0.02,
                "cvar_95": -0.025,
            },
            "bins": [
                {"lo": -0.03, "hi": -0.01, "count": 3, "density": 7.5, "cum_pct": 0.15},
                {"lo": -0.01, "hi": 0.01, "count": 12, "density": 30.0, "cum_pct": 0.75},
                {"lo": 0.01, "hi": 0.02, "count": 5, "density": 25.0, "cum_pct": 1.0},
            ],
        },
    )

    assert 'transform="rotate(-90' in html
    assert "Mean / Median" in html


def test_load_run_outputs_treats_empty_csv_as_empty_dataframe(tmp_path):
    (tmp_path / "perf_report").mkdir()
    (tmp_path / "fold_manifest").mkdir()
    (tmp_path / "unit_report_summary.json").write_text(json.dumps(_sample_summary()))
    (tmp_path / "perf_report" / "unit_report_per_fold.csv").write_text("")

    _, per_fold, per_regime, diversity = load_run_outputs("unit_report", tmp_path)

    assert per_fold.empty
    assert per_regime.empty
    assert diversity.empty
