"""Doctor and inspector tests."""

import json

from cli import doctor, inspect_runs, registry


WTI_HEADER = (
    "TRADE DATE|HUB|PRODUCT|STRIP|CONTRACT|CONTRACT TYPE|STRIKE|"
    "SETTLEMENT PRICE|NET CHANGE|EXPIRATION DATE|PRODUCT_ID|"
    "OPTION_VOLATILITY|DELTA_FACTOR"
)


def _write_wti(path):
    path.write_text(
        WTI_HEADER + "\n"
        "9/25/2024|WTI|WTI Crude Futures|11/1/2024|T|C|35.0|34.69|-1.87|10/17/2024|425|58.26|1.0\n",
        encoding="utf-8",
    )
    return path


def _status(checks, name):
    for c in checks:
        if c["name"] == name:
            return c["status"]
    return None


def test_doctor_equity_warns_live_provider(tmp_path):
    reg = tmp_path / "r.yaml"
    checks = doctor.run_doctor("NVDA", registry_path=reg)
    assert _status(checks, "data_source") == "warn"


def test_doctor_futures_without_source_fails(tmp_path):
    reg = tmp_path / "r.yaml"
    checks = doctor.run_doctor("bz", registry_path=reg)
    ds = next(c for c in checks if c["name"] == "data_source")
    assert ds["status"] == "fail"
    assert "import" in ds["next_action"]


def test_doctor_futures_with_pinned_source_passes(tmp_path):
    data = _write_wti(tmp_path / "BZ.csv")
    reg = tmp_path / "r.yaml"
    registry.import_source("BZ", data, registry_path=reg)
    checks = doctor.run_doctor("bz", registry_path=reg)
    assert _status(checks, "data_source") == "pass"


def test_doctor_detects_hash_mismatch(tmp_path):
    data = _write_wti(tmp_path / "BZ.csv")
    reg = tmp_path / "r.yaml"
    registry.import_source("BZ", data, registry_path=reg)
    data.write_text(WTI_HEADER + "\n9/25/2024|WTI|x|x|T|C|1|1|1|1|425|1|1\n", encoding="utf-8")
    checks = doctor.run_doctor("bz", registry_path=reg)
    assert _status(checks, "hash") == "fail"


def test_list_profiles_includes_known(tmp_path):
    reg = tmp_path / "r.yaml"
    rows = inspect_runs.list_profiles(registry_path=reg)
    symbols = {r["symbol"] for r in rows}
    assert "bz" in symbols
    bz = next(r for r in rows if r["symbol"] == "bz")
    assert bz["status"] == "missing data"


def test_show_run_reads_summary(tmp_path):
    run_dir = tmp_path / "outputs" / "runs" / "WTI" / "wti_q4"
    (run_dir / "report").mkdir(parents=True)
    (run_dir / "data").mkdir(parents=True)
    summary = {
        "run_id": "wti_q4",
        "reproducible": True,
        "preset": "official",
        "cache_guard": {"status": "pass"},
    }
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (run_dir / "report" / "final_report.html").write_text("<html></html>", encoding="utf-8")

    out = inspect_runs.show_run("wti_q4", outputs_dir=tmp_path / "outputs")
    assert out["preset"] == "official"
    assert out["guards"]["cache_guard"]["status"] == "pass"
    assert out["report"].endswith("final_report.html")


def test_show_run_missing_raises(tmp_path):
    try:
        inspect_runs.show_run("ghost", outputs_dir=tmp_path / "outputs")
        assert False, "expected RunNotFound"
    except inspect_runs.RunNotFound:
        pass
