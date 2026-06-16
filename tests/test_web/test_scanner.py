import pandas as pd

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


def test_fleet_summary_includes_adjustment_warnings():
    rows = [
        {"changes": 1, "unattributed": 0, "adjustment_warning_rows": 2, "breaks_total": 0, "breaks_open": 0, "sev_high": 0},
        {"changes": 3, "unattributed": 1, "adjustment_warning_rows": 4, "breaks_total": 1, "breaks_open": 1, "sev_high": 1},
    ]

    out = scanner.fleet_summary(rows)

    assert out["total_changes"] == 4
    assert out["total_adjustment_warnings"] == 6
