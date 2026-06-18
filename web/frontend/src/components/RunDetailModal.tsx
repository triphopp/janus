import { FileText, GitCompareArrows, X } from "lucide-react";
import { Fragment, useEffect, useMemo, useState } from "react";

import { dashboardApi } from "../api";
import { compactCounts, fmtNum, h8, isNum, pct, valueText } from "../format";
import type { ChangeRecord, RunDetail } from "../types";
import { Modal } from "./Modal";
import { RawSourcePanel } from "./RawSourcePanel";

type RawTarget = { symbol: string; asOfDate: string; label: string };
type RawPanels = Record<string, RawTarget>;

export function RunDetailModal({
  runId,
  onClose,
  onOpenDiff
}: {
  runId: string;
  onClose: () => void;
  onOpenDiff: (runId: string) => void;
}) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rawPanels, setRawPanels] = useState<RawPanels>({});

  useEffect(() => {
    let active = true;
    setDetail(null);
    setError(null);
    setRawPanels({});
    dashboardApi
      .runDetail(runId)
      .then((data) => active && setDetail(data))
      .catch((err: Error) => active && setError(err.message));
    return () => {
      active = false;
    };
  }, [runId]);

  function toggleRawPanel(key: string, target: RawTarget) {
    setRawPanels((current) => {
      if (current[key]) {
        const next = { ...current };
        delete next[key];
        return next;
      }
      return { ...current, [key]: target };
    });
  }

  return (
    <Modal onClose={onClose}>
      <div className="mhead">
        <h3 className="mono">{runId}</h3>
        <button className="x" onClick={onClose} title="Close">
          <X size={18} />
        </button>
      </div>
      {error ? <div className="err">{error}</div> : null}
      {!detail && !error ? <div className="loading">Loading run lineage...</div> : null}
      {detail ? (
        <RunDetailBody
          detail={detail}
          rawPanels={rawPanels}
          toggleRawPanel={toggleRawPanel}
          onOpenDiff={onOpenDiff}
        />
      ) : null}
    </Modal>
  );
}

