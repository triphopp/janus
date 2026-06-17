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

import json
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from core import breaks as bk
from core import diff_report
from web import scanner

app = FastAPI(title="Janus Data-Ops Dashboard", version="1.1")
FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"


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
    if ledger.exists():
        diff_report.write_diff_html(
            list(scanner._iter_jsonl(ledger)),
            scanner.load_breaks(run_id),
            run_id,
            out_dir=scanner.DIFF_DIR,
        )
    if not p.exists():
        raise HTTPException(404, f"no diff HTML for run: {run_id}")
    return HTMLResponse(p.read_text(encoding="utf-8"))


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

