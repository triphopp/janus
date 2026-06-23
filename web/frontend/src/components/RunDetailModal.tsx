import { FileText, GitCompareArrows, X } from "lucide-react";
import { Fragment, useEffect, useMemo, useState } from "react";

import { dashboardApi } from "../api";
import { compactCounts, fmtNum, h8, isNum, pct, valueText } from "../format";
import type { ChangeRecord, DashboardRunDetail, DashboardSection, EvidenceOutlier, RunDetail } from "../types";
import { EvidencePanel } from "./EvidencePanel";
import { Modal } from "./Modal";
import { RawSourcePanel } from "./RawSourcePanel";
import { SectionPanel } from "./SectionPanel";

type RawTarget = { symbol: string; asOfDate: string; label: string };
type RawPanels = Record<string, RawTarget>;
type EvidencePanels = Record<string, boolean>;

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
  const [evidenceOutliers, setEvidenceOutliers] = useState<EvidenceOutlier[]>([]);
  const [evidencePanels, setEvidencePanels] = useState<EvidencePanels>({});

  useEffect(() => {
    let active = true;
    setDetail(null);
    setError(null);
    setRawPanels({});
    setEvidenceOutliers([]);
    setEvidencePanels({});
    dashboardApi
      .runDetail(runId)
      .then((data) => active && setDetail(data))
      .catch((err: Error) => active && setError(err.message));
    dashboardApi
      .evidenceOutliers(runId)
      .then((data) => active && setEvidenceOutliers(data.outliers))
      .catch(() => { /* evidence endpoint optional — silently skip */ });
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
          evidenceOutliers={evidenceOutliers}
          evidencePanels={evidencePanels}
          toggleEvidencePanel={(caseId) =>
            setEvidencePanels((prev) => ({ ...prev, [caseId]: !prev[caseId] }))
          }
        />
      ) : null}
    </Modal>
  );
}

