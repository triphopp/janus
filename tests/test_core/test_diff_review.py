"""Policy tests for core/diff_review.py."""

import json
from pathlib import Path

import pytest

from core.diff_review import (
    KEY_COLUMNS,
    LABEL_COLUMNS,
    CANONICAL_MARKET_COLUMNS,
    summarize_ledger,
    write_diff_summary,
    is_summary_fresh,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    return path


def _rec(change_type="cell_mod", column="price_std", reason="outlier_cap",
         stage_from="adapter", stage_to="validators",
         delta=-0.1, pct=-0.001, **kw) -> dict:
    return {
        "stage_from": stage_from, "stage_to": stage_to,
        "change_type": change_type, "column": column,
        "reason": reason, "delta": delta, "pct": pct,
        "key": {"as_of_date": "2024-01-01", "symbol": "WTI"},
        **kw,
    }


# ── Status: pass ──────────────────────────────────────────────────────────────

def test_clean_attributed_ledger_passes(tmp_path):
    p = tmp_path / "run1_changes.jsonl"
    _write_jsonl(p, [_rec(reason="outlier_cap", column="price_std")])
    s = summarize_ledger(p, run_id="run1")
    assert s["status"] == "pass"
    assert s["findings"] == []


# ── Hard gate: KEY_MUTATION ───────────────────────────────────────────────────

def test_key_mutation_fails(tmp_path):
    p = tmp_path / "bad_changes.jsonl"
    _write_jsonl(p, [_rec(column="strike", change_type="cell_mod", reason="outlier_cap")])
    s = summarize_ledger(p, run_id="bad")
    assert s["status"] == "fail"
    assert any(f["code"] == "KEY_MUTATION" for f in s["findings"])


def test_key_mutation_count_in_protected(tmp_path):
    p = tmp_path / "r_changes.jsonl"
    _write_jsonl(p, [_rec(column="symbol"), _rec(column="as_of_date")])
    s = summarize_ledger(p, run_id="r")
    assert s["protected"]["key_mutations"] == 2


# ── Hard gate: LABEL_MUTATION ─────────────────────────────────────────────────

def test_label_mutation_fails(tmp_path):
    p = tmp_path / "lbl_changes.jsonl"
    _write_jsonl(p, [_rec(column="label", reason="outlier_cap")])
    s = summarize_ledger(p, run_id="lbl")
    assert s["status"] == "fail"
    assert any(f["code"] == "LABEL_MUTATION" for f in s["findings"])


# ── Hard gate: PROTECTED_UNATTRIBUTED ────────────────────────────────────────

def test_protected_unattributed_fails(tmp_path):
    p = tmp_path / "ua_changes.jsonl"
    _write_jsonl(p, [_rec(column="price_std", reason="UNATTRIBUTED")])
    s = summarize_ledger(p, run_id="ua")
    assert s["status"] == "fail"
    assert any(f["code"] == "PROTECTED_UNATTRIBUTED" for f in s["findings"])


# ── Hard gate: UNEXPLAINED_ROW_DROP ──────────────────────────────────────────

def test_unexplained_row_drop_fails(tmp_path):
    p = tmp_path / "rd_changes.jsonl"
    _write_jsonl(p, [{"stage_from": "adapter", "stage_to": "validators",
                      "change_type": "row_drop", "key": {"symbol": "X"},
                      "reason": None}])
    s = summarize_ledger(p, run_id="rd")
    assert s["status"] == "fail"
    assert any(f["code"] == "UNEXPLAINED_ROW_DROP" for f in s["findings"])


# ── Hard gate: PROTECTED_SCHEMA_DROP ─────────────────────────────────────────

def test_protected_schema_drop_fails(tmp_path):
    p = tmp_path / "sd_changes.jsonl"
    _write_jsonl(p, [{"stage_from": "adapter", "stage_to": "validators",
                      "change_type": "schema_drop", "column": "price_std",
                      "key": {}}])
    s = summarize_ledger(p, run_id="sd")
    assert s["status"] == "fail"
    assert any(f["code"] == "PROTECTED_SCHEMA_DROP" for f in s["findings"])


# ── Degraded: malformed JSONL ─────────────────────────────────────────────────

def test_malformed_jsonl_is_degraded(tmp_path):
    p = tmp_path / "mf_changes.jsonl"
    p.write_text('{"ok": true}\n{bad-json}\n', encoding="utf-8")
    s = summarize_ledger(p, run_id="mf")
    assert s["status"] == "degraded"
    assert any(f["code"] == "MALFORMED_JSONL" for f in s["findings"])
    assert s["ledger"]["malformed_lines"] == 1


# ── Degraded: missing ledger ──────────────────────────────────────────────────

def test_missing_ledger_is_degraded(tmp_path):
    p = tmp_path / "ghost_changes.jsonl"
    s = summarize_ledger(p, run_id="ghost")
    assert s["status"] == "degraded"
    assert any(f["code"] == "LEDGER_MISSING" for f in s["findings"])


# ── Budget: non-protected UNATTRIBUTED ───────────────────────────────────────

def test_non_protected_unattributed_warns_before_fail_threshold(tmp_path):
    p = tmp_path / "npu_changes.jsonl"
    # 1 UNATTRIBUTED on a non-protected column → warn (warn_count=1)
    _write_jsonl(p, [_rec(column="some_derived", reason="UNATTRIBUTED")])
    s = summarize_ledger(p, run_id="npu")
    assert s["status"] == "warn"
    assert any(f["code"] == "UNATTRIBUTED_BUDGET_WARN" for f in s["findings"])


def test_non_protected_unattributed_fails_above_threshold(tmp_path):
    p = tmp_path / "npu2_changes.jsonl"
    records = [_rec(column="some_col", reason="UNATTRIBUTED") for _ in range(60)]
    _write_jsonl(p, records)
    s = summarize_ledger(p, run_id="npu2")
    assert s["status"] == "fail"
    assert any(f["code"] == "UNATTRIBUTED_BUDGET_FAIL" for f in s["findings"])


# ── Materiality ───────────────────────────────────────────────────────────────

def test_materiality_warn_for_large_attributed_price_delta(tmp_path):
    p = tmp_path / "mat_changes.jsonl"
    # p99 abs_pct will be 0.02 > warn threshold 0.01
    records = [_rec(column="price_std", reason="outlier_cap", delta=-1.0, pct=-0.02)
               for _ in range(10)]
    _write_jsonl(p, records)
    s = summarize_ledger(p, run_id="mat")
    assert any(f["code"] in ("MATERIALITY_WARN", "MATERIALITY_FAIL") for f in s["findings"])


# ── Rollups ───────────────────────────────────────────────────────────────────

def test_rollups_count_correctly(tmp_path):
    p = tmp_path / "r2_changes.jsonl"
    _write_jsonl(p, [
        _rec(column="price_std", reason="outlier_cap"),
        _rec(column="iv", reason="outlier_cap"),
        {"stage_from": "adapter", "stage_to": "validators",
         "change_type": "row_drop", "key": {}, "reason": "validator_or_filter"},
    ])
    s = summarize_ledger(p, run_id="r2")
    assert s["rollups"]["by_change_type"]["cell_mod"] == 2
    assert s["rollups"]["by_change_type"]["row_drop"] == 1
    assert s["rollups"]["by_reason"]["outlier_cap"] == 2


# ── Deterministic samples ─────────────────────────────────────────────────────

def test_deterministic_samples_are_reproducible(tmp_path):
    p = tmp_path / "det_changes.jsonl"
    records = [_rec(column="price_std", reason="outlier_cap", delta=-float(i) * 0.01)
               for i in range(100)]
    _write_jsonl(p, records)
    s1 = summarize_ledger(p, run_id="det")
    s2 = summarize_ledger(p, run_id="det")
    assert s1["samples"] == s2["samples"]


# ── Streaming: must not call read_text ───────────────────────────────────────

def test_summarizer_streams_without_read_text(tmp_path, monkeypatch):
    p = tmp_path / "stream_changes.jsonl"
    _write_jsonl(p, [_rec()])

    original_read_text = Path.read_text

    def guard_read_text(self, *args, **kwargs):
        if str(self) == str(p):
            raise AssertionError(f"must stream ledger, not read_text: {self}")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guard_read_text)
    summarize_ledger(p, run_id="stream")


# ── write_diff_summary ────────────────────────────────────────────────────────

def test_write_diff_summary_creates_file(tmp_path):
    p = tmp_path / "ws_changes.jsonl"
    _write_jsonl(p, [_rec()])
    out = write_diff_summary(p, run_id="ws", out_dir=tmp_path)
    assert Path(out).exists()
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert data["run_id"] == "ws"
    assert "generated_at" in data
    assert data["status"] == "pass"


# ── is_summary_fresh ──────────────────────────────────────────────────────────

def test_is_summary_fresh_false_when_summary_missing(tmp_path):
    assert not is_summary_fresh(tmp_path / "a.jsonl", tmp_path / "a_summary.json")


def test_is_summary_fresh_true_after_write(tmp_path):
    p = tmp_path / "fr_changes.jsonl"
    _write_jsonl(p, [_rec()])
    out = Path(write_diff_summary(p, run_id="fr", out_dir=tmp_path))
    assert is_summary_fresh(p, out)