function RunDetailBody({
  detail,
  rawPanels,
  toggleRawPanel,
  onOpenDiff
}: {
  detail: RunDetail;
  rawPanels: RawPanels;
  toggleRawPanel: (key: string, target: RawTarget) => void;
  onOpenDiff: (runId: string) => void;
}) {
  const pa = (detail.price_adjustments || {}) as Record<string, unknown>;
  const metricSrc = detail.strategy_metrics_available ? "strategy return stream" : "market return diagnostic";
  const tagSummary = detail.tagged_return_outlier_summary || {};
  const warningRows = Number(pa.warning_rows ?? detail.adjustment_warning_rows ?? 0);
  const totalRows = Number(pa.rows ?? detail.n_rows ?? 0);
  const policy = String(pa.policy ?? detail.adjustment_policy ?? "-");
  const status = String(pa.status ?? detail.adjustment_status ?? "not_applicable");
  const taggedRows = detail.tagged_return_outliers || [];
  const changes = detail.changes_sample || [];
  const dq = detail.data_quality;

  return (
    <>
      <div className="msub">
        {detail.instrument || ""} {detail.symbol || ""} | metric source: {metricSrc} | {detail.changes} stage changes |{" "}
        {detail.adjustment_warning_rows || 0} adj warnings | {detail.unattributed} unattributed | {detail.breaks_total} breaks
      </div>

      <div className="block">
        <div className="bt">Provenance</div>
        <div className="kv mono">code {detail.code_version || "-"}</div>
        <div className="kv mono">
          config {h8(detail.config_hash)} | knowledge cutoff {detail.knowledge_cutoff || "-"}
        </div>
        <div className="action-row">
          {detail.has_diff ? (
            <button className="linkbtn" onClick={() => onOpenDiff(detail.run_id)}>
              <GitCompareArrows size={14} /> Open stage diff
            </button>
          ) : null}
          {detail.has_report ? (
            <a className="linkout" href={`/report/${encodeURIComponent(detail.run_id)}`} target="_blank" rel="noreferrer">
              <FileText size={14} /> Open HTML report
            </a>
          ) : null}
        </div>
      </div>

      <div className="block">
        <div className="bt">Data quality scorecard</div>
        {dq ? (
          <>
            <div className="adjustment-head">
              <span className={`pill ${dq.status === "fail" ? "sev-high" : dq.status === "warn" ? "sev-medium" : "sev-low"}`}>
                {dq.status}
              </span>
              <span className="muted">worst: {dq.worst_dimension || "-"}</span>
            </div>
            <div className="flow adjflow">
              {(dq.dimensions || []).map((dim) => (
                <FlowNode
                  key={dim.name}
                  label={dim.name}
                  value={`${pct(dim.rate)} (${dim.n_defect}/${dim.n_total})`}
                  mono
                  tone={dim.status === "fail" ? "bad" : dim.status === "warn" ? "warn" : "ok"}
                />
              ))}
            </div>
          </>
        ) : (
          <span className="muted">no data quality scorecard recorded</span>
        )}
      </div>

      <div className="block">
        <div className="bt">Price adjustments</div>
        <div className="adjustment-head">
          <span className={`pill ${warningRows ? "sev-high" : status === "pass" ? "sev-low" : "sev-medium"}`}>{status}</span>
          <span className="muted">{policy}</span>
        </div>
        <div className="msub compact">
          {policy === "dividend_total_return_pit"
            ? "Returns include a point-in-time dividend add-back."
            : warningRows
              ? `${warningRows} of ${totalRows} rows use unadjusted price.`
              : "No adjustment warnings."}
        </div>
        <div className="flow adjflow">
          <FlowNode label="dividends folded" value={`${valueText(pa.dividend_days ?? 0)} day(s)`} mono />
          <FlowNode label="warning rows" value={`${warningRows} / ${totalRows}`} mono tone={warningRows ? "bad" : "ok"} />
          <FlowNode label="factor rows" value={valueText(pa.factor_rows ?? detail.adjustment_factor_rows ?? 0)} mono />
          <FlowNode
            label="adj_factor range"
            value={pa.adj_factor_min != null ? `${fmtNum(pa.adj_factor_min)}-${fmtNum(pa.adj_factor_max)}` : "-"}
            mono
          />
          <FlowNode label="max price diff" value={fmtNum(pa.max_abs_price_std_vs_provider_adjusted ?? detail.adjustment_max_abs_price_diff)} mono />
        </div>
      </div>

      <div className="block">
        <div className="bt">Tagged return outliers</div>
        <div className="flow adjflow">
          <FlowNode label="tagged rows" value={valueText(tagSummary.total || 0)} mono />
          <FlowNode label="shown" value={valueText(tagSummary.shown || 0)} mono />
          <FlowNode label="policy" value={compactCounts(tagSummary.by_policy)} mono />
          <FlowNode label="validation" value={compactCounts(tagSummary.by_status)} mono />
          <FlowNode label="severity" value={compactCounts(tagSummary.by_severity)} mono />
        </div>
        <div className="card nested-card">
          <table className="tbl">
            <thead>
              <tr>
                <th>Date</th>
                <th>Symbol</th>
                <th className="num">Raw return</th>
                <th className="num">Std return</th>
                <th className="num">Winsorized</th>
                <th>Policy</th>
                <th>Status</th>
                <th>Severity</th>
                <th className="num">Z</th>
                <th>Reason</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {taggedRows.length ? (
                taggedRows.map((row, index) => {
                  const date = String(row.as_of_date || "").slice(0, 10);
                  const symbol = row.symbol || "";
                  const rawKey = `tagged:${symbol}:${date}:${index}`;
                  const rawPanel = rawPanels[rawKey];
                  return (
                    <Fragment key={rawKey}>
                      <tr>
                        <td className="mono">{date || "-"}</td>
                        <td className="mono">{symbol || "-"}</td>
                        <td className="num bad">{pct(row.return_raw)}</td>
                        <td className="num">{pct(row.return_std)}</td>
                        <td className="num">{pct(row.return_winsorized)}</td>
                        <td className="mono">{row._return_outlier_policy || "-"}</td>
                        <td className="mono">{row._return_validation_status || "-"}</td>
                        <td className="mono">{row._return_outlier_severity || "-"}</td>
                        <td className="num">{fmtNum(row._return_outlier_zscore)}</td>
                        <td className="mono">{row._return_outlier_reason || "-"}</td>
                        <td>
                          {symbol && date ? (
                            <button
                              className="ghost tiny"
                              aria-pressed={Boolean(rawPanel)}
                              onClick={() => toggleRawPanel(rawKey, { symbol, asOfDate: date, label: `${symbol} ${date}` })}
                            >
                              {rawPanel ? "close" : "raw"}
                            </button>
                          ) : null}
                        </td>
                      </tr>
                      {rawPanel ? <InlineRawRow colSpan={11} runId={detail.run_id} target={rawPanel} /> : null}
                    </Fragment>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={11} className="faint">
                    no tagged return outliers in this run
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <StagePipeline detail={detail} />

      <div className="block">
        <div className="bt">Change sample</div>
        <div className="card nested-card">
          <table className="tbl">
            <thead>
              <tr>
                <th>Stage</th>
                <th>Type</th>
                <th>Column</th>
                <th>Reason</th>
                <th className="num">Value / delta</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {changes.length ? (
                changes.slice(0, 120).map((change, index) => (
                  <ChangeRow
                    key={`${change.stage_from}:${change.stage_to}:${change.column}:${index}`}
                    change={change}
                    runId={detail.run_id}
                    rawKey={`change:${index}:${change.stage_from}:${change.stage_to}:${change.column}`}
                    rawPanel={rawPanels[`change:${index}:${change.stage_from}:${change.stage_to}:${change.column}`]}
                    onToggleRaw={toggleRawPanel}
                  />
                ))
              ) : (
                <tr>
                  <td colSpan={6} className="faint">
                    no changes
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function StagePipeline({ detail }: { detail: RunDetail }) {
  const hops = detail.stage_hops || [];
  return (
    <div className="block">
      <div className="bt">Stage pipeline - where the data changed</div>
      {hops.length ? (
        <div className="flow adjflow">
          {hops.map((hop) => (
            <div className="fnode stage" key={`${hop.stage_from}:${hop.stage_to}`}>
              <div className="fl">
                {`${hop.stage_from || "-"} -> ${hop.stage_to || "-"}`}
              </div>
              <div className="fv">
                {hop.changes} change{hop.changes === 1 ? "" : "s"}
                {hop.unattributed ? <span className="bad"> | {hop.unattributed} unattr</span> : null}
              </div>
              <div className="kv mono">
                {hop.cell_mod} val | {hop.schema_add} +col | {hop.row_add} +row | {hop.row_drop} -row
              </div>
            </div>
          ))}
        </div>
      ) : (
        <span className="muted">no stage changes recorded</span>
      )}
    </div>
  );
}

function ChangeRow({
  change,
  runId,
  rawKey,
  rawPanel,
  onToggleRaw
}: {
  change: ChangeRecord;
  runId: string;
  rawKey: string;
  rawPanel?: RawTarget;
  onToggleRaw: (key: string, target: RawTarget) => void;
}) {
  const delta = change.delta;
  const deltaClass = delta == null || !isNum(delta) ? "faint" : Number(delta) >= 0 ? "ok" : "bad";
  const key = change.key || {};
  const symbol = key.symbol != null ? String(key.symbol) : "";
  const asOfDate = key.as_of_date != null ? String(key.as_of_date).slice(0, 10) : "";
  const value =
    change.change_type === "schema_add"
      ? change.after == null
        ? "-"
        : `= ${valueText(change.after)}`
      : delta == null || !isNum(delta)
        ? "-"
        : `${Number(delta) > 0 ? "+" : ""}${fmtNum(delta)}`;

  return (
    <Fragment>
      <tr>
        <td className="mono faint tiny-text">
          {`${change.stage_from || ""} -> ${change.stage_to || ""}`}
        </td>
        <td>{change.change_type || "-"}</td>
        <td className="mono">{change.column || "-"}</td>
        <td className={change.reason === "UNATTRIBUTED" ? "bad" : "muted"}>{change.reason || "-"}</td>
        <td className={`num ${deltaClass}`}>{value}</td>
        <td>
          {symbol && asOfDate ? (
            <button
              className="ghost tiny"
              aria-pressed={Boolean(rawPanel)}
              onClick={() => onToggleRaw(rawKey, { symbol, asOfDate, label: `${symbol} ${asOfDate}` })}
            >
              {rawPanel ? "close" : "raw"}
            </button>
          ) : null}
        </td>
      </tr>
      {rawPanel ? <InlineRawRow colSpan={6} runId={runId} target={rawPanel} /> : null}
    </Fragment>
  );
}

function InlineRawRow({ colSpan, runId, target }: { colSpan: number; runId: string; target: RawTarget }) {
  return (
    <tr className="raw-source-row">
      <td colSpan={colSpan}>
        <div className="raw-source-inline">
          <div className="raw-source-title">Raw source - {target.label}</div>
          <RawSourcePanel runId={runId} symbol={target.symbol} asOfDate={target.asOfDate} />
        </div>
      </td>
    </tr>
  );
}

function FlowNode({
  label,
  value,
  mono,
  tone
}: {
  label: string;
  value: string;
  mono?: boolean;
  tone?: "bad" | "ok" | "warn";
}) {
  return (
    <div className="fnode">
      <div className="fl">{label}</div>
      <div className={`fv ${mono ? "mono" : ""} ${tone || ""}`}>{value}</div>
    </div>
  );
}
