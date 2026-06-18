import pandas as pd

from web import dashboard
from web import scanner


def test_prepared_fallback_counts_price_adjustment_warnings(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "r1__KO__equity__demo" / "data"
    run_dir.mkdir(parents=True)
    pd.DataFrame({
        "adj_factor": [1.0, 0.5, 0.5],
        "adj_factor_is_pit": ["False", "False", "False"],
        "price_adjustment_warning": ["False", "True", "True"],
        "adjusted_price_provider": [100.0, 51.0, 52.0],
        "price_std": [100.0, 102.0, 104.0],
    }).to_csv(run_dir / "prepared.csv", index=False)
    monkeypatch.setattr(scanner, "RUNS_DIR", tmp_path / "runs")

    out = scanner._load_price_adjustments_from_prepared("r1")

    assert out["status"] == "warning"
    assert out["policy"] == "retro_adjustment_blocked"
    assert out["factor_rows"] == 2
    assert out["warning_rows"] == 2
    assert out["max_abs_price_std_vs_provider_adjusted"] == 52.0


def test_scan_runs_links_report_in_symbol_grouped_layout(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "MSFT" / "r2"
    (run_dir / "report").mkdir(parents=True)
    (run_dir / "report" / "final_report.html").write_text("<html>report</html>", encoding="utf-8")
    (run_dir / "summary.json").write_text(
        """
        {
          "run_id": "r2",
          "instrument": "MSFT",
          "family": "equity",
          "n_rows_prepared": 3,
          "n_folds": 2,
          "n_folds_passed": 1,
          "metrics_input": "market_diagnostic",
          "strategy_metrics_available": false,
          "stability_score": {"sharpe_mean": 0.25},
          "data_quality": {
            "status": "warn",
            "worst_dimension": "coverage_shortfall",
            "dimensions": [{"name": "coverage_shortfall", "status": "warn"}]
          }
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(scanner, "OUTPUTS", tmp_path)
    monkeypatch.setattr(scanner, "MANIFEST_DIR", tmp_path / "manifest")
    monkeypatch.setattr(scanner, "BREAKS_DIR", tmp_path / "breaks")
    monkeypatch.setattr(scanner, "DIFF_DIR", tmp_path / "diff")
    monkeypatch.setattr(scanner, "RUNS_DIR", tmp_path / "runs")

    rows = scanner.scan_runs()

    assert scanner.find_run_dir("r2") == run_dir
    assert rows[0]["has_report"] is True
    assert rows[0]["metrics_input"] == "market_diagnostic"
    assert rows[0]["strategy_metrics_available"] is False
    assert rows[0]["dq_status"] == "warn"
    assert rows[0]["dq_worst_dimension"] == "coverage_shortfall"


def test_run_detail_includes_tagged_return_outliers(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "LMT" / "r3"
    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        '{"run_id":"r3","instrument":"LMT","family":"equity","n_rows_prepared":2}',
        encoding="utf-8",
    )
    pd.DataFrame({
        "as_of_date": ["2021-10-25", "2021-10-26"],
        "symbol": ["LMT", "LMT"],
        "raw_close": [340.0, 310.0],
        "price_std": [340.0, 310.0],
        "return_raw": [0.01, -0.088],
        "return_std": [0.01, -0.088],
        "return_winsorized": [0.01, -0.04],
        "_return_outlier_flag": [False, True],
        "_return_outlier_policy": ["tag_only", "tag_only"],
        "_return_validation_status": ["unreviewed", "provider_confirmed"],
        "_return_outlier_reason": ["", "cross_provider_validated"],
        "_return_outlier_evidence": ["", "provider_return=-0.0879"],
        "_return_outlier_direction": ["", "low"],
        "_return_outlier_zscore": [None, -8.5],
        "_return_outlier_severity": ["", "severe"],
        "_return_prior_median": [None, 0.001],
        "_return_clip_lower": [None, -0.04],
        "_return_clip_upper": [None, 0.04],
    }).to_csv(data_dir / "prepared.csv", index=False)
    monkeypatch.setattr(scanner, "OUTPUTS", tmp_path)
    monkeypatch.setattr(scanner, "MANIFEST_DIR", tmp_path / "manifest")
    monkeypatch.setattr(scanner, "BREAKS_DIR", tmp_path / "breaks")
    monkeypatch.setattr(scanner, "DIFF_DIR", tmp_path / "diff")
    monkeypatch.setattr(scanner, "RUNS_DIR", tmp_path / "runs")

    detail = scanner.run_detail("r3")

    assert detail["tagged_return_outlier_summary"]["total"] == 1
    assert detail["tagged_return_outlier_summary"]["by_status"] == {"provider_confirmed": 1}
    assert detail["tagged_return_outlier_summary"]["by_direction"] == {"low": 1}
    assert detail["tagged_return_outlier_summary"]["by_severity"] == {"severe": 1}
    assert detail["tagged_return_outliers"][0]["symbol"] == "LMT"
    assert detail["tagged_return_outliers"][0]["return_std"] == -0.088
    assert detail["tagged_return_outliers"][0]["_return_outlier_zscore"] == -8.5


def test_dashboard_requires_react_frontend_build(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "FRONTEND_DIST", tmp_path / "dist")

    response = dashboard.index()
    body = response.body.decode("utf-8")

    assert response.status_code == 503
    assert "web/frontend" in body
    assert "npm run build" in body
    assert not hasattr(dashboard, "_PAGE")


def test_fleet_summary_includes_adjustment_warnings():
    rows = [
        {"changes": 1, "unattributed": 0, "adjustment_warning_rows": 2, "breaks_total": 0, "breaks_open": 0, "sev_high": 0},
        {"changes": 3, "unattributed": 1, "adjustment_warning_rows": 4, "breaks_total": 1, "breaks_open": 1, "sev_high": 1, "dq_status": "fail"},
    ]

    out = scanner.fleet_summary(rows)

    assert out["total_changes"] == 4
    assert out["total_adjustment_warnings"] == 6
    assert out["dq_runs_failing"] == 1
