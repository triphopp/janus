"""FastAPI live dashboard for the data-ops pipeline.

Routes
  GET  /                              → HTML app (runs, fleet stats, break timeline)
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
from fastapi.responses import HTMLResponse, PlainTextResponse

from core import breaks as bk
from core import diff_report
from web import scanner

app = FastAPI(title="Janus Data-Ops Dashboard", version="1.1")


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


# ──────────────────────────── HTML app ────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_PAGE)


_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>Janus · Data-Ops</title>
<style>
:root{
  --bg:#fbfbfd; --surface:#ffffff; --ink:#15171c; --mut:#6b7280; --faint:#9ca3af;
  --line:#ececf1; --line2:#e0e0e8; --accent:#5b5bd6; --accent-soft:#eceaff;
  --hi:#e5484d; --hi-soft:#fde8e8; --md:#d9870b; --md-soft:#fdf2e0; --lo:#8b8f9c; --lo-soft:#f0f1f4;
  --ok:#2f9e6b; --ok-soft:#e6f4ec; --info:#3b7fd4; --info-soft:#e8f1fb;
  --shadow:0 1px 2px rgba(20,23,28,.04),0 4px 16px rgba(20,23,28,.05);
  --r:14px; --r-sm:9px;
}
@media(prefers-color-scheme:dark){:root{
  --bg:#0f1014; --surface:#17181e; --ink:#e7e8ee; --mut:#9aa0ac; --faint:#6b7280;
  --line:#23252d; --line2:#2b2e38; --accent:#9b9bf0; --accent-soft:#23233a;
  --hi-soft:#3a1d1f; --md-soft:#3a2c14; --lo-soft:#22242b; --ok-soft:#16301f; --info-soft:#16263a;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 6px 20px rgba(0,0,0,.3);
}}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif;
  font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:32px 24px 80px}
.mono{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}

/* header */
header{display:flex;align-items:center;gap:12px;margin-bottom:28px}
.logo{width:30px;height:30px;border-radius:9px;background:var(--accent);color:#fff;
  display:grid;place-items:center;font-weight:700;font-size:15px}
header h1{font-size:17px;font-weight:650;margin:0;letter-spacing:-.01em}
header .meta{color:var(--faint);font-size:12.5px;margin-top:1px}
header .live{margin-left:auto;display:flex;align-items:center;gap:7px;color:var(--mut);font-size:12.5px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 3px var(--ok-soft)}
.ghost{background:none;border:1px solid var(--line2);color:var(--mut);border-radius:8px;
  padding:5px 11px;font:inherit;font-size:12.5px;cursor:pointer}
.ghost:hover{border-color:var(--accent);color:var(--accent)}

/* stats */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line);border-radius:var(--r);overflow:hidden;margin-bottom:30px}
.stat{background:var(--surface);padding:16px 18px}
.stat .n{font-size:26px;font-weight:680;letter-spacing:-.02em}
.stat .l{font-size:12px;color:var(--mut);margin-top:2px}
.stat.warn .n{color:var(--hi)} .stat.good .n{color:var(--ok)}

/* section */
section{margin-bottom:34px}
.shead{display:flex;align-items:baseline;gap:10px;margin-bottom:12px}
.shead h2{font-size:13px;font-weight:640;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:0}
.shead .c{font-size:12px;color:var(--faint)}
.shead .sp{margin-left:auto}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow)}

/* timeline */
.tl{display:flex;gap:3px;align-items:flex-end;height:64px;padding:16px 16px 8px}
.tl .bar{width:16px;display:flex;flex-direction:column-reverse;border-radius:3px 3px 0 0;overflow:hidden;cursor:default;background:var(--line)}
.tl .s-high{background:var(--hi)} .tl .s-medium{background:var(--md)} .tl .s-low{background:var(--lo)}
.tllab{color:var(--faint);font-size:11.5px;padding:0 16px 12px}

/* table */
.tbl{width:100%;border-collapse:collapse}
.tbl th{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--faint);
  text-align:left;padding:10px 14px;border-bottom:1px solid var(--line)}
.tbl td{padding:11px 14px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:middle}
.tbl tr:last-child td{border-bottom:none}
.tbl tbody tr{cursor:pointer}
.tbl tbody tr:hover td{background:var(--bg)}
.tbl .num{font-variant-numeric:tabular-nums}
.muted{color:var(--mut)} .faint{color:var(--faint)}
.bad{color:var(--hi);font-weight:600} .ok{color:var(--ok)}

/* chips / pills */
.pill{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:999px;
  font-size:11.5px;font-weight:550;line-height:1.7}
.sev-high{background:var(--hi-soft);color:var(--hi)}
.sev-medium{background:var(--md-soft);color:var(--md)}
.sev-low{background:var(--lo-soft);color:var(--lo)}
.sdot{width:7px;height:7px;border-radius:50%;display:inline-block}
.sdot.h{background:var(--hi)} .sdot.m{background:var(--md)} .sdot.l{background:var(--lo)}
.st-DETECTED{background:var(--md-soft);color:var(--md)}
.st-TRIAGED{background:var(--info-soft);color:var(--info)}
.st-ESCALATED{background:var(--hi-soft);color:var(--hi)}
.st-ACKNOWLEDGED{background:var(--accent-soft);color:var(--accent)}
.st-AUTO_RESOLVED{background:var(--ok-soft);color:var(--ok)}
.st-CLOSED{background:var(--lo-soft);color:var(--lo)}

/* breaks list */
.blist{display:flex;flex-direction:column}
.brow{display:flex;align-items:center;gap:12px;padding:13px 16px;border-bottom:1px solid var(--line);cursor:pointer}
.brow:last-child{border-bottom:none}
.brow:hover{background:var(--bg)}
.brow .bid{font-size:12px;color:var(--faint);min-width:0}
.brow .btype{font-weight:550;font-size:13px}
.brow .bflow{display:flex;align-items:center;gap:6px;color:var(--mut);font-size:12px}
.arrow{color:var(--faint)}
.brow .sp{margin-left:auto}

/* modal */
.scrim{position:fixed;inset:0;background:rgba(20,23,28,.45);backdrop-filter:blur(2px);
  display:none;align-items:flex-start;justify-content:center;padding:48px 20px;z-index:50;overflow:auto}
.modal{background:var(--surface);border:1px solid var(--line2);border-radius:18px;
  box-shadow:0 20px 60px rgba(20,23,28,.25);max-width:760px;width:100%;padding:26px}
.mhead{display:flex;align-items:center;gap:10px;margin-bottom:4px}
.mhead .x{margin-left:auto;background:none;border:none;color:var(--faint);font-size:20px;cursor:pointer;line-height:1}
.mhead h3{font-size:15px;font-weight:640;margin:0}
.msub{color:var(--mut);font-size:12.5px;margin-bottom:20px}
.block{margin:20px 0}
.block>.bt{font-size:11px;font-weight:640;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);margin-bottom:10px}

/* WHAT-CHANGED flow strip */
.flow{display:flex;align-items:stretch;gap:0;background:var(--bg);border:1px solid var(--line);border-radius:var(--r-sm);overflow:hidden}
.flow.adjflow{overflow-x:auto}
.fnode{flex:1;padding:12px 14px;min-width:0}
.adjflow .fnode{min-width:130px}
.fnode+.fnode{border-left:1px solid var(--line)}
.fnode .fl{font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--faint);margin-bottom:3px}
.fnode .fv{font-size:13px;font-weight:550;word-break:break-word}
.fnode.stage .fv{color:var(--accent)}
.delta.pos{color:var(--ok)} .delta.neg{color:var(--hi)}
.ba{display:flex;align-items:center;gap:8px;font-size:13px}
.ba .b{color:var(--mut);text-decoration:line-through;text-decoration-color:var(--line2)}
.ba .a{font-weight:600}

/* LIFECYCLE chain (signed) */
.chain{display:flex;overflow-x:auto;padding:6px 2px 2px}
.cstep{position:relative;min-width:158px;padding:0 14px;display:flex;flex-direction:column;align-items:center;text-align:center}
.cstep+.cstep::before{content:"";position:absolute;left:-50%;right:50%;top:11px;height:2px;background:var(--line2)}
.cnode{width:24px;height:24px;border-radius:50%;border:2px solid var(--accent);background:var(--surface);
  display:grid;place-items:center;font-size:12px;color:var(--accent);z-index:1;margin-bottom:8px;font-weight:700}
.cnode.done{background:var(--accent);color:#fff}
.cnode.term{border-color:var(--ok);background:var(--ok);color:#fff}
.cmeta{font-size:11.5px;color:var(--mut);line-height:1.45}
.cmeta .who{color:var(--ink);font-weight:550}
.cmeta .rc{color:var(--accent)}
.clink{margin-top:7px;font-size:9.5px;color:var(--faint);font-family:ui-monospace,monospace;
  background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:2px 5px;display:inline-block}
.chain-ok{color:var(--ok)} .chain-bad{color:var(--hi);font-weight:600}

/* lineage impact */
.imp{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.imp .col{background:var(--accent-soft);color:var(--accent);padding:3px 9px;border-radius:7px;font-size:12px;font-weight:550}
.imp .tag{background:var(--bg);border:1px solid var(--line);padding:3px 9px;border-radius:7px;font-size:12px;color:var(--mut)}

/* transition form */
.form{background:var(--bg);border:1px solid var(--line);border-radius:var(--r-sm);padding:16px}
.form .row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px}
.form .f{flex:1;min-width:150px}
.form label{display:block;font-size:11.5px;color:var(--mut);margin-bottom:4px}
.form input,.form select{width:100%;padding:8px 10px;border:1px solid var(--line2);border-radius:8px;
  background:var(--surface);color:var(--ink);font:inherit;font-size:13px}
.form input:focus,.form select:focus{outline:none;border-color:var(--accent)}
.btn{background:var(--accent);color:#fff;border:none;border-radius:8px;padding:9px 16px;font:inherit;font-size:13px;font-weight:550;cursor:pointer}
.btn:hover{filter:brightness(1.05)}
.err{color:var(--hi);font-size:12.5px;margin-top:8px}
.term-note{color:var(--ok);font-size:13px;display:flex;align-items:center;gap:7px}

.kv{font-size:11.5px;color:var(--mut);margin-top:4px}
.linkout{font-size:12.5px}
.cmp{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.cmp select{min-width:230px}
.cmpsum{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
.cmpsum .b{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:6px 12px;font-size:12.5px}
.cmpsum .b b{color:var(--accent)}
.diffmini{overflow:auto;border:1px solid var(--line);border-radius:var(--r-sm);margin-top:10px}
.diffmini table{min-width:760px}
.changebox{display:grid;grid-template-columns:minmax(80px,1fr) 20px minmax(80px,1fr);gap:6px;align-items:center;min-width:220px}
.changebox .old{color:var(--mut);text-decoration:line-through;text-decoration-color:var(--line2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.changebox .new{font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kchips{display:flex;flex-wrap:wrap;gap:4px;max-width:320px}
.kchip{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:2px 6px;font-size:11px;color:var(--mut)}
.ctype{display:inline-flex;border-radius:999px;padding:2px 8px;font-size:11.5px;font-weight:650;background:var(--accent-soft);color:var(--accent);white-space:nowrap}
.ctype.cell_mod{background:var(--md-soft);color:var(--md)}
.ctype.row_add{background:var(--ok-soft);color:var(--ok)}
.ctype.row_drop{background:var(--hi-soft);color:var(--hi)}
.loading{color:var(--mut);font-size:12.5px;padding:10px 0}
</style></head><body>
<div class="wrap">
  <header>
    <div class="logo">J</div>
    <div>
      <h1>Janus · Data-Ops</h1>
      <div class="meta">point-in-time lineage & break tracking</div>
    </div>
    <div class="live">
      <span class="dot"></span> live
      <button class="ghost" onclick="loadAll()">Refresh</button>
    </div>
  </header>

  <div class="stats" id="stats"></div>

  <section>
    <div class="shead"><h2>Break timeline</h2><span class="c" id="tlrange"></span></div>
    <div class="card"><div class="tl" id="tl"></div><div class="tllab" id="tllab"></div></div>
  </section>

  <section>
    <div class="shead"><h2>Runs</h2><span class="c" id="runc"></span></div>
    <div class="card"><table class="tbl" id="runs"><thead><tr>
      <th>Run</th><th>When</th><th>Instrument</th><th class="num">Stage changes</th>
      <th class="num">Adj warn</th><th class="num">Unattr.</th><th class="num">Breaks</th><th class="num">Sharpe</th><th></th>
    </tr></thead><tbody></tbody></table></div>
  </section>

  <section>
    <div class="shead"><h2>Breaks</h2><span class="c" id="brkc"></span>
      <span class="sp"></span>
      <select class="ghost" id="fstatus"><option value="">All status</option>
        <option>DETECTED</option><option>TRIAGED</option><option>ESCALATED</option>
        <option>ACKNOWLEDGED</option><option>AUTO_RESOLVED</option><option>CLOSED</option></select>
      <select class="ghost" id="fsev"><option value="">All severity</option>
        <option>high</option><option>medium</option><option>low</option></select>
    </div>
    <div class="card"><div class="blist" id="breaks"></div></div>
  </section>

  <section>
    <div class="shead"><h2>Compare runs</h2><span class="c">data vintage diff — what changed in the DATA between two imports</span></div>
    <div class="card" style="padding:16px">
      <div class="cmp">
        <select id="cmpA" class="ghost"></select>
        <span class="arrow">→</span>
        <select id="cmpB" class="ghost"></select>
        <button class="btn" onclick="doCompare()">Compare</button>
        <span class="faint" id="cmpHint">pick two runs of the same instrument (A = before, B = after)</span>
      </div>
      <div id="cmpOut"></div>
    </div>
  </section>
</div>

<div class="scrim" id="scrim"><div class="modal" id="modal"></div></div>

<script>
const $=s=>document.querySelector(s), api=p=>fetch(p).then(r=>r.json());
const esc=s=>String(s==null?'':s).replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
const isNum=v=>typeof v==='number'&&Number.isFinite(v)||(!Array.isArray(v)&&v!==''&&v!=null&&!Number.isNaN(Number(v)));
const fmtNum=v=>{
  if(!isNum(v))return '—';
  const n=Number(v),a=Math.abs(n);
  if(a===0)return '0';
  if(a<0.0001)return n.toExponential(2);
  const max=a>=1000?2:a>=1?4:a>=0.01?5:6;
  return new Intl.NumberFormat(undefined,{maximumFractionDigits:max}).format(n);
};
const fmt4=fmtNum;
const val=v=>v==null||v===''?'<span class="faint">—</span>':isNum(v)?`<span class="mono" title="${esc(v)}">${fmtNum(v)}</span>`:esc(v);
const deltaHtml=d=>d==null||!isNum(d)?'<span class="faint">—</span>':`<span class="${Number(d)<0?'bad':'ok'} mono" title="${esc(d)}">${Number(d)>0?'+':''}${fmtNum(d)}</span>`;
const keyHtml=k=>{
  const ent=Object.entries(k||{});
  return ent.length?`<div class="kchips">${ent.slice(0,5).map(([a,b])=>`<span class="kchip mono">${esc(a)}=${esc(isNum(b)?fmtNum(b):b)}</span>`).join('')}${ent.length>5?`<span class="kchip">+${ent.length-5}</span>`:''}</div>`:'<span class="faint">—</span>';
};
const typeLabel=t=>({cell_mod:'value changed',row_add:'row added',row_drop:'row dropped',schema_add:'column added',schema_drop:'column removed'}[t]||t||'change');
const t19=s=>String(s||'').replace('T',' ').slice(0,19);
const sev=s=>`<span class="pill sev-${s}"><span class="sdot ${s[0]}"></span>${s||'?'}</span>`;
const stp=s=>`<span class="pill st-${s}">${esc(s)}</span>`;
const h8=h=>h?String(h).slice(0,8):'∅';
const OPEN=new Set(['DETECTED','TRIAGED','ESCALATED']);
const TERMINAL=new Set(['CLOSED']);
const NEXT={DETECTED:['TRIAGED'],TRIAGED:['AUTO_RESOLVED','ACKNOWLEDGED','ESCALATED'],
  AUTO_RESOLVED:['CLOSED'],ACKNOWLEDGED:['CLOSED'],ESCALATED:['ACKNOWLEDGED','CLOSED'],CLOSED:[]};

function stat(n,l,cls){return `<div class="stat ${cls||''}"><div class="n">${n}</div><div class="l">${l}</div></div>`}

async function loadRuns(){
  const d=await api('/api/runs'), s=d.summary;
  $('#stats').innerHTML=
    stat(s.n_runs,'runs')+stat(s.total_changes,'stage changes')+
    stat(s.total_adjustment_warnings||0,'adj warnings',(s.total_adjustment_warnings||0)?'warn':'good')+
    stat(s.total_unattributed,'unattributed',s.total_unattributed?'warn':'good')+
    stat(s.breaks_total,'breaks')+
    stat(s.breaks_open,'open',s.breaks_open?'warn':'good')+
    stat(s.sev_high,'high severity',s.sev_high?'warn':'good');
  $('#runc').textContent=`${d.runs.length} total`;
  // populate compare selects (keep current picks)
  const opts=d.runs.map(r=>`<option value="${r.run_id}">${esc(r.run_id)} · ${esc(r.instrument||r.symbol||'?')}</option>`).join('');
  for(const id of ['#cmpA','#cmpB']){const el=$(id),cur=el.value;el.innerHTML=opts;if(cur)el.value=cur;}
  $('#runs tbody').innerHTML=d.runs.map(r=>{
    const sh=r.sharpe_mean==null?'<span class="faint">—</span>':r.sharpe_mean.toFixed(2);
    const brk=r.breaks_total?`${r.breaks_total}${r.breaks_open?` <span class="muted">(${r.breaks_open} open)</span>`:''}`:'<span class="faint">0</span>';
    return `<tr onclick="runDetail('${r.run_id}')">
      <td><span class="mono">${esc(r.run_id)}</span></td>
      <td class="muted">${t19(r.created_at)||'—'}</td>
      <td>${esc(r.instrument||r.symbol||'—')}</td>
      <td class="num">${r.changes}</td>
      <td class="num ${r.adjustment_warning_rows?'bad':'faint'}">${r.adjustment_warning_rows||0}</td>
      <td class="num ${r.unattributed?'bad':'faint'}">${r.unattributed}</td>
      <td class="num">${brk}</td>
      <td class="num">${sh}</td>
      <td>${r.has_diff?`<a class="linkout" href="/diff/${r.run_id}" target="_blank" onclick="event.stopPropagation()">diff ↗</a>`:''}</td>
    </tr>`}).join('');
}

async function loadTrend(){
  const d=(await api('/api/trend')).trend;
  const mx=Math.max(1,...d.map(x=>x.total)), H=44, px=v=>Math.round(v/mx*H);
  $('#tl').innerHTML=d.length?d.map(x=>`<div class="bar" title="${x.day}: ${x.total} (H${x.high} M${x.medium} L${x.low})" style="height:${Math.max(3,px(x.total))}px">
    <div class="s-low" style="height:${px(x.low)}px"></div>
    <div class="s-medium" style="height:${px(x.medium)}px"></div>
    <div class="s-high" style="height:${px(x.high)}px"></div></div>`).join(''):'<span class="faint" style="padding:18px">No breaks recorded yet</span>';
  $('#tlrange').textContent=d.length?`${d[0].day} → ${d[d.length-1].day}`:'';
  $('#tllab').textContent=d.length?`${d.reduce((a,x)=>a+x.total,0)} breaks across ${d.length} day(s)`:'';
}

let BREAKS=[];
async function loadBreaks(){
  const qs=new URLSearchParams();
  if($('#fstatus').value)qs.set('status',$('#fstatus').value);
  if($('#fsev').value)qs.set('severity',$('#fsev').value);
  BREAKS=(await api('/api/breaks?'+qs)).breaks;
  $('#brkc').textContent=`${BREAKS.length} shown`;
  $('#breaks').innerHTML=BREAKS.length?BREAKS.map(b=>{
    const [from,to]=(b.stage||'').split('->');
    return `<div class="brow" onclick="openBreak('${b.run_id}','${b.break_id}')">
      <span class="sdot ${b.severity?b.severity[0]:'l'}"></span>
      <span class="btype">${esc(b.type)}</span>
      <span class="bflow mono">${esc(from||b.stage||'')}<span class="arrow"> → </span>${esc(to||'')}</span>
      <span class="bid mono">${esc(b.run_id)}</span>
      <span class="sp"></span>
      ${stp(b.status)}
    </div>`}).join(''):'<div class="brow"><span class="faint">No breaks match the filter</span></div>';
}

function openBreak(run,bid){
  const b=BREAKS.find(x=>x.break_id===bid&&x.run_id===run); if(!b)return;
  const [from,to]=(b.stage||'').split('->');
  const next=NEXT[b.status]||[];
  // signed-chain validity (recompute prev_hash linkage client-side for display)
  let chainOk=true,prev=null;
  for(const h of (b.history||[])){ if(h.prev_hash!==prev){chainOk=false;} prev=h.entry_hash; }

  // WHAT CHANGED strip
  let changed='';
  if(b.field!=null||b.before!=null||b.after!=null){
    const d=b.delta, dcls=d==null?'':(d>=0?'pos':'neg'), dtxt=d==null?'':`Δ ${Number(d)>=0?'+':''}${fmtNum(d)}`;
    changed=`<div class="ba"><span class="b">${val(b.before)}</span><span class="arrow">→</span><span class="a">${val(b.after)}</span> <span class="delta ${dcls}">${dtxt}</span></div>`;
  } else {
    changed=`<span class="muted">row-level change (no single cell)</span>`;
  }
  const keyTxt=Object.entries(b.key||{}).map(([k,v])=>`${k}=${v}`).join(' · ')||'—';

  // LIFECYCLE signed chain
  const hist=(b.history||[]);
  const chain=hist.map((h,i)=>{
    const term=TERMINAL.has(h.to_status), done=i<hist.length-1||term;
    const cls=term?'term':(done?'done':'');
    const sym=term?'✓':(i+1);
    const rc=h.reason_code?`<div class="rc">⊕ ${esc(h.reason_code)}</div>`:'';
    const nt=h.note?`<div>“${esc(h.note)}”</div>`:'';
    return `<div class="cstep">
      <div class="cnode ${cls}">${sym}</div>
      <div style="margin-bottom:6px">${stp(h.to_status)}</div>
      <div class="cmeta">
        <div class="who">${esc(h.actor_id)}</div>
        <div>${esc(h.actor_role)} · ${t19(h.at).slice(11)||t19(h.at)}</div>
        ${rc}${nt}
      </div>
      <div class="clink" title="prev_hash → entry_hash">${h8(h.prev_hash)} → ${h8(h.entry_hash)}</div>
    </div>`}).join('');

  // lineage impact
  const imp=(b.lineage_impact&&b.lineage_impact.length)
    ? `<div class="imp"><span class="col">${esc(b.field||'row')}</span><span class="arrow">→</span>${b.lineage_impact.map(c=>`<span class="tag">${esc(c)}</span>`).join('')}</div>`
    : `<span class="muted">no downstream columns recorded</span>`;

  // transition form
  let form;
  if(next.length){
    form=`<div class="form">
      <div class="row">
        <div class="f"><label>Transition to</label><select id="tstatus">${next.map(s=>`<option>${s}</option>`).join('')}</select></div>
        <div class="f"><label>Actor ID</label><input id="taid" placeholder="alice@desk"></div>
        <div class="f"><label>Role</label><select id="trole"><option>analyst</option><option>lead</option><option>system</option></select></div>
      </div>
      <div class="row">
        <div class="f"><label>Reason code <span class="faint">(required to close high-sev)</span></label><input id="treason" placeholder="benign_provider_revision"></div>
        <div class="f"><label>Note</label><input id="tnote" placeholder="optional"></div>
      </div>
      <button class="btn" onclick="doTransition('${run}','${bid}')">Sign &amp; apply transition</button>
      <div class="err" id="terr"></div>
    </div>`;
  } else {
    form=`<div class="term-note">✓ Terminal state — chain closed, no further transitions.</div>`;
  }

  $('#modal').innerHTML=`
    <div class="mhead">
      ${sev(b.severity)}
      <h3 class="mono">${esc(bid)}</h3>
      <button class="x" onclick="closeM()">×</button>
    </div>
    <div class="msub">${esc(b.type)} · current ${stp(b.status)} ·
      <span class="${chainOk?'chain-ok':'chain-bad'}">${chainOk?'⛓ chain verified':'⚠ chain TAMPERED'}</span></div>

    <div class="block"><div class="bt">Where it broke</div>
      <div class="flow">
        <div class="fnode"><div class="fl">row key</div><div class="fv mono" style="font-size:11.5px">${esc(keyTxt)}</div></div>
        <div class="fnode stage"><div class="fl">from stage</div><div class="fv">${esc(from||b.stage||'—')}</div></div>
        <div class="fnode stage"><div class="fl">to stage</div><div class="fv">${esc(to||'—')}</div></div>
        <div class="fnode"><div class="fl">change${b.field?' · '+esc(b.field):''}</div><div class="fv">${changed}</div></div>
      </div>
    </div>

    <div class="block"><div class="bt">Lifecycle chain (signed)</div>
      <div class="chain">${chain}</div>
    </div>

    <div class="block"><div class="bt">Downstream impact (lineage)</div>${imp}</div>

    <div class="block"><div class="bt">Triage</div>${form}</div>`;
  $('#scrim').style.display='flex';
}

async function doTransition(run,bid){
  const body={to_status:$('#tstatus').value,actor_id:$('#taid').value.trim(),
    actor_role:$('#trole').value,reason_code:$('#treason').value.trim()||null,note:$('#tnote').value.trim()||null};
  if(!body.actor_id){$('#terr').textContent='actor_id required';return;}
  const r=await fetch(`/api/breaks/${run}/${bid}/transition`,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){const e=await r.json();$('#terr').textContent=e.detail||'error';return;}
  closeM(); await loadAll();
}

async function runDetail(rid){
  const d=await api('/api/runs/'+rid);
  const pa=d.price_adjustments||{};
  const rows=(d.changes_sample||[]).slice(0,80).map(c=>{
    const dd=c.delta, dc=dd==null?'faint':(dd>=0?'ok':'bad');
    return `<tr><td>${esc(c.change_type)}</td><td class="mono">${esc(c.column||'')}</td>
      <td class="${c.reason==='UNATTRIBUTED'?'bad':'muted'}">${esc(c.reason||'')}</td>
      <td class="num ${dc}">${dd==null?'':(Number(dd)>0?'+':'')+fmtNum(dd)}</td></tr>`}).join('');
  $('#modal').innerHTML=`
    <div class="mhead"><h3 class="mono">${esc(rid)}</h3><button class="x" onclick="closeM()">×</button></div>
    <div class="msub">${esc(d.instrument||'')} ${esc(d.symbol||'')} · ${d.changes} stage changes · ${d.adjustment_warning_rows||0} adj warnings · ${d.unattributed} unattributed · ${d.breaks_total} breaks</div>
    <div class="block"><div class="bt">Provenance</div>
      <div class="kv mono">code ${esc(d.code_version||'—')}</div>
      <div class="kv mono">config ${h8(d.config_hash)} · knowledge cutoff ${esc(d.knowledge_cutoff||'—')}</div>
      ${d.has_diff?`<div class="linkout" style="margin-top:8px"><a href="/diff/${rid}" target="_blank">Open full diff ↗</a></div>`:''}
    </div>
    <div class="block"><div class="bt">Price adjustments</div>
      <div class="flow adjflow">
        <div class="fnode"><div class="fl">status</div><div class="fv">${esc(pa.status||d.adjustment_status||'not_applicable')}</div></div>
        <div class="fnode"><div class="fl">policy</div><div class="fv">${esc(pa.policy||d.adjustment_policy||'—')}</div></div>
        <div class="fnode"><div class="fl">factor rows</div><div class="fv mono">${pa.factor_rows??d.adjustment_factor_rows??0}</div></div>
        <div class="fnode"><div class="fl">warning rows</div><div class="fv mono">${pa.warning_rows??d.adjustment_warning_rows??0}</div></div>
        <div class="fnode"><div class="fl">max |price diff|</div><div class="fv mono">${fmt4(pa.max_abs_price_std_vs_provider_adjusted??d.adjustment_max_abs_price_diff)}</div></div>
      </div>
    </div>
    <div class="block"><div class="bt">Change sample</div>
      <div class="card"><table class="tbl"><thead><tr><th>Type</th><th>Column</th><th>Reason</th><th class="num">Δ</th></tr></thead><tbody>${rows||'<tr><td class="faint">no changes</td></tr>'}</tbody></table></div>
    </div>`;
  $('#scrim').style.display='flex';
}

async function doCompare(){
  const a=$('#cmpA').value,b=$('#cmpB').value,out=$('#cmpOut');
  if(!a||!b){out.innerHTML='<div class="loading">Pick two runs first.</div>';return;}
  if(a===b){out.innerHTML='<div class="loading">Pick two different runs.</div>';return;}
  out.innerHTML='<div class="loading">Comparing prepared data...</div>';
  try{
    const d=await api(`/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
    const by=d.by_type||{};
    const cell=(d.cell_mods||[]).slice().sort((x,y)=>Math.abs(y.delta||0)-Math.abs(x.delta||0));
    const rows=[...cell.slice(0,120),...(d.row_adds||[]).slice(0,40),...(d.row_drops||[]).slice(0,40),...(d.schema||[])].slice(0,220);
    const body=rows.map(c=>`<tr>
      <td><span class="ctype ${esc(c.change_type)}">${esc(typeLabel(c.change_type))}</span></td>
      <td class="mono">${esc(c.column||c.field||'')}</td>
      <td>${keyHtml(c.key)}</td>
      <td><div class="changebox"><span class="old">${val(c.before)}</span><span class="arrow">→</span><span class="new">${val(c.after)}</span></div></td>
      <td class="num">${deltaHtml(c.delta)}</td>
    </tr>`).join('');
    out.innerHTML=`
      <div class="cmpsum">
        <span class="b"><b>${d.n_changes}</b> total</span>
        <span class="b"><b>${by.cell_mod||0}</b> values</span>
        <span class="b"><b>${by.row_add||0}</b> rows added</span>
        <span class="b"><b>${by.row_drop||0}</b> rows dropped</span>
        <span class="b"><b>${(by.schema_add||0)+(by.schema_drop||0)}</b> schema</span>
        <span class="b"><b>${d.rows_a}</b> → <b>${d.rows_b}</b> rows</span>
      </div>
      <div class="kv mono">identity: ${esc((d.identity||[]).join(', ')||'row position')}</div>
      <div class="diffmini"><table class="tbl"><thead><tr><th>Change</th><th>Field</th><th>Row key</th><th>Before → After</th><th class="num">Delta</th></tr></thead><tbody>${body||'<tr><td colspan="5" class="faint">No data differences found.</td></tr>'}</tbody></table></div>`;
  }catch(e){
    out.innerHTML=`<div class="err">${esc(e.message||e)}</div>`;
  }
}

function closeM(){$('#scrim').style.display='none'}
$('#scrim').onclick=e=>{if(e.target.id==='scrim')closeM()};
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeM()});
$('#fstatus').onchange=loadBreaks; $('#fsev').onchange=loadBreaks;
async function loadAll(){await Promise.all([loadRuns(),loadTrend(),loadBreaks()]);}
loadAll(); setInterval(loadAll,5000);
</script></body></html>"""
