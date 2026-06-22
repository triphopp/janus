"""Diff ledger policy evaluation — streaming JSONL summarizer.

Produces outputs/diff/<run_id>_summary.json from a raw *_changes.jsonl
ledger without loading the entire file into memory.

Policy source: Policy/diff_ledger_review_policy.md
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

# ── Protected column sets ─────────────────────────────────────────────────────

KEY_COLUMNS: frozenset[str] = frozenset({
    "as_of_date", "date", "timestamp", "available_time", "knowledge_time",
    "symbol", "instrument", "product_id", "contract_root", "hub",
    "instrument_type", "delivery_month", "expiry", "right", "strike",
})

LABEL_COLUMNS: frozenset[str] = frozenset({
    "label", "target", "y", "label_end_time", "label_end_date",
    "future_return", "forward_return", "realized_return", "realized_vol",
})

CANONICAL_MARKET_COLUMNS: frozenset[str] = frozenset({
    "raw_close", "close", "settlement", "price", "price_std",
    "return_raw", "return_std", "iv", "iv_provided", "iv_solved",
    "delta", "gamma", "vega", "theta", "rho",
    "dte", "dte_days", "T", "underlying_price", "F", "S",
})

PROTECTED_COLUMNS: frozenset[str] = KEY_COLUMNS | LABEL_COLUMNS | CANONICAL_MARKET_COLUMNS

# Column families for numeric materiality grouping
_PRICE_LIKE = frozenset({"price", "price_std", "close", "settlement", "raw_close"})
_RETURN_LIKE = frozenset({"return_std", "return_raw", "return_winsorized"})
_IV_LIKE     = frozenset({"iv", "iv_provided", "iv_solved"})
_DELTA_LIKE  = frozenset({"delta"})

# ── Default policy ────────────────────────────────────────────────────────────

def default_policy() -> dict:
    return {
        "policy_version": 1,
        "inline_diff_max_bytes": 10 * 1024 * 1024,
        "hard_gates": {
            "key_mutation_count": 0,
            "label_mutation_count": 0,
            "protected_unattributed_count": 0,
            "unexplained_row_drop_count": 0,
            "protected_schema_drop_count": 0,
        },
        "budgets": {
            "non_protected_unattributed": {
                "warn_count": 1,
                "fail_count": 50,
                "fail_rate": 0.0001,
            },
            "unknown_schema_add": {"warn_count": 1, "fail_count": 10},
            "row_add_stage_diff": {"pass_rate": 0.0, "fail_rate": 0.0001},
        },
        "expected_filter_rate": {
            "equity": 0.01,
            "futures": 0.01,
            "equity_options": 0.20,
            "futures_options": 0.20,
            "validators": 0.05,
        },
        "materiality": {
            "price_like": {"warn_p99_abs_pct": 0.01, "fail_p99_abs_pct": 0.05},
            "return_like": {"warn_p99_abs_delta": 0.01, "fail_p99_abs_delta": 0.05},
            "iv_like": {"warn_p99_abs_delta": 0.05, "fail_p99_abs_delta": 0.20},
            "delta_like": {"warn_p99_abs_delta": 0.05, "fail_p99_abs_delta": 0.20},
        },
        "max_protected_samples": 50,
        "max_unattributed_samples": 50,
        "max_numeric_samples_per_family": 50,
        "max_row_drop_samples_per_reason": 20,
        "max_row_add_samples": 20,
        "max_strata_samples": 5,
        "enforce": "warn",
    }


# ── JSONL streaming helper (local copy — do not import from web/) ─────────────

def _iter_jsonl(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                yield None  # caller counts as malformed


# ── Column family classifier ──────────────────────────────────────────────────

def _col_family(col: str | None) -> str | None:
    if col is None:
        return None
    if col in _PRICE_LIKE:
        return "price_like"
    if col in _RETURN_LIKE:
        return "return_like"
    if col in _IV_LIKE:
        return "iv_like"
    if col in _DELTA_LIKE:
        return "delta_like"
    return "other_numeric"


# ── Deterministic sample key ──────────────────────────────────────────────────

def _sample_key(run_id: str, rec: dict) -> str:
    parts = [
        run_id,
        rec.get("stage_from") or "",
        rec.get("stage_to") or "",
        rec.get("change_type") or "",
        rec.get("reason") or "",
        rec.get("column") or "",
        json.dumps(rec.get("key") or {}, sort_keys=True),
    ]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _maybe_add_sample(bucket: list, rec: dict, key: str, limit: int) -> None:
    if len(bucket) < limit:
        bucket.append({"_sample_key": key, **rec})
    else:
        # keep lowest hash — deterministic selection
        if key < bucket[-1].get("_sample_key", "z" * 64):
            bucket[-1] = {"_sample_key": key, **rec}
            bucket.sort(key=lambda r: r.get("_sample_key", ""))


# ── Percentile helpers ────────────────────────────────────────────────────────

def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = int(len(s) * p / 100)
    return s[min(idx, len(s) - 1)]


# ── Core summarizer ───────────────────────────────────────────────────────────

def summarize_ledger(
    ledger_path: Path,
    *,
    run_id: str | None = None,
    policy: dict | None = None,
    context: dict | None = None,
    baseline: dict | None = None,
) -> dict:
    """Stream ledger_path and return a policy summary dict.

    Never calls Path.read_text() on the ledger — always streams line by line.
    """
    ledger_path = Path(ledger_path)
    pol = default_policy()
    if policy:
        pol.update(policy)
    ctx = context or {}
    rid = run_id or ledger_path.stem.replace("_changes", "")

    ledger_bytes = ledger_path.stat().st_size if ledger_path.exists() else 0

    # ── Accumulators ─────────────────────────────────────────────────────────
    total_records = 0
    malformed_lines = 0

    by_stage: dict[str, int] = {}
    by_change_type: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    by_column: dict[str, int] = {}
    by_stage_change_type: dict[str, dict[str, int]] = {}
    by_stage_reason: dict[str, dict[str, int]] = {}
    by_column_reason: dict[str, dict[str, int]] = {}

    protected: dict[str, Any] = {
        "key_mutations": 0,
        "label_mutations": 0,
        "protected_unattributed": 0,
        "protected_schema_drop": 0,
        "unexplained_row_drop": 0,
    }

    non_protected_unattributed = 0
    unknown_schema_add = 0
    row_add_count = 0
    row_drop_count = 0
    schema_drop_count = 0
    cell_mod_count = 0

    # Numeric materiality: col → {family, abs_delta[], abs_pct[]}
    numeric: dict[str, dict] = {}

    # Samples
    max_ps  = pol.get("max_protected_samples", 50)
    max_ua  = pol.get("max_unattributed_samples", 50)
    max_nf  = pol.get("max_numeric_samples_per_family", 50)
    max_rd  = pol.get("max_row_drop_samples_per_reason", 20)
    max_ra  = pol.get("max_row_add_samples", 20)
    max_st  = pol.get("max_strata_samples", 5)

    samples: dict[str, list] = {
        "protected_mutations": [],
        "unattributed": [],
        "row_drops": [],
        "row_adds": [],
        "strata": [],
    }
    numeric_samples: dict[str, list] = {}  # family → []
    row_drop_by_reason: dict[str, list] = {}

    findings: list[dict] = []

    def _add_finding(code: str, status: str, detail: str, rec: dict | None = None) -> None:
        findings.append({
            "code": code,
            "status": status,
            "detail": detail,
            "example": rec,
        })

    # ── Stream ────────────────────────────────────────────────────────────────
    if not ledger_path.exists():
        return _final(rid, "degraded", ledger_path, ledger_bytes, 0, 0, {}, {}, {}, {}, {},
                      protected, {}, {}, samples,
                      [{"code": "LEDGER_MISSING", "status": "degraded",
                        "detail": f"ledger not found: {ledger_path}", "example": None}],
                      pol, ctx, baseline)

    for raw in _iter_jsonl(ledger_path):
        if raw is None:
            malformed_lines += 1
            continue
        total_records += 1

        sf   = raw.get("stage_from") or ""
        st   = raw.get("stage_to") or ""
        ct   = raw.get("change_type") or ""
        col  = raw.get("column")
        rsn  = raw.get("reason") or ""
        dlt  = raw.get("delta")
        pct_ = raw.get("pct")
        stage_key = f"{sf}->{st}"

        # ── Rollups ───────────────────────────────────────────────────────────
        by_stage[stage_key] = by_stage.get(stage_key, 0) + 1
        by_change_type[ct]  = by_change_type.get(ct, 0) + 1
        if rsn:
            by_reason[rsn] = by_reason.get(rsn, 0) + 1
        if col:
            by_column[col] = by_column.get(col, 0) + 1

        bsct = by_stage_change_type.setdefault(stage_key, {})
        bsct[ct] = bsct.get(ct, 0) + 1

        bsr = by_stage_reason.setdefault(stage_key, {})
        if rsn:
            bsr[rsn] = bsr.get(rsn, 0) + 1

        if col and rsn:
            bcr = by_column_reason.setdefault(col, {})
            bcr[rsn] = bcr.get(rsn, 0) + 1

        # ── Hard gate checks ──────────────────────────────────────────────────
        skey = _sample_key(rid, raw)

        if ct == "cell_mod":
            cell_mod_count += 1
            if col in KEY_COLUMNS:
                protected["key_mutations"] += 1
                _maybe_add_sample(samples["protected_mutations"], raw, skey, max_ps)
            elif col in LABEL_COLUMNS:
                protected["label_mutations"] += 1
                _maybe_add_sample(samples["protected_mutations"], raw, skey, max_ps)
            elif col in CANONICAL_MARKET_COLUMNS and rsn == "UNATTRIBUTED":
                protected["protected_unattributed"] += 1
                _maybe_add_sample(samples["protected_mutations"], raw, skey, max_ps)
            elif col not in PROTECTED_COLUMNS and rsn == "UNATTRIBUTED":
                non_protected_unattributed += 1
                _maybe_add_sample(samples["unattributed"], raw, skey, max_ua)

        elif ct == "row_drop":
            row_drop_count += 1
            if not rsn:
                protected["unexplained_row_drop"] += 1
            bkt = row_drop_by_reason.setdefault(rsn or "_none", [])
            _maybe_add_sample(bkt, raw, skey, max_rd)

        elif ct == "row_add":
            row_add_count += 1
            _maybe_add_sample(samples["row_adds"], raw, skey, max_ra)

        elif ct == "schema_drop":
            schema_drop_count += 1
            if col in PROTECTED_COLUMNS:
                protected["protected_schema_drop"] += 1
                _maybe_add_sample(samples["protected_mutations"], raw, skey, max_ps)

        elif ct == "schema_add":
            # reason=None/empty on schema_add is normal (adapter-derived columns
            # like iv, delta, price_std, _outlier_flag carry no explicit reason).
            # Only flag when an explicit non-empty reason is present but unrecognized.
            if rsn and rsn not in ("adapter_universe_filter", "pit_mad_derived",
                                   "outlier_cap", "outlier_policy", "pit_mad_outlier",
                                   "pit_mad_threshold", "return_clip_filter",
                                   "validator_or_filter"):
                unknown_schema_add += 1

        # ── Numeric materiality ───────────────────────────────────────────────
        if ct == "cell_mod" and col and (dlt is not None or pct_ is not None):
            family = _col_family(col)
            if family:
                entry = numeric.setdefault(col, {"family": family, "n": 0,
                                                  "abs_delta": [], "abs_pct": []})
                entry["n"] += 1
                if dlt is not None:
                    try:
                        entry["abs_delta"].append(abs(float(dlt)))
                    except (TypeError, ValueError):
                        pass
                if pct_ is not None:
                    try:
                        entry["abs_pct"].append(abs(float(pct_)))
                    except (TypeError, ValueError):
                        pass
                fam_samp = numeric_samples.setdefault(family, [])
                _maybe_add_sample(fam_samp, raw, skey, max_nf)

        # ── Strata sample ─────────────────────────────────────────────────────
        _maybe_add_sample(samples["strata"], raw, skey, max_st)

    # ── Flatten row_drop samples ──────────────────────────────────────────────
    samples["row_drops"] = []
    for bkt in row_drop_by_reason.values():
        samples["row_drops"].extend(bkt)

    # ── Numeric materiality stats ─────────────────────────────────────────────
    numeric_delta_stats: dict[str, dict] = {}
    for col, entry in numeric.items():
        ad = entry["abs_delta"]
        ap = entry["abs_pct"]
        numeric_delta_stats[col] = {
            "family": entry["family"],
            "n": entry["n"],
            "max_abs_delta": max(ad) if ad else None,
            "p95_abs_delta": _pct(ad, 95),
            "p99_abs_delta": _pct(ad, 99),
            "max_abs_pct":   max(ap) if ap else None,
            "p99_abs_pct":   _pct(ap, 99),
        }

    # ── Rates ─────────────────────────────────────────────────────────────────
    denom = total_records or 1
    denom_confidence = "exact" if total_records else "degraded_total_records_proxy"
    rates = {
        "unattributed_rate":           (by_reason.get("UNATTRIBUTED", 0)) / denom,
        "protected_unattributed_rate": protected["protected_unattributed"] / denom,
        "row_drop_rate":               row_drop_count / denom,
        "row_add_rate":                row_add_count / denom,
        "schema_drop_rate":            schema_drop_count / denom,
        "malformed_rate":              malformed_lines / max(total_records + malformed_lines, 1),
        "denominator_confidence":      denom_confidence,
    }

    return _final(
        rid, None, ledger_path, ledger_bytes, total_records, malformed_lines,
        by_stage, by_change_type, by_reason, by_column,
        {
            "by_stage_change_type": by_stage_change_type,
            "by_stage_reason": by_stage_reason,
            "by_column_reason": by_column_reason,
        },
        protected, rates, numeric_delta_stats, samples,
        findings, pol, ctx, baseline,
        extras={
            "non_protected_unattributed": non_protected_unattributed,
            "unknown_schema_add": unknown_schema_add,
            "row_add_count": row_add_count,
            "row_drop_count": row_drop_count,
            "numeric_samples": numeric_samples,
        },
    )


def _final(
    run_id, status_override, ledger_path, ledger_bytes, total_records, malformed_lines,
    by_stage, by_change_type, by_reason, by_column, cross_rollups,
    protected, rates, numeric_delta_stats, samples, findings, pol, ctx, baseline,
    extras=None,
) -> dict:
    extras = extras or {}
    pol_hard = pol.get("hard_gates", {})
    pol_bud  = pol.get("budgets", {})
    pol_mat  = pol.get("materiality", {})

    final_findings = list(findings)

    # ── Degrade checks ────────────────────────────────────────────────────────
    if malformed_lines > 0:
        final_findings.append({
            "code": "MALFORMED_JSONL",
            "status": "degraded",
            "detail": f"{malformed_lines} malformed JSONL line(s)",
            "example": None,
        })

    # ── Hard gate findings ────────────────────────────────────────────────────
    if protected.get("key_mutations", 0) > pol_hard.get("key_mutation_count", 0):
        final_findings.append({
            "code": "KEY_MUTATION",
            "status": "fail",
            "detail": f"{protected['key_mutations']} cell_mod on key column(s)",
            "example": next((s for s in samples.get("protected_mutations", [])
                             if s.get("column") in KEY_COLUMNS), None),
        })

    if protected.get("label_mutations", 0) > pol_hard.get("label_mutation_count", 0):
        final_findings.append({
            "code": "LABEL_MUTATION",
            "status": "fail",
            "detail": f"{protected['label_mutations']} cell_mod on label column(s)",
            "example": next((s for s in samples.get("protected_mutations", [])
                             if s.get("column") in LABEL_COLUMNS), None),
        })

    if protected.get("protected_unattributed", 0) > pol_hard.get("protected_unattributed_count", 0):
        final_findings.append({
            "code": "PROTECTED_UNATTRIBUTED",
            "status": "fail",
            "detail": f"{protected['protected_unattributed']} UNATTRIBUTED on canonical market columns",
            "example": samples.get("protected_mutations", [None])[0] if samples.get("protected_mutations") else None,
        })

    if protected.get("protected_schema_drop", 0) > pol_hard.get("protected_schema_drop_count", 0):
        final_findings.append({
            "code": "PROTECTED_SCHEMA_DROP",
            "status": "fail",
            "detail": f"{protected['protected_schema_drop']} schema_drop on protected column(s)",
            "example": None,
        })

    if protected.get("unexplained_row_drop", 0) > pol_hard.get("unexplained_row_drop_count", 0):
        final_findings.append({
            "code": "UNEXPLAINED_ROW_DROP",
            "status": "fail",
            "detail": f"{protected['unexplained_row_drop']} row_drop with no reason",
            "example": None,
        })

    # ── Budget findings ───────────────────────────────────────────────────────
    npu = extras.get("non_protected_unattributed", 0)
    npu_rate = npu / max(total_records, 1)
    npu_bud = pol_bud.get("non_protected_unattributed", {})
    _fail_rate = npu_bud.get("fail_rate", 0.0001)
    # Rate check only meaningful when ledger is large enough to reach the threshold by count alone
    _rate_applicable = total_records >= int(1 / max(_fail_rate, 1e-9))
    if npu >= npu_bud.get("fail_count", 50) or (_rate_applicable and npu_rate >= _fail_rate):
        final_findings.append({
            "code": "UNATTRIBUTED_BUDGET_FAIL",
            "status": "fail",
            "detail": f"non-protected UNATTRIBUTED count {npu} exceeds fail threshold",
            "example": samples.get("unattributed", [None])[0] if samples.get("unattributed") else None,
        })
    elif npu >= npu_bud.get("warn_count", 1):
        final_findings.append({
            "code": "UNATTRIBUTED_BUDGET_WARN",
            "status": "warn",
            "detail": f"non-protected UNATTRIBUTED count {npu} requires review",
            "example": samples.get("unattributed", [None])[0] if samples.get("unattributed") else None,
        })

    usa = extras.get("unknown_schema_add", 0)
    usa_bud = pol_bud.get("unknown_schema_add", {})
    if usa >= usa_bud.get("fail_count", 10):
        final_findings.append({"code": "UNKNOWN_SCHEMA_ADD_FAIL", "status": "fail",
                                "detail": f"{usa} schema_add with unrecognized reason", "example": None})
    elif usa >= usa_bud.get("warn_count", 1):
        final_findings.append({"code": "UNKNOWN_SCHEMA_ADD_WARN", "status": "warn",
                                "detail": f"{usa} schema_add with unrecognized reason", "example": None})

    row_add = extras.get("row_add_count", 0)
    ra_bud = pol_bud.get("row_add_stage_diff", {})
    if total_records and row_add / total_records > ra_bud.get("fail_rate", 0.0001):
        final_findings.append({"code": "UNAPPROVED_ROW_ADD", "status": "fail",
                                "detail": f"row_add rate {row_add / total_records:.6f} exceeds budget",
                                "example": samples.get("row_adds", [None])[0] if samples.get("row_adds") else None})

    # ── Materiality findings ──────────────────────────────────────────────────
    for col, stats in numeric_delta_stats.items():
        fam = stats.get("family")
        fam_pol = pol_mat.get(fam, {})
        if not fam_pol:
            continue
        p99d = stats.get("p99_abs_delta")
        p99p = stats.get("p99_abs_pct")
        fail_d = fam_pol.get("fail_p99_abs_delta")
        warn_d = fam_pol.get("warn_p99_abs_delta")
        fail_p = fam_pol.get("fail_p99_abs_pct")
        warn_p = fam_pol.get("warn_p99_abs_pct")
        mat_status = None
        if (fail_d and p99d and p99d >= fail_d) or (fail_p and p99p and p99p >= fail_p):
            mat_status = "fail"
        elif (warn_d and p99d and p99d >= warn_d) or (warn_p and p99p and p99p >= warn_p):
            mat_status = "warn"
        if mat_status:
            code = "MATERIALITY_FAIL" if mat_status == "fail" else "MATERIALITY_WARN"
            final_findings.append({
                "code": code,
                "status": mat_status,
                "detail": f"column '{col}' ({fam}): p99_abs_delta={p99d}, p99_abs_pct={p99p}",
                "example": None,
            })

    # ── Baseline anomaly ──────────────────────────────────────────────────────
    if baseline and baseline.get("comparable_runs", 0) >= 10:
        hist_rates = baseline.get("rates", {})
        for metric_key, current_val in [
            ("row_drop_rate", rates.get("row_drop_rate", 0)),
            ("unattributed_rate", rates.get("unattributed_rate", 0)),
        ]:
            hist = hist_rates.get(metric_key, [])
            if len(hist) >= 10:
                import statistics
                med = statistics.median(hist)
                mad = statistics.median([abs(v - med) for v in hist]) or 1e-9
                z = 0.6745 * abs(current_val - med) / mad
                if z > 3.5:
                    final_findings.append({"code": "BASELINE_ROBUST_Z_FAIL", "status": "fail",
                                           "detail": f"{metric_key}: robust_z={z:.2f} (median={med:.6f})", "example": None})
                elif z > 2.5:
                    final_findings.append({"code": "BASELINE_ROBUST_Z_WARN", "status": "warn",
                                           "detail": f"{metric_key}: robust_z={z:.2f} (median={med:.6f})", "example": None})

    # ── Status precedence: degraded > fail > warn > pass ─────────────────────
    if status_override:
        status = status_override
    else:
        codes = {f["status"] for f in final_findings}
        if "degraded" in codes:
            status = "degraded"
        elif "fail" in codes:
            status = "fail"
        elif "warn" in codes:
            status = "warn"
        else:
            status = "pass"

    return {
        "policy_version": pol.get("policy_version", 1),
        "run_id": run_id,
        "status": status,
        "ledger": {
            "path": str(ledger_path),
            "bytes": ledger_bytes,
            "total_records": total_records,
            "malformed_lines": malformed_lines,
        },
        "rollups": {
            "by_stage": by_stage,
            "by_change_type": by_change_type,
            "by_reason": by_reason,
            "by_column": by_column,
            **cross_rollups,
        },
        "protected": {
            "key_mutations": protected.get("key_mutations", 0),
            "label_mutations": protected.get("label_mutations", 0),
            "protected_unattributed": protected.get("protected_unattributed", 0),
            "protected_schema_drop": protected.get("protected_schema_drop", 0),
            "unexplained_row_drop": protected.get("unexplained_row_drop", 0),
        },
        "rates": rates,
        "budgets": {
            "non_protected_unattributed": extras.get("non_protected_unattributed", 0),
            "unknown_schema_add": extras.get("unknown_schema_add", 0),
            "row_add_count": extras.get("row_add_count", 0),
            "row_drop_count": extras.get("row_drop_count", 0),
        },
        "numeric_delta_stats": numeric_delta_stats,
        "samples": {k: [_strip_sample_key(s) for s in v]
                    for k, v in samples.items()},
        "findings": final_findings,
        "context": ctx,
        "baseline": {"available": bool(baseline and baseline.get("comparable_runs", 0) >= 10)},
    }


def _strip_sample_key(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k != "_sample_key"}


# ── Public write helper ───────────────────────────────────────────────────────

def write_diff_summary(
    ledger_path: Path,
    *,
    run_id: str | None = None,
    out_dir: Path | str | None = None,
    policy: dict | None = None,
    context: dict | None = None,
    baseline: dict | None = None,
) -> str:
    """Summarize ledger and write <run_id>_summary.json beside the ledger.

    Returns the path written as a string.
    """
    import datetime
    ledger_path = Path(ledger_path)
    if out_dir is None:
        out_dir = ledger_path.parent
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rid = run_id or ledger_path.stem.replace("_changes", "")
    summary = summarize_ledger(ledger_path, run_id=rid, policy=policy,
                               context=context, baseline=baseline)
    summary["generated_at"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    out_path = out_dir / f"{rid}_summary.json"
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return str(out_path)


def is_summary_fresh(ledger_path: Path, summary_path: Path) -> bool:
    """Return True if summary_path exists and is newer than ledger_path."""
    if not summary_path.exists():
        return False
    if not ledger_path.exists():
        return True
    return summary_path.stat().st_mtime >= ledger_path.stat().st_mtime
