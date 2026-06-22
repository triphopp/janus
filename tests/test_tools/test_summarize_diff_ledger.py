"""Tests for tools/summarize_diff_ledger.py."""

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

import tools.summarize_diff_ledger as tool


def _write_ledger(diff_dir: Path, run_id: str, records: list[dict] | None = None) -> Path:
    p = diff_dir / f"{run_id}_changes.jsonl"
    recs = records or [
        {"stage_from": "adapter", "stage_to": "validators",
         "change_type": "cell_mod", "column": "price_std",
         "reason": "outlier_cap", "delta": -0.1, "key": {}}
    ]
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return p


# ── --run-id ──────────────────────────────────────────────────────────────────

def test_run_one_writes_summary(tmp_path):
    diff_dir = tmp_path / "diff"
    diff_dir.mkdir()
    _write_ledger(diff_dir, "r1")
    result = tool.run_one("r1", diff_dir)
    assert "r1" in result["rebuilt"]
    assert (diff_dir / "r1_summary.json").exists()


def test_run_one_skips_fresh_summary(tmp_path):
    diff_dir = tmp_path / "diff"
    diff_dir.mkdir()
    _write_ledger(diff_dir, "r2")
    tool.run_one("r2", diff_dir)
    result = tool.run_one("r2", diff_dir)
    assert "r2" in result["skipped"]
    assert result["rebuilt"] == []


def test_run_one_force_rebuilds(tmp_path):
    diff_dir = tmp_path / "diff"
    diff_dir.mkdir()
    _write_ledger(diff_dir, "r3")
    tool.run_one("r3", diff_dir)
    result = tool.run_one("r3", diff_dir, force=True)
    assert "r3" in result["rebuilt"]


def test_run_one_error_when_ledger_missing(tmp_path):
    diff_dir = tmp_path / "diff"
    diff_dir.mkdir()
    result = tool.run_one("ghost", diff_dir)
    assert result["errors"]
    assert "not found" in result["errors"][0]["error"]


# ── --all ─────────────────────────────────────────────────────────────────────

def test_run_all_summarizes_all_ledgers(tmp_path):
    diff_dir = tmp_path / "diff"
    diff_dir.mkdir()
    for rid in ["a", "b", "c"]:
        _write_ledger(diff_dir, rid)
    result = tool.run_all(diff_dir)
    assert set(result["rebuilt"]) >= {"a_changes", "b_changes", "c_changes"}
    assert result["errors"] == []


def test_run_all_skips_fresh_on_second_call(tmp_path):
    diff_dir = tmp_path / "diff"
    diff_dir.mkdir()
    _write_ledger(diff_dir, "x")
    tool.run_all(diff_dir)
    result = tool.run_all(diff_dir)
    assert result["rebuilt"] == []
    assert result["skipped"]


# ── --check ───────────────────────────────────────────────────────────────────

def test_check_returns_true_when_all_fresh(tmp_path):
    diff_dir = tmp_path / "diff"
    diff_dir.mkdir()
    _write_ledger(diff_dir, "y")
    tool.run_all(diff_dir)
    assert tool.check(diff_dir) is True


def test_check_returns_false_when_summary_missing(tmp_path):
    diff_dir = tmp_path / "diff"
    diff_dir.mkdir()
    _write_ledger(diff_dir, "z")  # no summary written
    assert tool.check(diff_dir) is False


# ── never imports run_pipeline ────────────────────────────────────────────────

def test_tool_never_imports_run_pipeline(tmp_path, monkeypatch):
    import builtins
    orig = builtins.__import__

    def guard(name, *args, **kwargs):
        assert "run_pipeline" not in name, "tool must not import run_pipeline"
        return orig(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)
    diff_dir = tmp_path / "diff"
    diff_dir.mkdir()
    _write_ledger(diff_dir, "safe")
    tool.run_one("safe", diff_dir)
