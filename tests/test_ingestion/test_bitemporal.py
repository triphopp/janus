"""P1 tests — bitemporal store: time-travel, restatement, hash-chain, integrity."""

import json

import pandas as pd
import pytest

from ingestion.versioned_cache import VersionedCache


def _row(price, as_of="2024-03-15"):
    return pd.DataFrame({
        "as_of_date": [pd.Timestamp(as_of)],
        "available_at": [pd.Timestamp(as_of + "T03:00:00Z")],
        "ingested_at": [pd.Timestamp("2024-03-15T00:00:00Z")],
        "product_id": [254],
        "price": [price],
    })


def test_restatement_creates_new_partition_without_overwriting(tmp_path):
    cache = VersionedCache(tmp_path)
    cache.write("bz", _row(85.0), ingested_at="2024-03-15", storage_format="csv")
    # corrected settlement learned later → new knowledge partition, original preserved
    cache.write("bz", _row(85.5), ingested_at="2024-03-20", storage_format="csv")
    assert cache.list_versions("bz") == ["2024-03-15", "2024-03-20"]


def test_time_travel_reads_value_as_known_at_T(tmp_path):
    cache = VersionedCache(tmp_path)
    cache.write("bz", _row(85.0), ingested_at="2024-03-15", storage_format="csv")
    cache.write("bz", _row(85.5), ingested_at="2024-03-20", storage_format="csv")

    base_cfg = {"data_storage_format": "csv", "versioned_cache": {"filename": "data.csv"}}
    # As known on Mar 18 → original 85.0 (NOT the future restatement)
    early = cache.read("bz", {**base_cfg, "data_version": "as_of_knowledge",
                              "knowledge_time": "2024-03-18"})
    assert early["price"].iloc[0] == 85.0
    # As known on Mar 21 → corrected 85.5
    late = cache.read("bz", {**base_cfg, "data_version": "as_of_knowledge",
                             "knowledge_time": "2024-03-21"})
    assert late["price"].iloc[0] == 85.5


def test_manifest_hash_chain_links_prev(tmp_path):
    cache = VersionedCache(tmp_path)
    cache.write("bz", _row(85.0), ingested_at="2024-03-15", storage_format="csv")
    cache.write("bz", _row(85.5), ingested_at="2024-03-20", storage_format="csv")

    records = [json.loads(l) for l in cache.manifest_path.read_text().splitlines() if l.strip()]
    bz = [r for r in records if r["symbol"] == "bz"]
    assert bz[0]["prev_hash"] is None
    assert bz[1]["prev_hash"] == bz[0]["chain_hash"]  # chain links forward
    assert bz[0]["writer"]["pid"]  # writer identity recorded


def test_verify_partition_parquet_detects_tamper(tmp_path):
    cache = VersionedCache(tmp_path)
    try:
        cache.write("bz", _row(85.0), ingested_at="2024-03-15", storage_format="parquet")
    except RuntimeError:
        pytest.skip("parquet engine unavailable")
    assert cache.verify_partition("bz", "2024-03-15", "parquet") is True

    # overwrite the partition file out-of-band → hash no longer matches manifest
    path = cache._data_path("bz", "2024-03-15", "data.parquet")
    _row(999.0).to_parquet(path, index=False)
    assert cache.verify_partition("bz", "2024-03-15", "parquet") is False


def test_verify_on_read_raises_on_mismatch(tmp_path):
    cache = VersionedCache(tmp_path)
    try:
        cache.write("bz", _row(85.0), ingested_at="2024-03-15", storage_format="parquet")
    except RuntimeError:
        pytest.skip("parquet engine unavailable")
    path = cache._data_path("bz", "2024-03-15", "data.parquet")
    _row(999.0).to_parquet(path, index=False)
    with pytest.raises(RuntimeError):
        cache.read("bz", {"data_version": "latest", "verify_on_read": True})


def test_atomic_write_leaves_no_tmp_file(tmp_path):
    cache = VersionedCache(tmp_path)
    cache.write("bz", _row(85.0), ingested_at="2024-03-15", storage_format="csv")
    part_dir = cache._partition_dir("bz", "2024-03-15")
    assert not list(part_dir.glob("*.tmp"))
    assert (part_dir / "data.csv").exists()
