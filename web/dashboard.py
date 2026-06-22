"""FastAPI live dashboard for the data-ops pipeline.

Routes
  GET  /                              → React app (served from web/frontend/dist)
  GET  /api/runs                      → run index JSON (live re-scan of outputs/)
  GET  /api/runs/{run_id}             → one run + its breaks + change sample
  GET  /api/breaks                    → all breaks (filter: ?status=&severity=&run_id=)
  GET  /api/trend                     → break count per day, by severity
  POST /api/breaks/{run_id}/{break_id}/transition
                                       → signed lifecycle transition (writes back atomically)
  GET  /diff/{run_id}                 → serve the existing self-contained diff HTML
  GET  /healthz

Start:  python run_dashboard.py            (→ http://127.0.0.1:8800)
"""

from __future__ import annotations

import html
import json
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from core import breaks as bk
from core import diff_report
from core import diff_review
from web import scanner

app = FastAPI(title="Janus Data-Ops Dashboard", version="1.1")
FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"
MAX_INLINE_DIFF_BYTES = int(os.environ.get("JANUS_MAX_INLINE_DIFF_BYTES", str(10 * 1024 * 1024)))
MAX_DIFF_REGEN_LEDGER_BYTES = int(os.environ.get("JANUS_MAX_DIFF_REGEN_LEDGER_BYTES", str(10 * 1024 * 1024)))
MAX_DIFF_PAGE_LIMIT = int(os.environ.get("JANUS_MAX_DIFF_PAGE_LIMIT", "1000"))


# ──────────────────────────── JSON API ────────────────────────────
@app.get("/api/runs")
def api_runs():
    rows = scanner.scan_runs()
    return {"summary": scanner.fleet_summary(rows), "runs": rows}


@app.get("/api/runs/{run_id}")
def api_run(run_id: str):
    d = scanner.run_detail(run_id)
    if d is None:
        raise HTTPException(404, f"run not found: {run_id}")
    return d


@app.get("/api/runs/{run_id}/diff-meta")
def api_diff_meta(run_id: str):
    meta = _diff_meta(run_id)
    if not meta["has_ledger"] and not meta["has_html"]:
        raise HTTPException(404, f"no diff artifacts for run: {run_id}")
    return meta


@app.get("/api/runs/{run_id}/diff-summary")
def api_diff_summary(run_id: str, regenerate: bool = False):
    """Return the policy summary for a diff ledger.

    If the summary file is missing or stale, generate it on demand
    (unless the ledger is too large and regeneration would block the request).
    """
    ledger = scanner.DIFF_DIR / f"{run_id}_changes.jsonl"
    summary_path = scanner.DIFF_DIR / f"{run_id}_summary.json"
    if not ledger.exists() and not summary_path.exists():
        raise HTTPException(404, f"no diff artifacts for run: {run_id}")
    if regenerate or not diff_review.is_summary_fresh(ledger, summary_path):
        if ledger.exists() and ledger.stat().st_size <= MAX_DIFF_REGEN_LEDGER_BYTES:
            try:
                diff_review.write_diff_summary(ledger, run_id=run_id,
                                               out_dir=scanner.DIFF_DIR)
            except Exception as exc:
                raise HTTPException(500, f"summary generation failed: {exc}") from exc
    if not summary_path.exists():
        raise HTTPException(404, f"no diff summary for run: {run_id}")
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(500, f"failed to read summary: {exc}") from exc


