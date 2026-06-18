import { FileText, GitCompareArrows } from "lucide-react";

import { fmtNum, t19 } from "../format";
import type { RunRow } from "../types";
import { MetricPill } from "./Badges";

function dateRange(run: RunRow) {
  return Array.isArray(run.date_range) && run.date_range.length === 2
    ? `${String(run.date_range[0]).slice(0, 10)} -> ${String(run.date_range[1]).slice(0, 10)}`
    : "-";
}

export function RunsTable({
  runs,
  onOpenRun,
  onOpenDiff
}: {
  runs: RunRow[];
  onOpenRun: (runId: string) => void;
  onOpenDiff: (runId: string) => void;
}) {
  return (
    <section>
      <div className="shead">
        <h2>Runs</h2>
        <span className="shint">each pipeline execution - click a row for full lineage</span>
        <span className="c align-right">{runs.length} total</span>
      </div>
      <div className="card">
        <table className="tbl" id="runs">
          <thead>
            <tr>
              <th>Run</th>
              <th>When</th>
              <th>Instrument</th>
              <th>Data range</th>
              <th className="num">Stage changes</th>
              <th className="num">Adj warn</th>
              <th className="num">Unattr.</th>
              <th className="num">Breaks</th>
              <th>Metric src</th>
              <th>DQ</th>
              <th className="num">Sharpe</th>
              <th>Links</th>
            </tr>
          </thead>
          <tbody>
            {runs.length ? (
              runs.map((run) => (
                <tr key={run.run_id} onClick={() => onOpenRun(run.run_id)}>
                  <td>
                    <span className="mono run-id">{run.run_id}</span>
                  </td>
                  <td className="muted">{t19(run.created_at) || "-"}</td>
                  <td>{run.instrument || run.symbol || "-"}</td>
                  <td>
                    <span className="mono data-range">{dateRange(run)}</span>
                  </td>
                  <td className="num">{run.changes}</td>
                  <td className={`num ${run.adjustment_warning_rows ? "bad" : "faint"}`}>
                    {run.adjustment_warning_rows || 0}
                  </td>
                  <td className={`num ${run.unattributed ? "bad" : "faint"}`}>{run.unattributed}</td>
                  <td className="num">
                    {run.breaks_total ? (
                      <>
                        {run.breaks_total}
                        {run.breaks_open ? <span className="muted"> ({run.breaks_open} open)</span> : null}
                      </>
                    ) : (
                      <span className="faint">0</span>
                    )}
                  </td>
                  <td>
                    <MetricPill strategyMetricsAvailable={run.strategy_metrics_available} />
                  </td>
                  <td>
                    <span
                      className={`pill ${run.dq_status === "fail" ? "sev-high" : run.dq_status === "warn" ? "sev-medium" : "sev-low"}`}
                      title={run.dq_worst_dimension || undefined}
                    >
                      {run.dq_status || "-"}
                    </span>
                  </td>
                  <td className="num">{run.sharpe_mean == null ? <span className="faint">-</span> : fmtNum(run.sharpe_mean)}</td>
                  <td className="run-links-cell" onClick={(event) => event.stopPropagation()}>
                    <span className="run-links">
                      {run.has_diff ? (
                        <button className="linkbtn" onClick={() => onOpenDiff(run.run_id)}>
                          <GitCompareArrows size={14} /> diff
                        </button>
                      ) : null}
                      {run.has_report ? (
                        <a className="linkout" href={`/report/${encodeURIComponent(run.run_id)}`} target="_blank" rel="noreferrer">
                          <FileText size={14} /> report
                        </a>
                      ) : null}
                    </span>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={12}>
                  <div className="empty">
                    <div className="et">No runs yet</div>
                    <div className="ed">Start a pipeline run, then this table fills in automatically.</div>
                    <code>python run_pipeline.py -i TSLA --start 2020-01-01 --end 2024-12-31 --allow-unversioned-data</code>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
