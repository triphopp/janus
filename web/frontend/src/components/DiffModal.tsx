import { ExternalLink, X } from "lucide-react";
import { useEffect, useState } from "react";

import { dashboardApi } from "../api";
import { Modal } from "./Modal";

type Finding = {
  code: string;
  status: string;
  detail: string;
  example?: unknown;
};

type DiffMeta = {
  run_id: string;
  has_ledger: boolean;
  has_html: boolean;
  has_summary: boolean;
  render_mode: "inline_html" | "paged_required" | string;
  too_large_for_inline: boolean;
  review_status: string | null;
  findings_count: number;
  top_findings: Finding[];
  summary_path: string | null;
  records_api: string;
};

type DiffSummary = {
  status: string;
  findings: Finding[];
  rollups: Record<string, Record<string, number>>;
  protected: Record<string, number>;
  rates: Record<string, number | string>;
  budgets: Record<string, number>;
  samples: Record<string, unknown[]>;
};

const STATUS_COLOR: Record<string, string> = {
  pass: "#22c55e",
  warn: "#f59e0b",
  fail: "#ef4444",
  degraded: "#a78bfa",
};

function StatusBadge({ status }: { status: string | null }) {
  if (!status) return null;
  return (
    <span
      style={{
        background: STATUS_COLOR[status] ?? "#6b7280",
        color: "#0f1117",
        borderRadius: 4,
        padding: "2px 8px",
        fontWeight: 700,
        fontSize: 12,
        marginLeft: 8,
      }}
    >
      {status.toUpperCase()}
    </span>
  );
}

function FindingRow({ f }: { f: Finding }) {
  const color = STATUS_COLOR[f.status] ?? "#9ca3af";
  return (
    <div style={{ borderLeft: `3px solid ${color}`, paddingLeft: 8, marginBottom: 6 }}>
      <span style={{ fontWeight: 600, fontSize: 12, color }}>{f.code}</span>
      <span style={{ fontSize: 12, color: "#d1d5db", marginLeft: 8 }}>{f.detail}</span>
    </div>
  );
}