@app.get("/api/runs/{run_id}/diff-records")
def api_diff_records(
    run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1),
    stage: str | None = Query(None),
    change_type: str | None = Query(None),
    reason: str | None = Query(None),
):
    """Return a bounded page from the JSONL diff ledger.

    This endpoint is intentionally page-based so the dashboard never has to embed
    a multi-hundred-MB diff payload in an iframe or a self-contained HTML page.
    """
    limit = min(limit, MAX_DIFF_PAGE_LIMIT)
    ledger = scanner.DIFF_DIR / f"{run_id}_changes.jsonl"
    if not ledger.exists():
        raise HTTPException(404, f"no diff ledger for run: {run_id}")

    rows = []
    matched = 0
    has_more = False
    for rec in scanner._iter_jsonl(ledger):
        if stage and f"{rec.get('stage_from')}->{rec.get('stage_to')}" != stage:
            continue
        if change_type and rec.get("change_type") != change_type:
            continue
        if reason and rec.get("reason") != reason:
            continue

        if matched < offset:
            matched += 1
            continue
        if len(rows) < limit:
            rows.append(rec)
            matched += 1
            continue
        has_more = True
        break

    next_offset = offset + len(rows) if has_more else None
    return {
        "run_id": run_id,
        "offset": offset,
        "limit": limit,
        "returned": len(rows),
        "next_offset": next_offset,
        "has_more": has_more,
        "records": rows,
    }


@app.get("/api/breaks")
def api_breaks(status: str | None = Query(None), severity: str | None = Query(None),
              run_id: str | None = Query(None)):
    rows = scanner.all_breaks()
    if status:
        rows = [b for b in rows if b.get("status") == status]
    if severity:
        rows = [b for b in rows if b.get("severity") == severity]
    if run_id:
        rows = [b for b in rows if b.get("run_id") == run_id]
    return {"n": len(rows), "breaks": rows}


@app.get("/api/trend")
def api_trend():
    return {"trend": scanner.break_trend()}


@app.get("/api/compare")
def api_compare(a: str = Query(...), b: str = Query(...)):
    """Cross-run vintage diff of the prepared DATA: a=before, b=after."""
    res = scanner.compare_runs(a, b)
    if "error" in res:
        raise HTTPException(404, res["error"])
    return res


@app.get("/api/runs/{run_id}/raw-row")
def api_raw_row(run_id: str, symbol: str = Query(...), as_of_date: str = Query(...)):
    """Return raw-source fields for one (symbol, date) row — used by break modal."""
    row = scanner.load_raw_row(run_id, symbol, as_of_date)
    if row is None:
        raise HTTPException(404, f"no prepared row for {symbol} on {as_of_date} in run {run_id}")
    return row


@app.post("/api/breaks/{run_id}/{break_id}/transition")
def api_transition(run_id: str, break_id: str, body: dict):
    """Apply a signed lifecycle transition to one break and persist the ledger atomically.

    Body: {to_status, actor_id, actor_role, reason_code?, note?}
    SoD + state-machine + high-severity-reason rules enforced by core.breaks.transition.
    """
    path = scanner.breaks_path(run_id)
    if not path.exists():
        raise HTTPException(404, f"no break ledger for run: {run_id}")

    rows = scanner.load_breaks(run_id)
    idx = next((i for i, b in enumerate(rows) if b.get("break_id") == break_id), None)
    if idx is None:
        raise HTTPException(404, f"break not found: {break_id}")

    to_status = body.get("to_status")
    actor_id = body.get("actor_id")
    actor_role = body.get("actor_role")
    if not (to_status and actor_id and actor_role):
        raise HTTPException(422, "to_status, actor_id, actor_role required")

    try:
        bk.transition(rows[idx], to_status, actor_id, actor_role,
                      reason_code=body.get("reason_code"), note=body.get("note"))
    except bk.BreakTransitionError as e:
        raise HTTPException(409, str(e))

    _atomic_write_jsonl(path, rows)
    return {"ok": True, "break": rows[idx], "chain_valid": bk.verify_chain(rows[idx])}


def _atomic_write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, default=str) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@app.get("/report/{run_id}", response_class=HTMLResponse)
def serve_report(run_id: str):
    d = scanner.find_run_dir(run_id)
    if d is None:
        raise HTTPException(404, f"run not found: {run_id}")
    p = d / "report" / "final_report.html"
    if not p.exists():
        raise HTTPException(404, f"no HTML report for run: {run_id}")
    return HTMLResponse(p.read_text(encoding="utf-8"))


