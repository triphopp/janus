"""Data-source registry tests for the user-facing CLI."""

import hashlib

import pytest

from cli import registry


WTI_HEADER = (
    "TRADE DATE|HUB|PRODUCT|STRIP|CONTRACT|CONTRACT TYPE|STRIKE|"
    "SETTLEMENT PRICE|NET CHANGE|EXPIRATION DATE|PRODUCT_ID|"
    "OPTION_VOLATILITY|DELTA_FACTOR"
)


def _write_wti(path, rows=None):
    rows = rows or [
        "9/25/2024|WTI|WTI Crude Futures|11/1/2024|T|C|35.0|34.69|-1.87|10/17/2024|425|58.26|1.0",
        "12/31/2024|WTI|WTI Crude Futures|2/1/2025|T|P|70.0|2.10|0.05|1/15/2025|425|41.00|-0.4",
    ]
    path.write_text(WTI_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


# ── file inspection ──────────────────────────────────────────────────────────

def test_detect_format_pipe(tmp_path):
    f = _write_wti(tmp_path / "WTI.csv")
    assert registry.detect_format(f) == "psv"


def test_detect_format_comma(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    assert registry.detect_format(f) == "csv"


def test_scan_file_rows_and_date_range(tmp_path):
    f = _write_wti(tmp_path / "WTI.csv")
    meta = registry.scan_file(f)
    assert meta["format"] == "psv"
    assert meta["rows"] == 2
    assert meta["date_range"] == ["2024-09-25", "2024-12-31"]


def test_scan_file_rejects_single_column(tmp_path):
    f = tmp_path / "bad.txt"
    f.write_text("justonecolumn\nvalue\n", encoding="utf-8")
    with pytest.raises(registry.RegistryError):
        registry.scan_file(f)


def test_scan_file_rejects_empty(tmp_path):
    f = tmp_path / "empty.csv"
    f.write_text("", encoding="utf-8")
    with pytest.raises(registry.RegistryError):
        registry.scan_file(f)


# ── import / list / use ──────────────────────────────────────────────────────

def test_import_registers_and_hashes(tmp_path):
    data = _write_wti(tmp_path / "WTI.csv")
    reg = tmp_path / "data_sources.yaml"

    rec = registry.import_source("wti", data, registry_path=reg)

    assert rec.sha256 == hashlib.sha256(data.read_bytes()).hexdigest()
    assert rec.format == "psv"
    assert rec.rows == 2
    assert rec.provider == "settlement"
    assert rec.date_range == ["2024-09-25", "2024-12-31"]
    assert reg.exists()


def test_import_sets_active_and_uppercases_ticker(tmp_path):
    data = _write_wti(tmp_path / "WTI.csv")
    reg = tmp_path / "r.yaml"

    rec = registry.import_source("wti", data, registry_path=reg)
    info = registry.list_sources("WTI", registry_path=reg)

    assert info["active"] == rec.source_id
    assert rec.source_id in info["sources"]


def test_get_active_returns_record(tmp_path):
    data = _write_wti(tmp_path / "WTI.csv")
    reg = tmp_path / "r.yaml"
    registry.import_source("WTI", data, registry_path=reg)

    active = registry.get_active("WTI", registry_path=reg)
    assert active is not None
    assert active.path == str(data.resolve())


def test_get_active_none_when_unregistered(tmp_path):
    reg = tmp_path / "r.yaml"
    assert registry.get_active("NOPE", registry_path=reg) is None


def test_use_switches_active_source(tmp_path):
    a = _write_wti(tmp_path / "a.csv")
    b = _write_wti(tmp_path / "b.csv")
    reg = tmp_path / "r.yaml"
    r1 = registry.import_source("WTI", a, source_id="src_a", registry_path=reg)
    r2 = registry.import_source("WTI", b, source_id="src_b", use=False, registry_path=reg)

    assert registry.get_active("WTI", registry_path=reg).source_id == r1.source_id
    registry.use_source("WTI", "src_b", registry_path=reg)
    assert registry.get_active("WTI", registry_path=reg).source_id == r2.source_id


def test_use_unknown_source_errors(tmp_path):
    data = _write_wti(tmp_path / "WTI.csv")
    reg = tmp_path / "r.yaml"
    registry.import_source("WTI", data, registry_path=reg)
    with pytest.raises(registry.RegistryError, match="no registered source"):
        registry.use_source("WTI", "ghost", registry_path=reg)


def test_import_missing_file_errors(tmp_path):
    reg = tmp_path / "r.yaml"
    with pytest.raises(registry.RegistryError, match="file not found"):
        registry.import_source("WTI", tmp_path / "ghost.csv", registry_path=reg)
