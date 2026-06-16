"""P2 tests — CDC diff engine: cell_mod, attribution, row/schema changes, tolerance."""

import pandas as pd

from core import cdc


def _frame(prices, strikes=None, extra=None):
    n = len(prices)
    df = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-03-15"] * n),
        "product_id": [254] * n,
        "strike": strikes if strikes is not None else [85.0 + i for i in range(n)],
        "right": ["C"] * n,
        "expiry": pd.to_datetime(["2024-05-01"] * n),
        "price": prices,
    })
    if extra:
        for k, v in extra.items():
            df[k] = v
    return df


IDENT = ["as_of_date", "product_id", "strike", "right", "expiry"]


def test_cell_mod_detected_with_delta():
    before = _frame([85.0, 90.0])
    after = _frame([85.0, 88.0])  # second row changed 90→88
    recs = cdc.diff_frames(before, after, "adapter", "validators", IDENT)
    mods = [r for r in recs if r.change_type == "cell_mod"]
    assert len(mods) == 1
    assert mods[0].column == "price"
    assert mods[0].before == 90.0 and mods[0].after == 88.0
    assert mods[0].delta == -2.0


def test_attributed_change_via_flag_column():
    before = _frame([85.0, 90.0])
    after = _frame([85.0, 88.0], extra={"_outlier_flag": [False, True]})
    rmap = {"price": {"flag_col": "_outlier_flag", "reason": "outlier_cap"}}
    recs = cdc.diff_frames(before, after, "adapter", "validators", IDENT, reason_map=rmap)
    mod = [r for r in recs if r.change_type == "cell_mod"][0]
    assert mod.reason == "outlier_cap"
    assert mod.reason_flag_col == "_outlier_flag"


def test_unattributed_change_when_flag_false():
    before = _frame([85.0, 90.0])
    after = _frame([85.0, 88.0], extra={"_outlier_flag": [False, False]})
    rmap = {"price": {"flag_col": "_outlier_flag", "reason": "outlier_cap"}}
    recs = cdc.diff_frames(before, after, "adapter", "validators", IDENT, reason_map=rmap)
    mod = [r for r in recs if r.change_type == "cell_mod"][0]
    assert mod.reason == cdc.UNATTRIBUTED


def test_tolerance_suppresses_tiny_change():
    before = _frame([85.0, 90.0])
    after = _frame([85.0, 90.0 + 1e-10])
    recs = cdc.diff_frames(before, after, "adapter", "validators", IDENT)
    assert [r for r in recs if r.change_type == "cell_mod"] == []


def test_row_drop_and_add():
    before = _frame([85.0, 90.0], strikes=[85.0, 90.0])
    after = _frame([85.0, 95.0], strikes=[85.0, 95.0])  # strike 90 dropped, 95 added
    recs = cdc.diff_frames(before, after, "adapter", "validators", IDENT)
    types = {r.change_type for r in recs}
    assert "row_drop" in types
    assert "row_add" in types


def test_schema_add_detected():
    before = _frame([85.0])
    after = _frame([85.0], extra={"_outlier_flag": [False]})
    recs = cdc.diff_frames(before, after, "adapter", "validators", IDENT)
    adds = [r for r in recs if r.change_type == "schema_add"]
    assert any(r.column == "_outlier_flag" for r in adds)


def test_float_key_snapping_prevents_spurious_rowchange():
    before = _frame([85.0], strikes=[85.0])
    after = _frame([85.0], strikes=[85.00000000001])  # sub-tick noise on key
    recs = cdc.diff_frames(before, after, "adapter", "validators", IDENT)
    assert [r for r in recs if r.change_type in ("row_add", "row_drop")] == []


def test_nan_keys_do_not_produce_spurious_row_changes():
    # futures rows carry strike=NaN, right=None — these must align, not look added+dropped
    before = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-03-15", "2024-03-16"]),
        "product_id": [254, 254],
        "strike": [float("nan"), float("nan")],
        "right": [None, None],
        "expiry": pd.to_datetime(["2024-05-01", "2024-05-01"]),
        "price": [85.0, 86.0],
    })
    after = before.copy()
    after["_flag"] = False  # validators add a column; rows unchanged
    recs = cdc.diff_frames(before, after, "adapter", "validators", IDENT)
    assert [r for r in recs if r.change_type in ("row_add", "row_drop")] == []


def test_write_ledger_and_rollup(tmp_path):
    before = _frame([85.0, 90.0])
    after = _frame([85.0, 88.0], extra={"_outlier_flag": [False, False]})
    rmap = {"price": {"flag_col": "_outlier_flag", "reason": "outlier_cap"}}
    recs = cdc.diff_frames(before, after, "adapter", "validators", IDENT, reason_map=rmap)
    path = cdc.write_ledger(recs, "run1", out_dir=tmp_path)
    assert (tmp_path / "run1_changes.jsonl").exists()
    roll = cdc.rollup(recs)
    assert roll["adapter->validators"]["price"]["cell_mod"] == 1
    assert roll["adapter->validators"]["price"]["unattributed"] == 1
