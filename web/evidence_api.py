"""Evidence API — FastAPI router for the evidence graph dashboard.

Routes:
  POST /api/evidence/run                           → trigger harness run (background task)
  GET  /api/evidence/cases                         → list cases (filter: status, run_id, signal_type, verdict)
  GET  /api/evidence/cases/{case_id}               → one case detail + sources + checks
  GET  /api/evidence/cases/{case_id}/graph         → nodes + edges (graph traversal payload)
  GET  /api/evidence/cases/{case_id}/timeline      → ordered node chain for UI rendering
  GET  /api/evidence/cases/{case_id}/status        → job + DB status combined
  POST /api/evidence/cases/{case_id}/review        → record review action, update status
  GET  /api/runs/{run_id}/outliers                 → outliers from parquet + evidence status
  GET  /healthz/evidence                           → DB reachability probe

When JANUS_EVIDENCE_DATABASE_URL is not set, read-only endpoints fall back to JSON snapshots.
Set JANUS_EVIDENCE_CONFIG to point to a HarnessConfig YAML file.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

_VALID_ACTIONS: set[str] = {
    "mark_supported_event",
    "mark_unsupported",
    "mark_suspected_bad_tick",
    "mark_vendor_conflict",
    "waive_with_reason",
    "escalate",
    "close",
}

_ACTION_TO_STATUS: dict[str, str] = {
    "mark_supported_event":    "supported_event",
    "mark_unsupported":        "unsupported",
    "mark_suspected_bad_tick": "suspected_bad_tick",
    "mark_vendor_conflict":    "vendor_conflict",
    "waive_with_reason":       "waived",
    "escalate":                "investigating",
    "close":                   "closed",
}


# ── Pydantic models ───────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    """Trigger a harness run for one outlier case."""
    run_id: str
    case_id: str
    instrument: str
    family: str = "equity"
    symbol: str | None = None
    as_of_date: str
    signal_type: str = "return_outlier"
    z_score: float | None = None
    severity: str | None = None
    observed_value: float | None = None
    pct_change: float | None = None
    candidate_terms: list[str] = []
    source_hints: list[str] = []


class ReviewRequest(BaseModel):
    action: str
    actor: str = "analyst"
    reason: str = ""


# ── In-memory job tracker ────────────────────────────────────────────────────
# {case_id: {status, started_at, finished_at, verdict, error}}

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _job_set(case_id: str, **kwargs: Any) -> None:
    with _JOBS_LOCK:
        entry = _JOBS.setdefault(case_id, {})
        entry.update(kwargs)


def _job_get(case_id: str) -> dict | None:
    with _JOBS_LOCK:
        return dict(_JOBS.get(case_id, {})) if case_id in _JOBS else None


# ── Store factory (lazy, module-level singleton) ──────────────────────────────

_store = None


def _get_store():
    global _store
    if _store is not None:
        return _store
    dsn = os.environ.get("JANUS_EVIDENCE_DATABASE_URL")
    if not dsn:
        raise HTTPException(
            status_code=503,
            detail="Evidence graph not configured. Set JANUS_EVIDENCE_DATABASE_URL.",
        )
    try:
        from core.evidence_harness.graph_store import PostgresGraphStore
        _store = PostgresGraphStore(dsn=dsn)
        return _store
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Evidence DB unavailable: {exc}") from exc


def _reset_store():
    """For testing — clear the module-level singleton."""
    global _store
    _store = None


# ── Config + graph sink helpers ───────────────────────────────────────────────

_DEFAULT_EQUITY_TIERS = {
    "tier1_official": [
        "sec.gov", "edgar.sec.gov", "investor.apple.com",
        "investor.google.com", "ir.tesla.com",
    ],
    "tier2_reputable": [
        "reuters.com", "bloomberg.com", "wsj.com", "ft.com",
        "marketwatch.com", "cnbc.com", "apnews.com", "barrons.com",
        "nasdaq.com", "finance.yahoo.com", "seekingalpha.com",
        "apple.com", "9to5mac.com", "macrumors.com",
    ],
    "tier3_general": [
        "forbes.com", "businessinsider.com", "techcrunch.com",
        "investopedia.com", "motleyfool.com", "zacks.com",
        "cultofmac.com", "macobserver.com",
    ],
    "tier4_social": ["reddit.com", "x.com", "twitter.com"],
}


def _load_run_config():
    """Return HarnessConfig for live runs — reads JANUS_EVIDENCE_CONFIG if set."""
    from core.evidence_harness.config import load_harness_config
    from dataclasses import replace as dc_replace
    cfg_path = os.environ.get("JANUS_EVIDENCE_CONFIG")
    cfg = load_harness_config(cfg_path)
    # Ensure live mode and sensible defaults when triggered from dashboard
    changes: dict = {}
    if cfg.mode == "mock":
        changes.update(mode="live", enabled=True,
                       search_provider="duckduckgo", fetch_provider="httpx")
    if not cfg.source_tiers:
        changes["source_tiers"] = _DEFAULT_EQUITY_TIERS
    if changes:
        cfg = dc_replace(cfg, **changes)
    return cfg


def _graph_dir() -> Path:
    return Path(os.environ.get("JANUS_EVIDENCE_GRAPH_DIR", "outputs/evidence/graphs"))


def _run_harness_task(req: RunRequest) -> None:
    """Background task: run harness, write results, update job tracker."""
    from core.evidence_harness.schema import OutlierCasePackage
    from core.evidence_harness.controller import run_harness
    from core.evidence_harness.graph_builder import build_graph
    from core.evidence_harness.graph_adapter import make_graph_sink

    case_id = req.case_id
    _job_set(case_id, status="running",
             started_at=datetime.now(timezone.utc).isoformat())
    try:
        case = OutlierCasePackage(
            case_id=case_id,
            run_id=req.run_id,
            signal_type=req.signal_type,
            as_of_date=req.as_of_date,
            instrument=req.instrument,
            family=req.family,
            symbol=req.symbol or req.instrument,
            severity=req.severity,
            observed_value=req.observed_value,
            z_score=req.z_score,
            pct_change=req.pct_change,
            candidate_terms=req.candidate_terms,
            source_hints=req.source_hints,
        )
        cfg = _load_run_config()

        result = run_harness(case, cfg)

        dsn = os.environ.get("JANUS_EVIDENCE_DATABASE_URL")
        backend = "postgres" if dsn else "json"
        sink = make_graph_sink(
            backend=backend,
            dsn=dsn,
            graph_dir=str(_graph_dir()),
            write_json_snapshot=True,
        )
        graph = build_graph(result)
        sink.write(result)
        sink.close()

        _job_set(case_id,
                 status="done",
                 finished_at=datetime.now(timezone.utc).isoformat(),
                 verdict=result.verdict,
                 confidence=result.confidence,
                 graph_path=str(getattr(sink, "last_path", lambda: None)() or ""),
                 error=None)

    except Exception as exc:
        _job_set(case_id,
                 status="error",
                 finished_at=datetime.now(timezone.utc).isoformat(),
                 error=str(exc))


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/run")
def trigger_run(req: RunRequest, background_tasks: BackgroundTasks):
    """Trigger a harness investigation for one outlier case (runs in background)."""
    job = _job_get(req.case_id)
    if job and job.get("status") == "running":
        return {"case_id": req.case_id, "status": "already_running", "job": job}

    _job_set(req.case_id, status="queued",
             queued_at=datetime.now(timezone.utc).isoformat())
    background_tasks.add_task(_run_harness_task, req)
    return {
        "case_id": req.case_id,
        "status": "queued",
        "poll_url": f"/api/evidence/cases/{req.case_id}/status",
        "result_url": f"/api/evidence/cases/{req.case_id}",
    }


@router.get("/runs/{run_id}/outliers")
def list_run_outliers(run_id: str):
    """Return outliers from a pipeline run, enriched with evidence status."""
    import glob

    # Find the parquet file for this run
    base = Path("outputs/runs")
    pattern = str(base / "*" / run_id / "data" / "prepared.parquet")
    matches = glob.glob(pattern)
    if not matches:
        # Try flat layout
        pattern2 = str(base / run_id / "data" / "prepared.parquet")
        matches = glob.glob(pattern2)
    if not matches:
        raise HTTPException(status_code=404, detail=f"No data found for run {run_id!r}")

    try:
        import pandas as pd
        df = pd.read_parquet(matches[0])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read parquet: {exc}") from exc

    flag_col = "_return_outlier_flag"
    if flag_col not in df.columns:
        return {"run_id": run_id, "outliers": [], "total": 0}

    outlier_df = df[df[flag_col] == True].copy()
    outlier_df = outlier_df.sort_values(
        "_return_outlier_zscore", key=abs, ascending=False
    )

    def _safe(v):
        try:
            import math
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return None
            if hasattr(v, "isoformat"):
                return v.isoformat()
            return v
        except Exception:
            return str(v)

    outliers = []
    for _, row in outlier_df.iterrows():
        as_of = str(row["as_of_date"])[:10]
        symbol = str(row.get("symbol", ""))
        case_id = f"case_{symbol.lower()}_{as_of}_{run_id}"

        # Enrich with job / DB status
        job = _job_get(case_id)
        evidence_status = job.get("status") if job else "not_investigated"
        verdict = job.get("verdict") if job else None

        # Check DB if job not in memory
        if not job and os.environ.get("JANUS_EVIDENCE_DATABASE_URL"):
            try:
                store = _get_store()
                graph = store.load_case_graph(case_id)
                if graph:
                    evidence_status = "done"
                    verdict = graph.get("case", {}).get("verdict")
            except Exception:
                pass

        outliers.append({
            "case_id": case_id,
            "run_id": run_id,
            "symbol": symbol,
            "as_of_date": as_of,
            "return_price": _safe(row.get("return_price")),
            "z_score": _safe(row.get("_return_outlier_zscore")),
            "severity": str(row.get("_return_outlier_severity", "")),
            "direction": str(row.get("_return_outlier_direction", "")),
            "evidence": str(row.get("_return_outlier_evidence", "")),
            "evidence_status": evidence_status,
            "verdict": verdict,
            "investigate_url": f"/api/evidence/run",
        })

    return {"run_id": run_id, "outliers": outliers, "total": len(outliers)}


def _artifact_base() -> Path:
    return Path(os.environ.get("JANUS_EVIDENCE_ARTIFACT_DIR", "outputs/evidence/harness"))


def _latest_harness_run(case_id: str) -> Path | None:
    """Return the path to the most recent harness run folder for this case."""
    base = _artifact_base()
    best: tuple[float, Path] | None = None
    try:
        for verdict_path in base.rglob(f"*/{case_id}/*/verdict.json"):
            mtime = verdict_path.stat().st_mtime
            if best is None or mtime > best[0]:
                best = (mtime, verdict_path.parent)
    except Exception:
        pass
    return best[1] if best else None


def _read_jsonl(path: Path, limit: int = 50) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
                    if len(rows) >= limit:
                        break
    except Exception:
        pass
    return rows


@router.get("/cases/{case_id}/status")
def get_case_status(case_id: str):
    """Combined status: in-memory job tracker + harness artifact files."""
    job = _job_get(case_id)

    # Enrich from harness artifact (verdict.json + sources.jsonl + claims.jsonl)
    artifact: dict[str, Any] = {}
    run_dir = _latest_harness_run(case_id)
    if run_dir:
        verdict_path = run_dir / "verdict.json"
        if verdict_path.exists():
            try:
                with open(verdict_path, encoding="utf-8") as f:
                    v = json.load(f)
                artifact["verdict"] = v.get("verdict")
                artifact["confidence"] = v.get("confidence")
                artifact["llm_summary"] = v.get("llm_summary")
                artifact["llm_key_findings"] = v.get("llm_key_findings") or []
                artifact["limitations"] = v.get("limitations") or []
                artifact["started_at"] = v.get("started_at")
                artifact["finished_at"] = v.get("finished_at")
            except Exception:
                pass

        raw_sources = _read_jsonl(run_dir / "sources.jsonl")
        artifact["sources"] = [
            {
                "url": s.get("url", ""),
                "title": s.get("title") or _domain(s.get("url", "")),
                "source_tier": s.get("source_tier", ""),
                "document_id": s.get("document_id", ""),
            }
            for s in raw_sources
            if s.get("url")
        ]

        raw_claims = _read_jsonl(run_dir / "claims.jsonl")
        artifact["claims"] = [
            {
                "claim_text": c.get("claim_text", ""),
                "claim_type": c.get("claim_type", ""),
                "support_score": c.get("support_score"),
                "confidence": c.get("confidence", ""),
                "event_type": c.get("event_type"),
            }
            for c in raw_claims
            if c.get("claim_text")
        ]

    # Merge: job tracker wins for status/verdict (live); artifact fills the rest
    merged_job = dict(job or {"status": "not_investigated"})
    for k in ("verdict", "confidence", "llm_summary", "llm_key_findings",
               "limitations", "sources", "claims", "started_at", "finished_at"):
        if k not in merged_job and k in artifact:
            merged_job[k] = artifact[k]
        elif k in artifact and not merged_job.get(k):
            merged_job[k] = artifact[k]

    # Postgres DB fallback (optional)
    if os.environ.get("JANUS_EVIDENCE_DATABASE_URL"):
        try:
            store = _get_store()
            graph = store.load_case_graph(case_id)
            if graph and not merged_job.get("sources"):
                merged_job["sources"] = [
                    {"url": n.get("url", ""), "title": n.get("title") or _domain(n.get("url", "")),
                     "source_tier": n.get("source_tier", ""), "document_id": n.get("node_id", "")}
                    for n in graph.get("nodes", [])
                    if n.get("node_type") not in ("outlier", None) and n.get("url")
                ]
        except Exception:
            pass

    return {
        "case_id": case_id,
        "job": merged_job,
    }


def _domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        h = urlparse(url).hostname or ""
        return h.removeprefix("www.")
    except Exception:
        return url[:40]


@router.get("/cases")
def list_cases(
    status: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    signal_type: str | None = Query(default=None),
    verdict: str | None = Query(default=None),
):
    """List evidence cases with optional filters."""
    store = _get_store()
    filters: dict = {}
    if status:
        filters["status"] = status
    if run_id:
        filters["run_id"] = run_id
    if signal_type:
        filters["signal_type"] = signal_type
    if verdict:
        filters["verdict"] = verdict
    cases = store.list_cases(filters or None)
    return {"cases": cases, "count": len(cases)}


@router.get("/cases/{case_id}")
def get_case(case_id: str):
    """Return case metadata + sources + checks for a single case."""
    store = _get_store()
    graph = store.load_case_graph(case_id)
    if not graph:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    return {
        "case": graph["case"],
        "sources": graph.get("sources", []),
        "checks": graph.get("checks", []),
    }


@router.get("/cases/{case_id}/graph")
def get_case_graph(case_id: str):
    """Return the full graph payload: nodes + edges."""
    store = _get_store()
    graph = store.load_case_graph(case_id)
    if not graph:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    return {
        "case_id": case_id,
        "nodes": graph.get("nodes", []),
        "edges": graph.get("edges", []),
    }


@router.get("/cases/{case_id}/timeline")
def get_case_timeline(case_id: str):
    """Return nodes ordered by the deterministic timeline sort."""
    store = _get_store()
    graph = store.load_case_graph(case_id)
    if not graph:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    from core.evidence_harness.graph_builder import _build_timeline
    timeline = _build_timeline(graph.get("nodes", []))
    return {"case_id": case_id, "timeline": timeline}


@router.post("/cases/{case_id}/review")
def review_case(case_id: str, body: ReviewRequest):
    """Record a review action and update case status."""
    if body.action not in _VALID_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid action {body.action!r}. Valid: {sorted(_VALID_ACTIONS)}",
        )
    store = _get_store()
    graph = store.load_case_graph(case_id)
    if not graph:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")

    new_status = _ACTION_TO_STATUS[body.action]
    store.update_case_status(case_id, new_status)
    store.append_event(
        case_id,
        actor=body.actor,
        action=body.action,
        payload={"reason": body.reason, "new_status": new_status},
    )
    return {
        "case_id": case_id,
        "action": body.action,
        "new_status": new_status,
        "actor": body.actor,
    }


@router.get("/healthz")
def evidence_healthz():
    """DB reachability probe for the evidence graph."""
    try:
        store = _get_store()
        store.list_cases({"status": "_probe_"})
        return {"status": "ok"}
    except HTTPException as exc:
        label = "unconfigured" if exc.status_code == 503 else "error"
        return JSONResponse(status_code=exc.status_code, content={"status": label, "detail": exc.detail})
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(exc)})
