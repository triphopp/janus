"""End-to-end CLI fixture: import -> doctor -> plan ready -> cfg wired.

Uses a synthetic WTI-style settlement file (public-safe, no vendor rows). The
heavy pipeline run itself is validated out-of-band on real data; here we prove
the user path wires a reproducible, pinned, universe-scoped config end to end.
"""

from cli import doctor, plan, registry
from cli.main import main


WTI_HEADER = (
    "TRADE DATE|HUB|PRODUCT|STRIP|CONTRACT|CONTRACT TYPE|STRIKE|"
    "SETTLEMENT PRICE|NET CHANGE|EXPIRATION DATE|PRODUCT_ID|"
    "OPTION_VOLATILITY|DELTA_FACTOR"
)


def _synthetic_wti(path):
    rows = [
        "9/25/2024|WTI|WTI Crude Futures|11/1/2024|T|C|35.0|34.69|-1.87|10/17/2024|425|58.26|1.0",
        "10/15/2024|WTI|WTI Crude Futures|12/1/2024|T|P|70.0|2.10|0.05|11/15/2024|425|41.00|-0.4",
        "12/31/2024|WTI|WTI Crude Futures|2/1/2025|T|C|80.0|1.20|0.01|1/15/2025|425|38.00|0.2",
    ]
    path.write_text(WTI_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_import_doctor_plan_chain(tmp_path):
    data = _synthetic_wti(tmp_path / "WTI.csv")
    reg = tmp_path / "data_sources.yaml"

    # 1. import registers + pins
    rec = registry.import_source("WTI", data, registry_path=reg)
    assert rec.format == "psv"
    assert rec.rows == 3

    # 2. doctor passes on the data source
    checks = doctor.run_doctor("WTI", registry_path=reg)
    ds = next(c for c in checks if c["name"] in ("data_source", "hash"))
    assert ds["status"] == "pass"

    # 3. plan is ready, reproducible, and wires the pinned file + universe + greeks
    p = plan.build_plan(
        "WTI", start="2024-10-01", end="2024-12-31",
        preset="export", universe="near-term", run_id="wti_q4",
        registry_path=reg,
    )
    assert p.ready is True
    assert p.reproducible is True
    assert p.cfg["data_file"] == str(data.resolve())
    assert p.cfg["data_file_sha256"] == rec.sha256
    assert p.cfg["require_fixed_data_version"] is True
    assert p.cfg["compute_greeks"] is True            # export preset
    assert p.cfg["option_universe"]["max_dte_days"] == 90  # near-term


def test_cli_two_command_happy_path(tmp_path, capsys):
    """README's promise: import once, then run — at most two commands to be ready."""
    data = _synthetic_wti(tmp_path / "WTI.csv")
    reg = tmp_path / "r.yaml"

    rc_import = main(["--registry", str(reg), "import", "WTI", str(data)])
    capsys.readouterr()
    rc_explain = main([
        "--registry", str(reg), "explain", "WTI", "--window", "2024Q4",
        "--preset", "export",
    ])
    out = capsys.readouterr().out

    assert rc_import == 0
    assert rc_explain == 0
    assert "READY" in out


def test_old_entrypoint_still_importable():
    """Compatibility: the low-level run_pipeline entry still exists with its API."""
    import run_pipeline as rp

    assert hasattr(rp, "main")
    assert hasattr(rp, "run_pipeline")
    assert hasattr(rp, "apply_runtime_overrides")