@app.get("/diff/{run_id}", response_class=HTMLResponse)
def serve_diff(run_id: str):
    p = scanner.DIFF_DIR / f"{run_id}_diff.html"
    ledger = scanner.DIFF_DIR / f"{run_id}_changes.jsonl"
    if ledger.exists() and _should_regenerate_diff_html(ledger, p):
        diff_report.write_diff_html(
            list(scanner._iter_jsonl(ledger)),
            scanner.load_breaks(run_id),
            run_id,
            out_dir=scanner.DIFF_DIR,
        )
    if not p.exists():
        if ledger.exists():
            return HTMLResponse(_large_diff_html(run_id, _diff_meta(run_id)))
        raise HTTPException(404, f"no diff HTML for run: {run_id}")
    if p.stat().st_size > MAX_INLINE_DIFF_BYTES:
        return HTMLResponse(_large_diff_html(run_id, _diff_meta(run_id)))
    return FileResponse(p, media_type="text/html")


def _should_regenerate_diff_html(ledger: Path, html_path: Path) -> bool:
    """Regenerate only small ledgers; large ledgers must use paged APIs."""
    if ledger.stat().st_size > MAX_DIFF_REGEN_LEDGER_BYTES:
        return False
    return not html_path.exists() or ledger.stat().st_mtime_ns > html_path.stat().st_mtime_ns


def _diff_meta(run_id: str) -> dict:
    ledger = scanner.DIFF_DIR / f"{run_id}_changes.jsonl"
    html_path = scanner.DIFF_DIR / f"{run_id}_diff.html"
    summary_path = scanner.DIFF_DIR / f"{run_id}_summary.json"
    ledger_bytes = ledger.stat().st_size if ledger.exists() else 0
    html_bytes = html_path.stat().st_size if html_path.exists() else 0
    too_large = (
        ledger_bytes > MAX_DIFF_REGEN_LEDGER_BYTES
        or html_bytes > MAX_INLINE_DIFF_BYTES
    )
    review_status = None
    findings_count = 0
    top_findings: list = []
    if summary_path.exists():
        try:
            sm = json.loads(summary_path.read_text(encoding="utf-8"))
            review_status = sm.get("status")
            findings_count = len(sm.get("findings", []))
            top_findings = sm.get("findings", [])[:5]
        except Exception:
            review_status = "degraded"
    return {
        "run_id": run_id,
        "has_ledger": ledger.exists(),
        "has_html": html_path.exists(),
        "has_summary": summary_path.exists(),
        "ledger_bytes": ledger_bytes,
        "html_bytes": html_bytes,
        "max_inline_diff_bytes": MAX_INLINE_DIFF_BYTES,
        "max_regen_ledger_bytes": MAX_DIFF_REGEN_LEDGER_BYTES,
        "render_mode": "paged_required" if too_large else "inline_html",
        "too_large_for_inline": too_large,
        "records_api": f"/api/runs/{run_id}/diff-records?limit=200",
        "download_path": f"/diff/{run_id}/download" if ledger.exists() else None,
        "summary_path": f"/api/runs/{run_id}/diff-summary" if summary_path.exists() else None,
        "review_status": review_status,
        "findings_count": findings_count,
        "top_findings": top_findings,
    }


