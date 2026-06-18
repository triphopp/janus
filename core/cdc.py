"""Change Data Capture — stage→stage cell diff with reason attribution (P2, §6).

Detects what changed between two pipeline stages at the row/cell level, classifies
the change, and attributes a reason. The atom is a ChangeRecord (one JSONL line).

Strategy (hybrid, per data_diff_design §3):
- post-hoc key-aligned frame diff (Strategy B) — the engine here; catches everything,
  including silent/unexpected mutations.
- reason attribution via the flag columns the validators already emit (Strategy A-lite,
  §4) — a price cell_mod co-located with ``_outlier_flag==True`` → reason ``outlier_cap``.
  A change with no owning flag → ``UNATTRIBUTED`` (the highest-value bucket: silent bugs).

Float identity keys are snapped (round 6) before alignment so raw bit noise doesn't
split a key into a spurious row_drop+row_add pair (§9).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from core.progress import progress_iter, should_show_progress

UNATTRIBUTED = "UNATTRIBUTED"
DEFAULT_KEY_ROUND = 6
DEFAULT_ATOL = 1e-9
DEFAULT_RTOL = 0.0


@dataclass
class ChangeRecord:
    stage_from: str
    stage_to: str
    change_type: str               # schema_add|schema_drop|row_add|row_drop|cell_mod
    key: dict = field(default_factory=dict)
    column: Optional[str] = None
    before: object = None
    after: object = None
    delta: Optional[float] = None
    pct: Optional[float] = None
    reason: Optional[str] = None
    reason_flag_col: Optional[str] = None
    run_id: Optional[str] = None
    sample_count: Optional[int] = None   # schema_add: non-null count of the new column

    def to_dict(self) -> dict:
        return asdict(self)


def _resolve_identity_cols(df: pd.DataFrame, identity_cols) -> list[str]:
    if identity_cols:
        cols = [c for c in identity_cols if c in df.columns]
        if cols:
            return cols
    # sensible fallback
    candidates = ["as_of_date", "product_id", "symbol", "contract_root", "hub",
                  "instrument_type", "right", "strike", "delivery_month", "expiry"]
    return [c for c in candidates if c in df.columns]


# Stable sentinels so NaN/None key parts compare EQUAL across frames. Without this a
# futures row (strike=NaN, right=None) hashes consistently but NaN != NaN, so the same
# row appears as both row_drop (before) and row_add (after) — a spurious break.
_NULL_NUM = -9.99e18
_NULL_OBJ = "\x00<NA>"


def _normalize_keys(work: pd.DataFrame, key_cols: list[str], key_round: int) -> pd.DataFrame:
    for c in key_cols:
        if pd.api.types.is_float_dtype(work[c]):
            work[c] = work[c].round(key_round).fillna(_NULL_NUM)
        else:
            work[c] = work[c].astype(object).where(work[c].notna(), _NULL_OBJ)
    return work


def _key_frame(df: pd.DataFrame, key_cols: list[str], key_round: int) -> pd.DataFrame:
    """Return df indexed by a stable business key (float keys snapped, nulls sentinelled)."""
    work = df.copy()
    work = _normalize_keys(work, key_cols, key_round)
    # Build a tuple key column to index by; tolerate duplicate keys by keeping first.
    work = work.drop_duplicates(subset=key_cols, keep="first")
    return work.set_index(key_cols)


def _is_changed(before, after, atol: float, rtol: float) -> bool:
    b_na, a_na = pd.isna(before), pd.isna(after)
    if b_na and a_na:
        return False
    if b_na != a_na:
        return True
    if isinstance(before, (int, float, np.integer, np.floating)) and isinstance(
        after, (int, float, np.integer, np.floating)
    ):
        return abs(float(after) - float(before)) > (atol + rtol * abs(float(before)))
    return before != after


def _attribute(reason_map: Optional[dict], column: str, after_row) -> tuple[str, Optional[str]]:
    """Resolve (reason, flag_col) for a cell change using flag columns on the after row."""
    if reason_map and column in reason_map:
        spec = reason_map[column]
        flag_col = spec.get("flag_col")
        reason = spec.get("reason", "attributed")
        if flag_col is not None and flag_col in getattr(after_row, "index", []):
            flag_val = after_row[flag_col]
            if bool(flag_val) and not pd.isna(flag_val):
                return reason, flag_col
        else:
            # no flag column to gate on → trust the declared reason
            return reason, flag_col
    return UNATTRIBUTED, None


def diff_frames(
    before: pd.DataFrame,
    after: pd.DataFrame,
    stage_from: str,
    stage_to: str,
    identity_cols=None,
    reason_map: Optional[dict] = None,
    tol: Optional[dict] = None,
    key_round: int = DEFAULT_KEY_ROUND,
    run_id: Optional[str] = None,
    progress: str | dict | None = None,
) -> list[ChangeRecord]:
    """Key-aligned diff of two stage frames → list of ChangeRecords."""
    tol = tol or {}
    records: list[ChangeRecord] = []

    # schema changes — a derived column shows up here, not as cell_mod, because the
    # adapter ADDS price_std rather than mutating raw_close in place. Enrich the add with
    # a representative value + non-null count so it reads "price_std appeared = 95.21"
    # instead of a bare "column added" (data_diff_design / stage_chain_diff_redesign).
    before_cols, after_cols = set(before.columns), set(after.columns)
    for col in sorted(after_cols - before_cols):
        sample, count = _first_sample(after[col])
        records.append(ChangeRecord(stage_from, stage_to, "schema_add", column=col,
                                    after=sample, sample_count=count, run_id=run_id))
    for col in sorted(before_cols - after_cols):
        sample, count = _first_sample(before[col])
        records.append(ChangeRecord(stage_from, stage_to, "schema_drop", column=col,
                                    before=sample, sample_count=count, run_id=run_id))

    key_cols = _resolve_identity_cols(after, identity_cols)
    if not key_cols:
        return records  # cannot align without a key; schema diff only

    bidx = _key_frame(before, key_cols, key_round)
    aidx = _key_frame(after, key_cols, key_round)

    bkeys, akeys = set(bidx.index), set(aidx.index)

    def _key_dict(k):
        if not isinstance(k, tuple):
            k = (k,)
        return {col: _jsonable(v) for col, v in zip(key_cols, k)}

    for k in akeys - bkeys:
        records.append(ChangeRecord(stage_from, stage_to, "row_add", key=_key_dict(k), run_id=run_id))
    for k in bkeys - akeys:
        records.append(ChangeRecord(stage_from, stage_to, "row_drop", key=_key_dict(k),
                                    reason=_drop_reason(reason_map), run_id=run_id))

    # cell modifications on shared keys + shared non-key columns
    shared_cols = [c for c in after.columns if c in before.columns and c not in key_cols]
    common_keys = bkeys & akeys
    if common_keys and shared_cols:
        b_common = bidx.loc[list(common_keys), shared_cols]
        a_common = aidx.loc[list(common_keys), shared_cols]
        key_iter = progress_iter(
            common_keys,
            f"CDC {stage_from}->{stage_to}",
            total=len(common_keys),
            enabled=should_show_progress(progress or "plain", total=len(common_keys)),
        )
        for k in key_iter:
            b_row = b_common.loc[k]
            a_row = a_common.loc[k]
            after_full = aidx.loc[k]
            for col in shared_cols:
                bv, av = b_row[col], a_row[col]
                atol = tol.get(col, {}).get("atol", DEFAULT_ATOL)
                rtol = tol.get(col, {}).get("rtol", DEFAULT_RTOL)
                if _is_changed(bv, av, atol, rtol):
                    reason, flag_col = _attribute(reason_map, col, after_full)
                    delta, pct = _delta_pct(bv, av)
                    records.append(ChangeRecord(
                        stage_from, stage_to, "cell_mod",
                        key=_key_dict(k), column=col,
                        before=_jsonable(bv), after=_jsonable(av),
                        delta=delta, pct=pct, reason=reason, reason_flag_col=flag_col,
                        run_id=run_id,
                    ))
    return records


def _drop_reason(reason_map: Optional[dict]) -> Optional[str]:
    if reason_map and "_row_drop" in reason_map:
        return reason_map["_row_drop"].get("reason")
    return None


def _first_sample(series: pd.Series) -> tuple[object, int]:
    """First non-null value of a column + its non-null count, JSON-safe.

    Used to give schema_add/schema_drop records a concrete value to display so a
    derived column is legible as a before/after, not just a name.
    """
    clean = series.dropna()
    count = int(len(clean))
    sample = _jsonable(clean.iloc[0]) if count else None
    return sample, count


def _delta_pct(before, after):
    try:
        b, a = float(before), float(after)
        delta = a - b
        pct = (delta / b) if b != 0 else None
        return delta, pct
    except (TypeError, ValueError):
        return None, None


def _jsonable(v):
    if isinstance(v, str) and v == _NULL_OBJ:
        return None
    if isinstance(v, float) and v == _NULL_NUM:
        return None
    if pd.isna(v) if np.isscalar(v) else False:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (pd.Timestamp,)):
        return v.isoformat()
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def diff_run(
    stage_frames: list[tuple[str, pd.DataFrame]],
    identity_cols=None,
    reason_maps: Optional[dict] = None,
    tol: Optional[dict] = None,
    run_id: Optional[str] = None,
    progress: str | dict | None = None,
) -> list[ChangeRecord]:
    """Diff consecutive stages. reason_maps keyed by 'stage_from->stage_to'."""
    reason_maps = reason_maps or {}
    out: list[ChangeRecord] = []
    for (sf, bf), (st, af) in zip(stage_frames, stage_frames[1:]):
        rmap = reason_maps.get(f"{sf}->{st}")
        out.extend(diff_frames(
            bf,
            af,
            sf,
            st,
            identity_cols,
            rmap,
            tol,
            run_id=run_id,
            progress=progress,
        ))
    return out


def write_ledger(records: list[ChangeRecord], run_id: str,
                 out_dir: Path | str = Path("outputs/diff")) -> str:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}_changes.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.to_dict(), default=str) + "\n")
    return str(path)


def rollup(records: list[ChangeRecord]) -> dict:
    """Per stage-transition × column change counts + unattributed tally."""
    out: dict = {}
    for r in records:
        tkey = f"{r.stage_from}->{r.stage_to}"
        bucket = out.setdefault(tkey, {})
        ckey = r.column or "_rows"
        cell = bucket.setdefault(ckey, {"cell_mod": 0, "row_add": 0, "row_drop": 0,
                                        "schema_add": 0, "schema_drop": 0,
                                        "unattributed": 0, "max_abs_delta": 0.0})
        if r.change_type in cell:
            cell[r.change_type] += 1
        if r.reason == UNATTRIBUTED:
            cell["unattributed"] += 1
        if r.delta is not None:
            cell["max_abs_delta"] = max(cell["max_abs_delta"], abs(r.delta))
    return out
