"""v1.3 tests: lightweight data audit snapshots."""

import json

import pandas as pd

from core.audit import diff_stages, hash_schema, hash_subset, snapshot


def test_hashes_are_deterministic_and_schema_sensitive():
    df = pd.DataFrame({"a": [1, 2], "b": [1.0, 2.0]})
    same = df.copy()
    changed_schema = pd.DataFrame({"a": [1, 2], "b": ["1", "2"]})

    assert hash_subset(df) == hash_subset(same)
    assert hash_schema(df) == hash_schema(same)
    assert hash_schema(df) != hash_schema(changed_schema)


def test_snapshot_writes_jsonl_and_key_stats(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df = pd.DataFrame({"price": [1.0, 2.0, None], "volume": [10, 20, 30]})
    cfg = {"audit": {"enabled": True, "snapshot_cols": ["price", "volume"]}}

    snap = snapshot(df, "ingestion", cfg, run_id="unit")

    assert snap["row_count"] == 3
    assert snap["key_stats"]["price"]["null_count"] == 1
    path = tmp_path / "outputs" / "audit" / "unit.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["stage"] == "ingestion"


def test_snapshot_hashes_are_deterministic_across_reruns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df = pd.DataFrame({"price": [1.0, 2.0], "volume": [10, 20]})
    cfg = {"audit": {"enabled": True, "snapshot_cols": ["price", "volume"]}}

    first = snapshot(df, "metrics", cfg, run_id="deterministic")
    second = snapshot(df, "metrics", cfg, run_id="deterministic")

    assert first["schema_hash"] == second["schema_hash"]
    assert first["data_hash"] == second["data_hash"]


def test_snapshot_accepts_datetime_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df = pd.DataFrame({"as_of_date": pd.to_datetime(["2024-01-01", "2024-01-02"])})
    cfg = {"audit": {"enabled": True, "snapshot_cols": ["as_of_date"]}}

    snap = snapshot(df, "ingestion", cfg, run_id="datetime")

    assert snap["key_stats"]["as_of_date"]["min"] == "2024-01-01T00:00:00"
    assert snap["key_stats"]["as_of_date"]["max"] == "2024-01-02T00:00:00"
    assert snap["key_stats"]["as_of_date"]["mean"] is None


def test_diff_stages_detects_row_schema_and_nan_changes():
    before = {
        "stage": "before",
        "row_count": 2,
        "schema_hash": "abc",
        "na_pattern": {"a": 0},
        "key_stats": {"a": {"mean": 1.0, "min": 1.0, "max": 1.0, "null_count": 0}},
    }
    after = {
        "stage": "after",
        "row_count": 3,
        "schema_hash": "def",
        "na_pattern": {"a": 1},
        "key_stats": {"a": {"mean": 2.0, "min": 1.0, "max": 3.0, "null_count": 1}},
    }

    diff = diff_stages(before, after)

    assert diff["row_delta"] == 1
    assert diff["schema_changed"]
    assert diff["new_nans"] == {"a": 1}
    assert diff["key_stat_deltas"]["a"]["mean"] == 1.0


def test_diff_stages_ignores_nonnumeric_stat_deltas():
    before = {
        "stage": "splitter",
        "row_count": 2,
        "schema_hash": "abc",
        "na_pattern": {"as_of_date": 0},
        "key_stats": {"as_of_date": {"min": "2024-01-01", "max": "2024-01-02", "mean": None, "null_count": 0}},
    }
    after = {
        "stage": "metrics",
        "row_count": 2,
        "schema_hash": "abc",
        "na_pattern": {"as_of_date": 0},
        "key_stats": {"as_of_date": {"min": "2024-01-01", "max": "2024-01-02", "mean": None, "null_count": 0}},
    }

    diff = diff_stages(before, after)

    assert diff["key_stat_deltas"] == {}