def _large_diff_html(run_id: str, meta: dict) -> str:
    safe_run = html.escape(run_id)
    records_api = html.escape(meta["records_api"])
    download = meta.get("download_path")
    download_link = (
        f'<a class="btn" href="{html.escape(download)}">Download JSONL ledger</a>'
        if download else ""
    )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Janus diff too large - {safe_run}</title>
    <style>
      body{{margin:0;background:#101217;color:#e7e8ee;font-family:system-ui,-apple-system,Segoe UI,sans-serif}}
      main{{max-width:780px;margin:10vh auto;padding:0 24px}}
      h1{{font-size:24px;margin:0 0 10px}}
      p{{color:#aeb4c0;line-height:1.6}}
      code{{background:#171b22;border:1px solid #2b3240;border-radius:6px;padding:2px 6px;color:#d7e3ff}}
      .grid{{display:grid;grid-template-columns:180px 1fr;gap:10px 16px;margin:22px 0}}
      .k{{color:#8d96a8}} .v{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
      .btn{{display:inline-flex;margin-right:10px;margin-top:8px;padding:9px 12px;border:1px solid #3a4558;border-radius:6px;color:#e7e8ee;text-decoration:none;background:#171b22}}
    </style>
  </head>
  <body>
    <main>
      <h1>Diff is too large to render inline</h1>
      <p>
        Run <code>{safe_run}</code> has a diff artifact large enough to freeze the browser
        if loaded as a self-contained iframe. The dashboard blocked inline rendering and
        exposed a paged API instead.
      </p>
      <div class="grid">
        <div class="k">Ledger size</div><div class="v">{_format_bytes(meta["ledger_bytes"])}</div>
        <div class="k">HTML size</div><div class="v">{_format_bytes(meta["html_bytes"])}</div>
        <div class="k">Inline limit</div><div class="v">{_format_bytes(meta["max_inline_diff_bytes"])}</div>
        <div class="k">Render mode</div><div class="v">{html.escape(meta["render_mode"])}</div>
      </div>
      <a class="btn" href="{records_api}">Open first 200 records as JSON</a>
      {download_link}
    </main>
  </body>
</html>"""


def _format_bytes(n: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024


@app.get("/diff/{run_id}/download")
def download_diff_ledger(run_id: str):
    ledger = scanner.DIFF_DIR / f"{run_id}_changes.jsonl"
    if not ledger.exists():
        raise HTTPException(404, f"no diff ledger for run: {run_id}")
    return FileResponse(
        ledger,
        media_type="application/x-ndjson",
        filename=f"{run_id}_changes.jsonl",
    )


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/favicon.ico", response_class=PlainTextResponse)
def favicon():
    return ""


# ──────────────────────────── React app ────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    react_index = FRONTEND_DIST / "index.html"
    if react_index.exists():
        return HTMLResponse(react_index.read_text(encoding="utf-8"))
    return HTMLResponse(_missing_frontend_html(), status_code=503)


def _missing_frontend_html() -> str:
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Janus dashboard frontend not built</title>
    <style>
      body{margin:0;padding:40px;font-family:system-ui,sans-serif;background:#0f1014;color:#e7e8ee}
      main{max-width:720px;margin:0 auto}
      code{display:inline-block;margin-top:12px;padding:10px 12px;border:1px solid #2b2e38;border-radius:8px;background:#17181e;color:#8fb5f5}
      p{color:#9aa0ac;line-height:1.6}
    </style>
  </head>
  <body>
    <main>
      <h1>Janus dashboard frontend is not built</h1>
      <p>The dashboard UI now lives in <strong>web/frontend</strong>. Build it once, then restart or refresh this server.</p>
      <code>cd web/frontend &amp;&amp; npm install &amp;&amp; npm run build</code>
    </main>
  </body>
</html>"""


@app.get("/assets/{asset_path:path}")
def frontend_asset(asset_path: str):
    root = FRONTEND_ASSETS.resolve()
    target = (FRONTEND_ASSETS / asset_path).resolve()
    if not target.is_file() or not target.is_relative_to(root):
        raise HTTPException(404, f"asset not found: {asset_path}")
    return FileResponse(target)


@app.get("/{spa_path:path}", response_class=HTMLResponse)
def spa_fallback(spa_path: str):
    if spa_path.startswith(("api/", "diff/", "report/", "assets/")):
        raise HTTPException(404, f"not found: {spa_path}")
    return index()
