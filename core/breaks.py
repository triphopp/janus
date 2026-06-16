"""Break management lifecycle (P2, §7).

Every out-of-tolerance / unexplained change is a **break** — a tracked object with an
owner, an SLA, and a signed transition history, not a log line.

Lifecycle (§7):
    DETECTED → TRIAGED → { AUTO_RESOLVED | ACKNOWLEDGED | ESCALATED } → CLOSED

Severity routing:
- UNATTRIBUTED cell change → **high** (a value moved and no rule owns it = bug until proven)
- unexpected row_add / unexplained row_drop → **medium**
- attributed changes (outlier_cap, bound, strike-adjust) → not a break (expected ops)

Transitions are signed (§13.7): each carries actor_id, actor_role, timestamp, prev_status,
new_status, and a hash of the prior history entry → tamper-evident, segregation-of-duties.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.cdc import UNATTRIBUTED, ChangeRecord

# lifecycle states
DETECTED = "DETECTED"
TRIAGED = "TRIAGED"
AUTO_RESOLVED = "AUTO_RESOLVED"
ACKNOWLEDGED = "ACKNOWLEDGED"
ESCALATED = "ESCALATED"
CLOSED = "CLOSED"

_ALLOWED = {
    DETECTED: {TRIAGED},
    TRIAGED: {AUTO_RESOLVED, ACKNOWLEDGED, ESCALATED},
    AUTO_RESOLVED: {CLOSED},
    ACKNOWLEDGED: {CLOSED},
    ESCALATED: {ACKNOWLEDGED, CLOSED},
    CLOSED: set(),
}


class BreakTransitionError(Exception):
    """Illegal lifecycle transition."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_entry(entry: dict) -> str:
    return hashlib.sha256(json.dumps(entry, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _now_seq(run_id: str, seq: int) -> str:
    return f"BRK-{run_id}-{seq:05d}"


def classify(record: ChangeRecord) -> Optional[tuple[str, str]]:
    """Map a ChangeRecord to (break_type, severity), or None if not a break."""
    if record.change_type == "cell_mod":
        if record.reason == UNATTRIBUTED:
            return "unattributed", "high"
        return None  # attributed mutation = expected op
    if record.change_type == "row_add":
        return "unexpected_row_add", "medium"
    if record.change_type == "row_drop" and not record.reason:
        return "unexplained_row_drop", "medium"
    # schema_add/drop are usually expected (validators add flag columns) → not a break
    return None


def new_break(
    record: ChangeRecord,
    break_type: str,
    severity: str,
    run_id: str,
    seq: int,
    *,
    owner: Optional[str] = None,
    sla_hours: Optional[int] = None,
    lineage_impact: Optional[list[str]] = None,
) -> dict:
    """Create a break in DETECTED state from a ChangeRecord."""
    detected_at = _utc_now()
    first = {
        "to_status": DETECTED,
        "from_status": None,
        "actor_id": "cdc_engine",
        "actor_role": "system",
        "at": detected_at,
        "prev_hash": None,
    }
    first["entry_hash"] = _hash_entry(first)
    return {
        "break_id": _now_seq(run_id, seq),
        "type": break_type,
        "severity": severity,
        "status": DETECTED,
        "detected_at": detected_at,
        "run_id": run_id,
        "stage": f"{record.stage_from}->{record.stage_to}",
        "key": record.key,
        "field": record.column,
        "before": record.before,
        "after": record.after,
        "delta": record.delta,
        "reason": record.reason,
        "owner": owner,
        "sla_hours": sla_hours,
        "lineage_impact": lineage_impact or [],
        "history": [first],
    }


def transition(
    brk: dict,
    to_status: str,
    actor_id: str,
    actor_role: str,
    *,
    reason_code: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """Apply a signed lifecycle transition in place. Enforces the state machine + SoD."""
    current = brk["status"]
    if to_status not in _ALLOWED.get(current, set()):
        raise BreakTransitionError(f"illegal transition {current} -> {to_status}")

    # Segregation of duties: the system cannot acknowledge/close its own break (§13.7).
    if to_status in (ACKNOWLEDGED, CLOSED) and actor_role == "system":
        raise BreakTransitionError(f"{to_status} requires a human actor, not 'system'")
    # High-severity closure needs a reason code (§13.7).
    if to_status == CLOSED and brk.get("severity") == "high" and not reason_code:
        raise BreakTransitionError("closing a high-severity break requires a reason_code")

    prev_hash = brk["history"][-1]["entry_hash"]
    entry = {
        "to_status": to_status,
        "from_status": current,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "at": _utc_now(),
        "reason_code": reason_code,
        "note": note,
        "prev_hash": prev_hash,
    }
    entry["entry_hash"] = _hash_entry(entry)
    brk["history"].append(entry)
    brk["status"] = to_status
    if to_status in (ACKNOWLEDGED, CLOSED):
        brk["signed_by"] = actor_id
        brk["signed_at"] = entry["at"]
    return brk


def verify_chain(brk: dict) -> bool:
    """Validate the signed transition chain hasn't been rewritten."""
    prev = None
    for entry in brk["history"]:
        if entry.get("prev_hash") != prev:
            return False
        stored = entry.get("entry_hash")
        recomputed = _hash_entry({k: v for k, v in entry.items() if k != "entry_hash"})
        if stored != recomputed:
            return False
        prev = stored
    return True


def raise_breaks(
    records: list[ChangeRecord],
    run_id: str,
    *,
    owner: Optional[str] = None,
    sla_hours: Optional[int] = None,
    auto_resolve_attributed: bool = False,
) -> list[dict]:
    """Turn breaching/unexplained ChangeRecords into Break objects."""
    breaks: list[dict] = []
    seq = 0
    for rec in records:
        cls = classify(rec)
        if cls is None:
            continue
        break_type, severity = cls
        seq += 1
        breaks.append(new_break(
            rec, break_type, severity, run_id, seq,
            owner=owner, sla_hours=sla_hours,
        ))
    return breaks


def write_breaks(breaks: list[dict], run_id: str,
                 out_dir: Path | str = Path("outputs/breaks")) -> Optional[str]:
    if not breaks:
        return None
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for b in breaks:
            f.write(json.dumps(b, default=str) + "\n")
    return str(path)


def summarize(breaks: list[dict]) -> dict:
    by_sev: dict = {}
    by_type: dict = {}
    for b in breaks:
        by_sev[b["severity"]] = by_sev.get(b["severity"], 0) + 1
        by_type[b["type"]] = by_type.get(b["type"], 0) + 1
    return {"total": len(breaks), "by_severity": by_sev, "by_type": by_type}
