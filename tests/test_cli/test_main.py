"""End-to-end CLI dispatch + golden actionable-error tests.

These exercise the argument parser and command handlers without running the
heavy pipeline (``run`` is covered only on its refuse-to-run path).
"""

import pytest

from cli.main import main


WTI_HEADER = (
    "TRADE DATE|HUB|PRODUCT|STRIP|CONTRACT|CONTRACT TYPE|STRIKE|"
    "SETTLEMENT PRICE|NET CHANGE|EXPIRATION DATE|PRODUCT_ID|"
    "OPTION_VOLATILITY|DELTA_FACTOR"
)


def _write_wti(path):
    path.write_text(
        WTI_HEADER + "\n"
        "9/25/2024|WTI|WTI Crude Futures|11/1/2024|T|C|35.0|34.69|-1.87|10/17/2024|425|58.26|1.0\n"
        "12/31/2024|WTI|WTI Crude Futures|2/1/2025|T|P|70.0|2.10|0.05|1/15/2025|425|41.00|-0.4\n",
        encoding="utf-8",
    )
    return path


def _run(argv, reg):
    return main(["--registry", str(reg), *argv])


# ── import / data ────────────────────────────────────────────────────────────

def test_import_then_list(tmp_path, capsys):
    data = _write_wti(tmp_path / "WTI.csv")
    reg = tmp_path / "r.yaml"

    assert _run(["import", "WTI", str(data)], reg) == 0
    out = capsys.readouterr().out
    assert "Imported WTI" in out
    assert "sha256=" in out

    assert _run(["data", "list", "WTI"], reg) == 0
    out = capsys.readouterr().out
    assert "active:" in out


def test_data_use_switches_active(tmp_path, capsys):
    a = _write_wti(tmp_path / "a.csv")
    b = _write_wti(tmp_path / "b.csv")
    reg = tmp_path / "r.yaml"
    _run(["data", "import", "--ticker", "WTI", "--file", str(a)], reg)
    _run(["data", "import", "--ticker", "WTI", "--file", str(b)], reg)
    capsys.readouterr()

    # second import has its own source id; switch back to first
    info_rc = _run(["data", "list", "WTI"], reg)
    assert info_rc == 0


# ── run refusal (golden actionable error) ────────────────────────────────────

def test_run_refuses_without_data_source(tmp_path, capsys):
    reg = tmp_path / "r.yaml"
    rc = _run(["run", "WTI", "--window", "2024Q4"], reg)
    err = capsys.readouterr().err
    assert rc == 2
    assert "not ready" in err
    assert "janus import WTI" in err


def test_run_window_and_dates_conflict(tmp_path, capsys):
    reg = tmp_path / "r.yaml"
    rc = _run(["run", "WTI", "--window", "2024Q4", "--from", "2024-09-25"], reg)
    assert rc == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_run_override_requires_advanced(tmp_path, capsys):
    reg = tmp_path / "r.yaml"
    rc = _run(["run", "WTI", "--window", "2024Q4", "--override", "x=1"], reg)
    assert rc == 2
    assert "--advanced" in capsys.readouterr().err


# ── explain / doctor ─────────────────────────────────────────────────────────

def test_explain_ready_after_import(tmp_path, capsys):
    data = _write_wti(tmp_path / "WTI.csv")
    reg = tmp_path / "r.yaml"
    _run(["import", "WTI", str(data)], reg)
    capsys.readouterr()

    rc = _run(["explain", "WTI", "--window", "2024Q4", "--universe", "near-term"], reg)
    out = capsys.readouterr().out
    assert rc == 0
    assert "READY" in out
    assert "near-term" in out


def test_doctor_reports_states(tmp_path, capsys):
    reg = tmp_path / "r.yaml"
    rc = _run(["doctor", "BZ"], reg)
    out = capsys.readouterr().out
    assert "Doctor: BZ" in out
    assert rc == 2  # no data source -> fail


def test_list_runs(tmp_path, capsys):
    reg = tmp_path / "r.yaml"
    rc = _run(["list"], reg)
    out = capsys.readouterr().out
    assert rc == 0
    assert "SYMBOL" in out


def test_run_defaults_to_plain_progress(tmp_path, capsys, monkeypatch):
    data = _write_wti(tmp_path / "WTI.csv")
    reg = tmp_path / "r.yaml"
    _run(["import", "WTI", str(data)], reg)
    capsys.readouterr()

    captured = {}

    def fake_run_pipeline(cfg, start, end, run_id):
        captured.update({"cfg": cfg, "start": start, "end": end, "run_id": run_id})

    import run_pipeline as rp

    monkeypatch.setattr(rp, "run_pipeline", fake_run_pipeline)
    rc = _run(["run", "WTI", "--window", "2024Q4", "--name", "wti_test"], reg)

    assert rc == 0
    assert captured["cfg"]["progress_mode"] == "plain"
    assert captured["cfg"]["runtime_overrides"]["progress"] == "plain"


def test_run_accepts_progress_override(tmp_path, capsys, monkeypatch):
    data = _write_wti(tmp_path / "WTI.csv")
    reg = tmp_path / "r.yaml"
    _run(["import", "WTI", str(data)], reg)
    capsys.readouterr()

    captured = {}

    def fake_run_pipeline(cfg, start, end, run_id):
        captured.update({"cfg": cfg, "start": start, "end": end, "run_id": run_id})

    import run_pipeline as rp

    monkeypatch.setattr(rp, "run_pipeline", fake_run_pipeline)
    rc = _run(["run", "WTI", "--window", "2024Q4", "--progress", "bar"], reg)

    assert rc == 0
    assert captured["cfg"]["progress_mode"] == "bar"
    assert captured["cfg"]["runtime_overrides"]["progress"] == "bar"