function RunDetailBody({
  detail,
  rawPanels,
  toggleRawPanel,
  onOpenDiff,
  evidenceOutliers,
  evidencePanels,
  toggleEvidencePanel,
}: {
  detail: RunDetail;
  rawPanels: RawPanels;
  toggleRawPanel: (key: string, target: RawTarget) => void;
  onOpenDiff: (runId: string) => void;
  evidenceOutliers: EvidenceOutlier[];
  evidencePanels: EvidencePanels;
  toggleEvidencePanel: (caseId: string) => void;
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

      <TaggedOutliersBlock
        taggedRows={taggedRows}
        tagSummary={tagSummary}
        runId={detail.run_id}
        rawPanels={rawPanels}
        toggleRawPanel={toggleRawPanel}
      />

      {evidenceOutliers.length > 0 ? (
        <EvidenceOutliersBlock
          outliers={evidenceOutliers}
          runId={detail.run_id}
          evidencePanels={evidencePanels}
          toggleEvidencePanel={toggleEvidencePanel}
        />
      ) : null}

      <AdditionalSections detail={detail} />

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

// IDs that are already rendered by hardcoded blocks above — don't double-render
const HARDCODED_SECTION_IDS = new Set(["data_quality", "price_adjustments"]);

function AdditionalSections({ detail }: { detail: RunDetail }) {
  const sections = (detail as DashboardRunDetail).sections;
  if (!sections || sections.length === 0) return null;
  const extra = sections.filter((s: DashboardSection) => !HARDCODED_SECTION_IDS.has(s.id));
  if (extra.length === 0) return null;
  return (
    <>
      {extra.map((section: DashboardSection) => (
        <div key={section.id} className="block">
          <SectionPanel section={section} />
        </div>
      ))}
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

const TAG_PAGE_SIZE = 10;
const PAGE_SIZE = 10;
const SEV_ORDER: Record<string, number> = { severe: 0, high: 1, medium: 2, low: 3 };

function TaggedOutliersBlock({
  taggedRows,
  tagSummary,
  runId,
  rawPanels,
  toggleRawPanel,
}: {
  taggedRows: import("../types").TaggedOutlier[];
  tagSummary: Record<string, unknown>;
  runId: string;
  rawPanels: RawPanels;
  toggleRawPanel: (key: string, target: RawTarget) => void;
}) {
  const [sevFilter, setSevFilter] = useState<"all" | "high">("high");
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    let rows = [...taggedRows].sort(
      (a, b) =>
        (SEV_ORDER[a._return_outlier_severity ?? ""] ?? 9) -
        (SEV_ORDER[b._return_outlier_severity ?? ""] ?? 9)
    );
    if (sevFilter === "high") {
      rows = rows.filter(
        (r) => r._return_outlier_severity === "severe" || r._return_outlier_severity === "high"
      );
    }
    return rows;
  }, [taggedRows, sevFilter]);

  const pages    = Math.max(1, Math.ceil(filtered.length / TAG_PAGE_SIZE));
  const safePage = Math.min(page, pages - 1);
  const visible  = filtered.slice(safePage * TAG_PAGE_SIZE, (safePage + 1) * TAG_PAGE_SIZE);

  return (
    <div className="block">
      <div className="bt" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span>Tagged return outliers</span>
        <span className="muted" style={{ fontSize: 11, fontWeight: 400 }}>
          {filtered.length} / {taggedRows.length}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button
            className={`ghost tiny${sevFilter === "high" ? " active" : ""}`}
            onClick={() => { setSevFilter("high"); setPage(0); }}
          >
            Severe / High
          </button>
          <button
            className={`ghost tiny${sevFilter === "all" ? " active" : ""}`}
            onClick={() => { setSevFilter("all"); setPage(0); }}
          >
            All
          </button>
        </div>
      </div>

      <div className="flow adjflow" style={{ marginBottom: 6 }}>
        <FlowNode label="total" value={valueText(tagSummary.total || 0)} mono />
        <FlowNode label="severity" value={compactCounts(tagSummary.by_severity as Record<string,number>)} mono />
        <FlowNode label="direction" value={compactCounts(tagSummary.by_direction as Record<string,number>)} mono />
        <FlowNode label="reason" value={compactCounts(tagSummary.by_reason as Record<string,number>)} mono />
      </div>

      {filtered.length === 0 ? (
        <div className="muted" style={{ padding: "6px 0", fontSize: 12 }}>
          No severe / high outliers.
        </div>
      ) : (
        <>
          <div className="card nested-card">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Symbol</th>
                  <th className="num">Return</th>
                  <th className="num">Z</th>
                  <th>Severity</th>
                  <th>Reason</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {visible.map((row, index) => {
                  const date   = String(row.as_of_date || "").slice(0, 10);
                  const symbol = row.symbol || "";
                  const rawKey = `tagged:${symbol}:${date}:${safePage * TAG_PAGE_SIZE + index}`;
                  const rawPanel = rawPanels[rawKey];
                  return (
                    <Fragment key={rawKey}>
                      <tr>
                        <td className="mono">{date || "-"}</td>
                        <td className="mono">{symbol || "-"}</td>
                        <td className="num bad">{pct(row.return_raw)}</td>
                        <td className="num">{fmtNum(row._return_outlier_zscore)}</td>
                        <td className="mono">{row._return_outlier_severity || "-"}</td>
                        <td className="mono">{row._return_outlier_reason || "-"}</td>
                        <td>
                          {symbol && date ? (
                            <button
                              className="ghost tiny"
                              aria-pressed={Boolean(rawPanel)}
                              onClick={() =>
                                toggleRawPanel(rawKey, { symbol, asOfDate: date, label: `${symbol} ${date}` })
                              }
                            >
                              {rawPanel ? "close" : "raw"}
                            </button>
                          ) : null}
                        </td>
                      </tr>
                      {rawPanel ? (
                        <InlineRawRow colSpan={7} runId={runId} target={rawPanel} />
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>

          {pages > 1 ? (
            <div className="ev-pagination">
              <button
                className="ghost tiny"
                disabled={safePage === 0}
                onClick={() => setPage(safePage - 1)}
              >
                ← prev
              </button>
              <span className="muted" style={{ fontSize: 11 }}>
                page {safePage + 1} / {pages}
              </span>
              <button
                className="ghost tiny"
                disabled={safePage >= pages - 1}
                onClick={() => setPage(safePage + 1)}
              >
                next →
              </button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

function EvidenceOutliersBlock({
  outliers,
  runId,
  evidencePanels,
  toggleEvidencePanel,
}: {
  outliers: EvidenceOutlier[];
  runId: string;
  evidencePanels: EvidencePanels;
  toggleEvidencePanel: (caseId: string) => void;
}) {
  const [sevFilter, setSevFilter] = useState<"all" | "high">("high");
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    let rows = [...outliers].sort(
      (a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9)
    );
    if (sevFilter === "high") {
      rows = rows.filter((o) => o.severity === "severe" || o.severity === "high");
    }
    return rows;
  }, [outliers, sevFilter]);

  const pages    = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pages - 1);
  const visible  = filtered.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  return (
    <div className="block">
      <div className="bt" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span>Evidence investigation</span>
        <span className="muted" style={{ fontSize: 11, fontWeight: 400 }}>
          {filtered.length} / {outliers.length} outliers
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button
            className={`ghost tiny${sevFilter === "high" ? " active" : ""}`}
            onClick={() => { setSevFilter("high"); setPage(0); }}
          >
            Severe / High
          </button>
          <button
            className={`ghost tiny${sevFilter === "all" ? " active" : ""}`}
            onClick={() => { setSevFilter("all"); setPage(0); }}
          >
            All
          </button>
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="muted" style={{ padding: "8px 0", fontSize: 12 }}>
          No severe / high outliers in this run.
        </div>
      ) : (
        <>
          <div className="card nested-card">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Symbol</th>
                  <th className="num">Z-score</th>
                  <th>Severity</th>
                  <th>Direction</th>
                  <th>Status</th>
                  <th>Verdict</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {visible.map((o) => (
                  <Fragment key={o.case_id}>
                    <tr>
                      <td className="mono">{o.as_of_date}</td>
                      <td className="mono">{o.symbol || "-"}</td>
                      <td className={`num ${Number(o.z_score) < 0 ? "bad" : "ok"}`}>
                        {o.z_score != null ? o.z_score.toFixed(2) : "-"}
                      </td>
                      <td className="mono">{o.severity || "-"}</td>
                      <td className="mono">{o.direction || "-"}</td>
                      <td>
                        <span className={`pill ${evidenceStatusClass(o.evidence_status)}`}>
                          {o.evidence_status}
                        </span>
                      </td>
                      <td className={o.verdict ? verdictTdClass(o.verdict) : "muted"}>
                        {o.verdict ?? "-"}
                      </td>
                      <td>
                        <button
                          className="ghost tiny"
                          aria-pressed={Boolean(evidencePanels[o.case_id])}
                          onClick={() => toggleEvidencePanel(o.case_id)}
                        >
                          {evidencePanels[o.case_id] ? "close" : "details"}
                        </button>
                      </td>
                    </tr>
                    {evidencePanels[o.case_id] ? (
                      <tr>
                        <td colSpan={8} style={{ padding: 0 }}>
                          <EvidencePanel outlier={o} runId={runId} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>

          {pages > 1 ? (
            <div className="ev-pagination">
              <button
                className="ghost tiny"
                disabled={safePage === 0}
                onClick={() => setPage(safePage - 1)}
              >
                ← prev
              </button>
              <span className="muted" style={{ fontSize: 11 }}>
                page {safePage + 1} / {pages}
              </span>
              <button
                className="ghost tiny"
                disabled={safePage >= pages - 1}
                onClick={() => setPage(safePage + 1)}
              >
                next →
              </button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

function evidenceStatusClass(status: string) {
  if (status === "done") return "sev-low";
  if (status === "running") return "sev-medium";
  if (status === "error") return "sev-high";
  return "";
}

function verdictTdClass(verdict: string) {
  if (verdict === "supported_event") return "ok";
  if (verdict === "contradicted") return "bad";
  if (verdict === "failed" || verdict === "error") return "bad";
  return "warn";
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
