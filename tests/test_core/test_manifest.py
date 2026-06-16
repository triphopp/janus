"""P1 tests — run manifest + canonical hash determinism + replay compare."""

import numpy as np
import pandas as pd

from core.audit import canonical_frame_hash
from core import manifest as mf


def _df():
    return pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
        "available_at": pd.to_datetime(["2024-01-01", "2024-01-02"], utc=True) + pd.Timedelta(hours=3),
        "product_id": [254, 254],
        "price": [85.123456789, 86.0],
    })


# ── canonical hash ────────────────────────────────────────────────────────────

def test_canonical_hash_deterministic():
    assert canonical_frame_hash(_df()) == canonical_frame_hash(_df())


def test_canonical_hash_column_order_invariant():
    df = _df()
    reordered = df[df.columns[::-1]]
    assert canonical_frame_hash(df) == canonical_frame_hash(reordered)


def test_canonical_hash_ignores_sub_8dp_float_noise():
    df = _df()
    noisy = df.copy()
    noisy.loc[0, "price"] = 85.123456789 + 1e-12  # below 8dp rounding
    assert canonical_frame_hash(df) == canonical_frame_hash(noisy)


def test_canonical_hash_detects_real_value_change():
    df = _df()
    changed = df.copy()
    changed.loc[0, "price"] = 99.0
    assert canonical_frame_hash(df) != canonical_frame_hash(changed)


# ── manifest ──────────────────────────────────────────────────────────────────

def test_build_manifest_pins_inputs_and_outputs():
    cfg = {"family": "futures_options", "n_trials": 12}
    raw, prepared = _df(), _df().assign(return_std=[0.0, 0.01])
    m = mf.build_manifest(
        "run1", cfg, raw, prepared, symbol="254",
        contract_report={"contract_id": "settlement_options", "version": 1},
        n_trials=12, knowledge_cutoff_fallback="2024-12-31",
    )
    assert m["config_hash"]
    assert m["contract_versions"] == {"settlement_options": 1}
    assert "254" in m["input_data_hashes"]
    assert "prepared" in m["output_data_hashes"]
    assert m["n_trials"] == 12 and m["n_trials_source"] == "config"
    assert m["knowledge_time_cutoff"] is not None  # from available_at
    assert m["env"]["python"]


def test_compare_manifests_match_and_mismatch():
    cfg = {"family": "futures_options"}
    raw, prepared = _df(), _df()
    a = mf.build_manifest("r", cfg, raw, prepared, symbol="254")
    b = mf.build_manifest("r", cfg, raw, prepared, symbol="254")
    assert mf.compare_manifests(a, b)["match"] is True

    prepared2 = prepared.copy()
    prepared2.loc[0, "price"] = 1.0
    c = mf.build_manifest("r", cfg, raw, prepared2, symbol="254")
    cmp = mf.compare_manifests(a, c)
    assert cmp["match"] is False
    assert "output_data_hashes" in cmp["mismatches"]


def test_config_hash_changes_with_config():
    assert mf.config_hash({"a": 1}) != mf.config_hash({"a": 2})


def test_write_and_load_manifest_roundtrip(tmp_path):
    m = mf.build_manifest("rw", {"family": "equity"}, _df(), _df(), symbol="254")
    path = mf.write_manifest(m, tmp_path)
    loaded = mf.load_manifest(path)
    assert loaded["run_id"] == "rw"