function SummaryPanel({ summary }: { summary: DiffSummary }) {
  const rollupStage = summary.rollups?.by_stage ?? {};
  const rollupType = summary.rollups?.by_change_type ?? {};
  const rates = summary.rates ?? {};
  const prot = summary.protected ?? {};

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 20px", marginBottom: 12 }}>
        {Object.entries(rollupType).map(([k, v]) => (
          <span key={k} style={{ fontSize: 12, color: "#d1d5db" }}>
            <span style={{ color: "#9ca3af" }}>{k}:</span> <strong>{v}</strong>
          </span>
        ))}
        {typeof rates.unattributed_rate === "number" && (
          <span style={{ fontSize: 12, color: "#d1d5db" }}>
            <span style={{ color: "#9ca3af" }}>unattr rate:</span>{" "}
            <strong>{(rates.unattributed_rate as number * 100).toFixed(4)}%</strong>
          </span>
        )}
        {typeof rates.row_drop_rate === "number" && (
          <span style={{ fontSize: 12, color: "#d1d5db" }}>
            <span style={{ color: "#9ca3af" }}>row drop rate:</span>{" "}
            <strong>{((rates.row_drop_rate as number) * 100).toFixed(4)}%</strong>
          </span>
        )}
      </div>
      {prot.key_mutations > 0 && (
        <div style={{ fontSize: 12, color: "#ef4444", marginBottom: 4 }}>
          ⚠ {prot.key_mutations} key mutation(s)
        </div>
      )}
      {prot.protected_unattributed > 0 && (
        <div style={{ fontSize: 12, color: "#ef4444", marginBottom: 4 }}>
          ⚠ {prot.protected_unattributed} protected UNATTRIBUTED
        </div>
      )}
      {Object.keys(rollupStage).length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11, color: "#9ca3af", marginBottom: 4 }}>By stage</div>
          {Object.entries(rollupStage).map(([stage, n]) => (
            <div key={stage} style={{ fontSize: 11, color: "#d1d5db", marginLeft: 8 }}>
              {stage}: {n}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function DiffModal({
  runId,
  onBack,
  onClose,
}: {
  runId: string;
  onBack?: () => void;
  onClose: () => void;
}) {
  const [meta, setMeta] = useState<DiffMeta | null>(null);
  const [summary, setSummary] = useState<DiffSummary | null>(null);
  const [loadingMeta, setLoadingMeta] = useState(true);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [showRaw, setShowRaw] = useState(false);

  useEffect(() => {
    let active = true;
    setLoadingMeta(true);
    setMeta(null);
    setSummary(null);
    setShowRaw(false);
    dashboardApi.diffMeta(runId)
      .then((m) => { if (active) setMeta(m as unknown as DiffMeta); })
      .catch(() => { if (active) setMeta(null); })
      .finally(() => { if (active) setLoadingMeta(false); });
    return () => { active = false; };
  }, [runId]);

  useEffect(() => {
    if (!meta?.has_summary) return;
    let active = true;
    setLoadingSummary(true);
    dashboardApi.diffSummary(runId)
      .then((s) => { if (active) setSummary(s as unknown as DiffSummary); })
      .catch(() => {})
      .finally(() => { if (active) setLoadingSummary(false); });
    return () => { active = false; };
  }, [runId, meta?.has_summary]);

  return (
    <Modal onClose={onClose} wide>
      <div className="mhead">
        {onBack ? (
          <button className="linkbtn" onClick={onBack}>
            back to run
          </button>
        ) : null}
        <h3 className="mono diff-title">
          stage diff | {runId}
          {meta && <StatusBadge status={meta.review_status} />}
        </h3>
        <a
          className="linkout"
          href={`/diff/${encodeURIComponent(runId)}`}
          target="_blank"
          rel="noreferrer"
        >
          <ExternalLink size={14} /> open in tab
        </a>
        <button className="x" onClick={onClose} title="Close">
          <X size={18} />
        </button>
      </div>

      {loadingMeta && (
        <div className="msub" style={{ color: "#9ca3af" }}>Loading diff info…</div>
      )}

      {/* Policy review panel */}
      {meta && (meta.has_summary || summary) && (
        <div className="block">
          <div className="bt">
            Diff review
            {summary && <StatusBadge status={summary.status} />}
          </div>
          {loadingSummary && (
            <div style={{ fontSize: 12, color: "#9ca3af" }}>Loading summary…</div>
          )}
          {summary && (
            <>
              <SummaryPanel summary={summary} />
              {summary.findings.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ fontSize: 11, color: "#9ca3af", marginBottom: 6 }}>
                    Findings ({summary.findings.length})
                  </div>
                  {summary.findings.map((f, i) => (
                    <FindingRow key={i} f={f} />
                  ))}
                </div>
              )}
            </>
          )}
          {!loadingSummary && !summary && meta.top_findings?.length > 0 && (
            <div>
              {meta.top_findings.map((f, i) => <FindingRow key={i} f={f} />)}
            </div>
          )}
        </div>
      )}

      {/* Inline HTML diff or paged fallback */}
      {meta && (
        <div className="block">
          <div className="bt">
            Stage diff
            {meta.too_large_for_inline && (
              <span style={{ fontSize: 11, color: "#9ca3af", marginLeft: 8 }}>
                (too large for inline — use paged view or download)
              </span>
            )}
          </div>
          {meta.render_mode === "inline_html" ? (
            <>
              <div style={{ marginBottom: 6 }}>
                <button
                  className="linkbtn"
                  onClick={() => setShowRaw((v) => !v)}
                >
                  {showRaw ? "hide" : "show"} inline diff
                </button>
              </div>
              {showRaw && (
                <iframe
                  className="navframe"
                  src={`/diff/${encodeURIComponent(runId)}`}
                  title={`stage diff ${runId}`}
                />
              )}
            </>
          ) : (
            <div style={{ fontSize: 12, color: "#d1d5db" }}>
              <a className="linkout" href={`/diff/${encodeURIComponent(runId)}`}
                 target="_blank" rel="noreferrer">
                <ExternalLink size={12} /> Open diff in new tab
              </a>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
