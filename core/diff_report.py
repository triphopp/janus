"""HTML drill-down viewer for the CDC ledger + break ledger (P5, §5.3 / §10).

Self-contained single file (no server, no deps): the ChangeRecords + breaks are embedded
as JSON and rendered client-side with vanilla JS. An analyst opens it, filters
"UNATTRIBUTED" or "|Δ|>5%", and sees the handful of suspect rows instead of scanning a
40k-row CSV. This is the drill-down surface behind the `summary.cdc` metrics.

Security: data is JSON-encoded and ``<``/``>`` are escaped so a value containing
``</script>`` cannot break out of the embedded block (the XSS-ish risk audit L1 flagged in
reporting.py — uses placeholder replacement, never f-string/.format over the JS body).
"""

from __future__ import annotations

import json
from pathlib import Path

from core.cdc import ChangeRecord


def _safe_json(obj) -> str:
    return json.dumps(obj, default=str).replace("<", "\\u003c").replace(">", "\\u003e")


# Built with placeholder tokens (not .format) so the JS/CSS braces stay literal.
_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>Janus diff — __RUN_ID__</title>
<style>
 :root{
  --bg:#f7f8fb;--surface:#fff;--ink:#171923;--mut:#647083;--faint:#9aa3b2;
  --line:#e5e8ef;--line2:#d7dce6;--accent:#3867d6;--accent-soft:#eaf0ff;
  --hi:#c7373f;--hi-soft:#fdebed;--ok:#1f8a5b;--ok-soft:#e8f6ef;
  --warn:#b56a00;--warn-soft:#fff3dc;--row:#f5f7fb;--shadow:0 1px 2px rgba(20,27,43,.04),0 8px 28px rgba(20,27,43,.06)
 }
 @media(prefers-color-scheme:dark){:root{
  --bg:#101216;--surface:#181b21;--ink:#edf0f5;--mut:#a2aabb;--faint:#747d8f;
  --line:#292e38;--line2:#343b48;--accent:#8ea7ff;--accent-soft:#222b45;
  --hi:#ff7b85;--hi-soft:#3b2025;--ok:#62d195;--ok-soft:#163323;
  --warn:#f2b45b;--warn-soft:#342818;--row:#14171d;--shadow:none
 }}
 *{box-sizing:border-box}
 body{background:var(--bg);color:var(--ink);margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif;font-size:14px;line-height:1.45}
 .wrap{max-width:1280px;margin:0 auto;padding:28px 22px 64px}
 header{display:flex;align-items:flex-start;gap:14px;margin-bottom:18px}
 .mark{width:32px;height:32px;border-radius:8px;background:var(--accent);color:#fff;display:grid;place-items:center;font-weight:700}
 h1{font-size:18px;line-height:1.2;margin:0;font-weight:680}
 .sub{color:var(--mut);font-size:12.5px;margin-top:3px}
 .toolbar{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
 input,select,button{border:1px solid var(--line2);background:var(--surface);color:var(--ink);border-radius:8px;padding:7px 10px;font:inherit;font-size:13px}
 input{min-width:190px}
 select{min-width:128px}
 input:focus,select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
 .summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:20px 0}
 .stat{background:var(--surface);padding:14px 16px}
 .stat .n{font-size:24px;font-weight:700;letter-spacing:-.02em}
 .stat .l{font-size:11.5px;color:var(--mut);margin-top:1px;text-transform:uppercase;letter-spacing:.04em}
 .stat.bad .n{color:var(--hi)} .stat.good .n{color:var(--ok)}
 .filters{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;background:var(--surface);border:1px solid var(--line);box-shadow:var(--shadow);border-radius:10px;padding:12px;margin-bottom:14px}
 .f{display:flex;flex-direction:column;gap:4px}
 .f label{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
 .tog{display:flex;align-items:center;gap:7px;height:34px;color:var(--mut);font-size:13px}
 .tog input{min-width:auto}
 .resultline{margin-left:auto;color:var(--mut);font-size:12.5px;padding-bottom:7px}
 .count{color:var(--accent);font-weight:700}
 .panel{background:var(--surface);border:1px solid var(--line);box-shadow:var(--shadow);border-radius:10px;overflow:hidden}
 .tablewrap{overflow:auto;max-height:70vh}
 table{width:100%;border-collapse:separate;border-spacing:0;font-size:13px}
 th,td{border-bottom:1px solid var(--line);padding:11px 12px;text-align:left;vertical-align:top}
 th{position:sticky;top:0;background:var(--surface);z-index:1;color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.04em;font-weight:650}
 tbody tr:hover td{background:var(--row)}
 .type{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:2px 8px;font-size:11.5px;font-weight:650;white-space:nowrap}
 .cell_mod .type{background:var(--warn-soft);color:var(--warn)}
 .row_add .type{background:var(--ok-soft);color:var(--ok)}
 .row_drop .type{background:var(--hi-soft);color:var(--hi)}
 .schema_add .type,.schema_drop .type{background:var(--accent-soft);color:var(--accent)}
 .key{display:flex;gap:5px;flex-wrap:wrap;max-width:360px}
 .kchip{background:var(--row);border:1px solid var(--line);border-radius:6px;padding:2px 6px;font-family:ui-monospace,"SF Mono",Consolas,monospace;font-size:11.5px;color:var(--mut)}
 .field{font-family:ui-monospace,"SF Mono",Consolas,monospace;font-size:12.5px}
 .changebox{display:grid;grid-template-columns:minmax(90px,1fr) 22px minmax(90px,1fr);gap:6px;align-items:center;min-width:240px}
 .val{display:block;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .before{color:var(--mut);text-decoration:line-through;text-decoration-color:var(--line2)}
 .after{font-weight:650}
 .arr{color:var(--faint);text-align:center}
 .num{font-variant-numeric:tabular-nums;font-family:ui-monospace,"SF Mono",Consolas,monospace}
 .pos{color:var(--ok)} .neg{color:var(--hi)}
 .muted{color:var(--faint)} .reason{display:inline-block;border-radius:999px;padding:2px 8px;background:var(--accent-soft);color:var(--accent);font-size:11.5px;font-weight:650}
 .UNATTRIBUTED{background:var(--hi-soft);color:var(--hi)}
 .breaks{margin-top:22px}
 h2{font-size:13px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em;margin:0 0 10px}
 .empty{padding:22px;color:var(--faint);text-align:center}
 .sev-high{color:var(--hi);font-weight:700}.sev-medium{color:var(--warn);font-weight:700}.sev-low{color:var(--mut);font-weight:700}
 @media(max-width:720px){
  .wrap{padding:18px 12px 44px}
  header{flex-direction:column}.toolbar{margin-left:0;justify-content:flex-start}
  .filters{align-items:stretch}.f,input,select{width:100%;min-width:0}.resultline{margin-left:0}
  .changebox{grid-template-columns:1fr;min-width:180px}.arr{text-align:left}
 }
</style></head><body>
<div class="wrap">
<header>
 <div class="mark">J</div>
 <div><h1>Change Diff</h1><div class="sub">run <span class="num">__RUN_ID__</span></div></div>
 <div class="toolbar"><input id="q" placeholder="Search row, field, reason, value"><select id="sort"><option value="impact">largest movement</option><option value="unattr">unattributed first</option><option value="type">change type</option><option value="field">field name</option></select></div>
</header>
<div class="summary" id="summary"></div>
<div class="filters">
 <div class="f"><label>Stage</label><select id="stage"></select></div>
 <div class="f"><label>Change</label><select id="type"><option value="">all</option><option value="cell_mod">value changed</option><option value="row_add">row added</option><option value="row_drop">row dropped</option><option value="schema_add">column added</option><option value="schema_drop">column removed</option></select></div>
 <div class="f"><label>Field</label><select id="col"></select></div>
 <div class="f"><label>Reason</label><select id="reason"></select></div>
 <div class="f"><label>Min |delta|</label><input id="minabs" type="number" step="any" placeholder="any"></div>
 <label class="tog"><input id="issues" type="checkbox"> important only</label>
 <div class="resultline"><span id="n" class="count">0</span> rows shown</div>
</div>
<div class="panel"><div class="tablewrap"><table id="t"><thead><tr><th>Change</th><th>Field</th><th>Row key</th><th>Before → After</th><th>Delta</th><th>Reason</th></tr></thead><tbody></tbody></table></div></div>
<div class="breaks"><h2>Breaks <span id="nb" class="count">0</span></h2><div class="panel"><div class="tablewrap"><table id="bt"><thead><tr><th>Break</th><th>Severity</th><th>Type</th><th>Stage</th><th>Field</th><th>Before → After</th><th>Status</th></tr></thead><tbody></tbody></table></div></div></div>
</div>
<script>
const CHANGES=__CHANGES__;
const BREAKS=__BREAKS__;
const $=id=>document.getElementById(id);
function esc(v){return String(v==null?'':v).replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));}
function opts(sel,vals){sel.innerHTML='<option value="">all</option>'+vals.map(v=>'<option value="'+esc(v)+'">'+esc(v)+'</option>').join('');}
const stages=[...new Set(CHANGES.map(c=>c.stage_from+' → '+c.stage_to))];
const cols=[...new Set(CHANGES.map(c=>c.column).filter(Boolean))].sort();
const reasons=[...new Set(CHANGES.map(c=>c.reason).filter(Boolean))].sort();
opts($('stage'),stages);opts($('col'),cols);opts($('reason'),reasons);
function isNum(v){return typeof v==='number'&&Number.isFinite(v);}
function fmtNum(v){
 if(!isNum(v))return esc(v);
 const a=Math.abs(v); let max=4;
 if(a>=1000)max=2; else if(a>=1)max=4; else if(a>=0.01)max=5; else if(a===0)max=0; else return v.toExponential(2);
 return new Intl.NumberFormat(undefined,{maximumFractionDigits:max}).format(v);
}
function val(v,cls=''){
 if(v===null||v===undefined||v==='')return '<span class="muted">—</span>';
 if(isNum(v))return '<span class="num '+cls+'" title="'+esc(v)+'">'+fmtNum(v)+'</span>';
 return '<span class="'+cls+'" title="'+esc(v)+'">'+esc(v)+'</span>';
}
function delta(c){
 if(!isNum(c.delta))return '<span class="muted">—</span>';
 const sign=c.delta>0?'+':''; const cls=c.delta<0?'neg':'pos';
 let p='';
 if(isNum(c.pct)){
  const pct=c.pct*100;
  p='<div class="muted">'+(Math.abs(pct)<0.01?(c.pct*10000).toFixed(2)+' bp':pct.toFixed(2)+'%')+'</div>';
 }
 return '<span class="num '+cls+'" title="'+esc(c.delta)+'">'+sign+fmtNum(c.delta)+'</span>'+p;
}
function keyHtml(key){
 const ent=Object.entries(key||{});
 if(!ent.length)return '<span class="muted">—</span>';
 return '<div class="key">'+ent.slice(0,6).map(([k,v])=>'<span class="kchip">'+esc(k)+'='+esc(isNum(v)?fmtNum(v):v)+'</span>').join('')+(ent.length>6?'<span class="kchip">+'+(ent.length-6)+'</span>':'')+'</div>';
}
function typeLabel(t){return {cell_mod:'value changed',row_add:'row added',row_drop:'row dropped',schema_add:'column added',schema_drop:'column removed'}[t]||t;}
function important(c){return c.reason==='UNATTRIBUTED'||c.change_type==='row_add'||c.change_type==='row_drop'||c.change_type==='cell_mod';}
function hay(c){return [c.stage_from,c.stage_to,c.change_type,c.column,c.reason,JSON.stringify(c.key),c.before,c.after].join(' ').toLowerCase();}
function stat(n,l,cls=''){return '<div class="stat '+cls+'"><div class="n">'+n+'</div><div class="l">'+l+'</div></div>'}
function renderSummary(){
 const by=t=>CHANGES.filter(c=>c.change_type===t).length;
 const rowMoves=by('row_add')+by('row_drop');
 const schema=by('schema_add')+by('schema_drop');
 const unattr=CHANGES.filter(c=>c.reason==='UNATTRIBUTED').length;
 $('summary').innerHTML=stat(CHANGES.length,'total changes')+stat(by('cell_mod'),'value changes')+stat(rowMoves,'row adds/drops',rowMoves?'bad':'')+stat(schema,'schema changes')+stat(unattr,'unattributed',unattr?'bad':'good')+stat(BREAKS.length,'breaks',BREAKS.length?'bad':'good');
}
function render(){
 const st=$('stage').value,ty=$('type').value,co=$('col').value,re=$('reason').value,ma=parseFloat($('minabs').value),q=$('q').value.trim().toLowerCase(),issues=$('issues').checked;
 let rows=CHANGES.filter(c=>{
  if(st&&(c.stage_from+' → '+c.stage_to)!==st)return false;
  if(ty&&c.change_type!==ty)return false;
  if(co&&c.column!==co)return false;
  if(re&&c.reason!==re)return false;
  if(!isNaN(ma)&&!(c.delta!==null&&Math.abs(c.delta)>=ma))return false;
  if(q&&!hay(c).includes(q))return false;
  if(issues&&!important(c))return false;
  return true;});
 const sort=$('sort').value;
 rows.sort((a,b)=>{
  if(sort==='unattr')return (b.reason==='UNATTRIBUTED')-(a.reason==='UNATTRIBUTED')||Math.abs(b.delta||0)-Math.abs(a.delta||0);
  if(sort==='type')return String(a.change_type).localeCompare(String(b.change_type))||String(a.column||'').localeCompare(String(b.column||''));
  if(sort==='field')return String(a.column||'').localeCompare(String(b.column||''));
  return Math.abs(b.delta||0)-Math.abs(a.delta||0);
 });
 $('n').textContent=rows.length;
 $('t').tBodies[0].innerHTML=rows.length?rows.map(c=>{
  const cls=c.change_type;
  const rc=c.reason==='UNATTRIBUTED'?'UNATTRIBUTED':'';
  const change='<div class="changebox"><span class="val before">'+val(c.before)+'</span><span class="arr">→</span><span class="val after">'+val(c.after)+'</span></div>';
  return '<tr class="'+cls+'"><td><span class="type">'+typeLabel(c.change_type)+'</span></td><td class="field">'+val(c.column)+'</td><td>'+keyHtml(c.key)+'</td><td>'+change+'</td><td>'+delta(c)+'</td><td>'+(c.reason?'<span class="reason '+rc+'">'+esc(c.reason)+'</span>':'<span class="muted">—</span>')+'</td></tr>';
 }).join(''):'<tr><td colspan="6" class="empty">No rows match the current filters.</td></tr>';
}
['stage','type','col','reason','minabs','issues','q','sort'].forEach(id=>$(id).addEventListener('input',render));
$('nb').textContent=BREAKS.length;
$('bt').tBodies[0].innerHTML=BREAKS.length?BREAKS.map(b=>'<tr><td class="num">'+esc(b.break_id)+'</td><td class="sev-'+esc(b.severity)+'">'+esc(b.severity)+'</td><td>'+esc(b.type)+'</td><td>'+esc(b.stage)+'</td><td class="field">'+val(b.field)+'</td><td><div class="changebox"><span class="val before">'+val(b.before)+'</span><span class="arr">→</span><span class="val after">'+val(b.after)+'</span></div></td><td>'+esc(b.status)+'</td></tr>').join(''):'<tr><td colspan="7" class="empty">No breaks recorded for this run.</td></tr>';
renderSummary();
render();
</script></body></html>"""


def write_diff_html(
    records,
    breaks: list,
    run_id: str,
    out_dir: Path | str = Path("outputs/diff"),
) -> str:
    """Render a self-contained HTML diff viewer. Returns the path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    changes = [r.to_dict() if isinstance(r, ChangeRecord) else r for r in records]
    html = (
        _HTML.replace("__RUN_ID__", str(run_id))
        .replace("__CHANGES__", _safe_json(changes))
        .replace("__BREAKS__", _safe_json(breaks or []))
    )
    path = out_dir / f"{run_id}_diff.html"
    path.write_text(html, encoding="utf-8")
    return str(path)
