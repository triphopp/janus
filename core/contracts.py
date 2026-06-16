"""Data Contracts — versioned producer↔consumer interface (P0, bronze tier).

Replaces the column-only ``ingestion.base.validate_schema`` with a contract that
checks structural + semantic + point-in-time + symbology + distributional rules,
and routes failing rows to quarantine instead of silently flowing downstream.

Subsumes audit findings H2 (symbology not enforced) and H3 (dtype not enforced).
See: Memory/plans/data_ops_architecture.md §2 (contracts), §1 (quarantine).

Design notes:
- Per-row failures (structural null/invalid, semantic violation, symbology orphan,
  PIT violation) are DIVERTED to quarantine; the pipeline continues with clean rows.
- Frame-level breaks (missing required column, distributional drift) are reported and,
  when ``enforcement == 'block'``, raised as ``ContractViolation``.
- Semantic rules are vectorized pandas expressions evaluated with engine='python'.
  Contracts are trusted, author-controlled local YAML (not user input).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

DEFAULT_CONTRACTS_DIR = Path("contracts")
QUARANTINE_REASON_COL = "_quarantine_reason"

# family → (contract_id) default mapping. Override via cfg['contract']['id'].
FAMILY_CONTRACT = {
    "futures": "settlement_options",
    "futures_options": "settlement_options",
    "equity": "equity_price",
    "equity_options": "equity_options",
}


class ContractViolation(Exception):
    """Raised when a frame-level contract break occurs under enforcement='block'."""


@dataclass
class ContractResult:
    passed: pd.DataFrame
    quarantined: pd.DataFrame
    report: dict = field(default_factory=dict)


# ── loading ──────────────────────────────────────────────────────────────────

def resolve_contract_id(cfg: dict) -> Optional[tuple[str, Optional[int]]]:
    """Pick (contract_id, version) from cfg['contract'] or family default."""
    contract_cfg = cfg.get("contract") or {}
    cid = contract_cfg.get("id") or FAMILY_CONTRACT.get(cfg.get("family", ""))
    if not cid:
        return None
    return cid, contract_cfg.get("version")


def load_contract(
    contract_id: str,
    version: Optional[int] = None,
    contracts_dir: Path | str = DEFAULT_CONTRACTS_DIR,
) -> dict:
    """Load a contract YAML. version=None → highest available version on disk."""
    contracts_dir = Path(contracts_dir)
    if version is not None:
        path = contracts_dir / f"{contract_id}.v{version}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"contract not found: {path}")
    else:
        matches = sorted(contracts_dir.glob(f"{contract_id}.v*.yaml"))
        if not matches:
            raise FileNotFoundError(
                f"no contract versions for '{contract_id}' in {contracts_dir}"
            )
        path = matches[-1]
    with open(path, encoding="utf-8") as f:
        contract = yaml.safe_load(f)
    contract["_path"] = str(path)
    return contract


# ── helpers ──────────────────────────────────────────────────────────────────

def _snap_float_keys(df: pd.DataFrame, contract: dict) -> pd.DataFrame:
    """Round float identity keys to tick precision before any join/group (§9)."""
    rounding = (contract.get("identity") or {}).get("key_round") or {}
    # Per-column key_round in structural also honored.
    for col, spec in (contract.get("structural", {}).get("columns", {}) or {}).items():
        if isinstance(spec, dict) and "key_round" in spec:
            rounding.setdefault(col, spec["key_round"])
    for col, digits in rounding.items():
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(int(digits))
    return df


def _coerce_column(series: pd.Series, dtype: str) -> pd.Series:
    """Coerce one column toward the declared dtype. Invalid → NaN/<NA> (not raise)."""
    if dtype.startswith("datetime64"):
        utc = "UTC" in dtype
        return pd.to_datetime(series, errors="coerce", utc=utc)
    if dtype in ("int64", "Int64"):
        return pd.to_numeric(series, errors="coerce")
    if dtype in ("float64", "float"):
        return pd.to_numeric(series, errors="coerce")
    if dtype == "boolean":
        return series.astype("boolean")
    if dtype == "string":
        return series.astype("string")
    return series


def _eval_rule(df: pd.DataFrame, rule: str) -> Optional[pd.Series]:
    """Return per-row violation mask for a semantic rule, or None if unevaluable.

    Rule form: "ASSERT" or "ASSERT when CONDITION". A row violates when CONDITION
    holds (default: all rows) and ASSERT is false.
    """
    if " when " in rule:
        assert_expr, cond_expr = rule.split(" when ", 1)
    else:
        assert_expr, cond_expr = rule, None
    try:
        assert_mask = df.eval(assert_expr, engine="python")
        assert_mask = pd.Series(assert_mask, index=df.index).fillna(False).astype(bool)
        if cond_expr is not None:
            cond_mask = df.eval(cond_expr, engine="python")
            cond_mask = pd.Series(cond_mask, index=df.index).fillna(False).astype(bool)
        else:
            cond_mask = pd.Series(True, index=df.index)
    except Exception:
        return None
    return cond_mask & ~assert_mask


def _pit_violation(df: pd.DataFrame, pit_cfg: dict) -> Optional[pd.Series]:
    """available_at must be >= as_of_date (knowledge not before validity).

    Handles tz-aware available_at vs tz-naive as_of_date explicitly.
    """
    as_of_col = pit_cfg.get("as_of_col", "as_of_date")
    avail_col = pit_cfg.get("available_col", "available_at")
    if as_of_col not in df.columns or avail_col not in df.columns:
        return None
    asof = pd.to_datetime(df[as_of_col], errors="coerce")
    if getattr(asof.dt, "tz", None) is None:
        asof = asof.dt.tz_localize("UTC")
    else:
        asof = asof.dt.tz_convert("UTC")
    avail = pd.to_datetime(df[avail_col], errors="coerce", utc=True)
    # Violation when available strictly before the data's own valid date.
    return (avail.notna() & asof.notna() & (avail < asof)).fillna(False)


# ── validation ───────────────────────────────────────────────────────────────

def validate(
    df: pd.DataFrame,
    contract: dict,
    symbology=None,
) -> ContractResult:
    """Validate df against contract; route failing rows to quarantine."""
    df = df.copy()
    enforcement = contract.get("enforcement", "warn")
    rows_in = len(df)

    report: dict = {
        "contract_id": contract.get("contract_id"),
        "version": contract.get("version"),
        "tier": contract.get("tier"),
        "enforcement": enforcement,
        "rows_in": rows_in,
        "structural": {"coerced": [], "missing_required": [], "failures": {}},
        "semantic": {"checked": [], "skipped": [], "violations": {}},
        "symbology": {"checked": False, "orphans": [], "orphan_rows": 0},
        "pit": {"checked": False, "violations": 0},
        "distributional": {},
        "frame_breaks": [],
    }

    # Per-row reason accumulator (semicolon-joined for multi-fail rows).
    reasons = pd.Series("", index=df.index)

    def _divert(mask: pd.Series, reason: str):
        mask = mask.reindex(df.index, fill_value=False).fillna(False).astype(bool)
        if mask.any():
            reasons.loc[mask] = reasons.loc[mask] + reason + ";"

    # 1. snap float keys before any comparison/join
    df = _snap_float_keys(df, contract)

    # 2. structural — coerce dtypes, route null-in-required to quarantine
    cols_spec = (contract.get("structural") or {}).get("columns", {}) or {}
    for col, spec in cols_spec.items():
        dtype = spec.get("dtype")
        nullable = spec.get("nullable", True)
        if col not in df.columns:
            if not nullable:
                report["structural"]["missing_required"].append(col)
            continue
        df[col] = _coerce_column(df[col], dtype)
        report["structural"]["coerced"].append(col)
        if not nullable:
            bad = df[col].isna()  # any null in a required col (pre-existing or coercion-induced)
            if bad.any():
                report["structural"]["failures"][col] = int(bad.sum())
                _divert(bad, f"structural:{col}")

    # 3. point-in-time
    pit_cfg = contract.get("pit")
    if pit_cfg:
        pit_mask = _pit_violation(df, pit_cfg)
        if pit_mask is not None:
            report["pit"]["checked"] = True
            report["pit"]["violations"] = int(pit_mask.sum())
            _divert(pit_mask, "pit:available_before_as_of")

    # 4. symbology orphans (audit H2)
    sym_cfg = contract.get("symbology") or {}
    if sym_cfg.get("enforce_no_orphans") and symbology is not None:
        key = sym_cfg.get("key", "product_id")
        if key in df.columns:
            try:
                orphans = symbology.validate_no_orphans(df)
            except Exception:
                orphans = []
            report["symbology"]["checked"] = True
            report["symbology"]["orphans"] = [int(o) for o in orphans]
            if orphans:
                mask = df[key].isin(orphans)
                report["symbology"]["orphan_rows"] = int(mask.sum())
                _divert(mask, "symbology:orphan")

    # 5. semantic rules
    for entry in contract.get("semantic", []) or []:
        rule = entry.get("rule")
        reason = entry.get("reason", "semantic")
        mask = _eval_rule(df, rule)
        if mask is None:
            report["semantic"]["skipped"].append(rule)
            continue
        report["semantic"]["checked"].append(rule)
        n = int(mask.sum())
        if n:
            report["semantic"]["violations"][reason] = n
            _divert(mask, f"semantic:{reason}")

    # 6. distributional (frame-level)
    for entry in contract.get("distributional", []) or []:
        col = entry.get("col")
        check = entry.get("check")
        key = f"{col}:{check}"
        if col not in df.columns:
            report["distributional"][key] = {"status": "skipped_missing_col"}
            continue
        if check == "null_rate":
            rate = float(df[col].isna().mean())
            mx = float(entry.get("max", 0.0))
            status = "pass" if rate <= mx else "break"
            report["distributional"][key] = {"status": status, "value": rate, "max": mx}
            if status == "break":
                report["frame_breaks"].append(
                    {"type": "drift", "check": key, "value": rate, "max": mx}
                )
        elif check == "psi":
            # PSI needs a reference vintage (P1 bitemporal store). Not yet available.
            report["distributional"][key] = {
                "status": "not_evaluated",
                "note": "needs reference vintage (P1)",
            }
        else:
            report["distributional"][key] = {"status": "unknown_check"}

    # split passed / quarantined
    q_mask = reasons.str.len() > 0
    quarantined = df[q_mask].copy()
    if not quarantined.empty:
        quarantined[QUARANTINE_REASON_COL] = reasons[q_mask].str.rstrip(";")
        quarantined["_contract_id"] = contract.get("contract_id")
        quarantined["_contract_version"] = contract.get("version")
        quarantined["_tier"] = contract.get("tier")
    passed = df[~q_mask].copy()

    rows_q = int(q_mask.sum())
    report["rows_passed"] = rows_in - rows_q
    report["rows_quarantined"] = rows_q
    report["quarantine_rate"] = (rows_q / rows_in) if rows_in else 0.0
    report["quarantine_by_reason"] = _reason_counts(reasons[q_mask])

    # enforcement: block raises on frame-level breaks (missing required col / drift)
    if enforcement == "block":
        blocking = list(report["frame_breaks"])
        if report["structural"]["missing_required"]:
            blocking.append(
                {"type": "structural", "missing_required": report["structural"]["missing_required"]}
            )
        if blocking:
            raise ContractViolation(
                f"contract {contract.get('contract_id')} v{contract.get('version')} "
                f"frame-level break under enforcement=block: {blocking}"
            )

    return ContractResult(passed=passed, quarantined=quarantined, report=report)


def _reason_counts(reason_series: pd.Series) -> dict:
    counts: dict = {}
    for raw in reason_series:
        for r in str(raw).rstrip(";").split(";"):
            if r:
                counts[r] = counts.get(r, 0) + 1
    return counts


def validate_for_cfg(
    df: pd.DataFrame,
    cfg: dict,
    symbology=None,
    contracts_dir: Path | str = DEFAULT_CONTRACTS_DIR,
) -> Optional[ContractResult]:
    """Resolve the contract for a pipeline cfg and validate. None if no contract."""
    resolved = resolve_contract_id(cfg)
    if resolved is None:
        return None
    contract_id, version = resolved
    try:
        contract = load_contract(contract_id, version, contracts_dir)
    except FileNotFoundError:
        return None
    return validate(df, contract, symbology=symbology)
