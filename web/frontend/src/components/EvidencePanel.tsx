import { useCallback, useEffect, useRef, useState } from "react";
import { dashboardApi } from "../api";
import type {
  EvidenceCaseStatus,
  EvidenceClaim,
  EvidenceInvestigateRequest,
  EvidenceOutlier,
  EvidenceSource,
} from "../types";

const POLL_MS = 3000;

const VERDICT_PILL: Record<string, string> = {
  supported_event:      "sev-low",
  contradicted:         "sev-high",
  insufficient_evidence:"sev-medium",
  failed:               "sev-high",
};

type Tab = "summary" | "sources" | "claims";

export function EvidencePanel({
  outlier,
  runId,
}: {
  outlier: EvidenceOutlier;
  runId: string;
}) {
  const [job, setJob] = useState<EvidenceCaseStatus["job"] | null>(
    outlier.evidence_status !== "not_investigated"
      ? { status: outlier.evidence_status, verdict: outlier.verdict ?? null }
      : null
  );
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("summary");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await dashboardApi.evidenceCaseStatus(runId, outlier.case_id);
      setJob(res.job);
      if (res.job.status === "done" || res.job.status === "error") stopPolling();
    } catch { /* transient */ }
  }, [runId, outlier.case_id, stopPolling]);

  useEffect(() => {
    // On mount: if already done (from previous run), fetch full data with sources
    if (job?.status === "done" || job?.status === "error") {
      void fetchStatus();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (job?.status === "running") {
      pollRef.current = setInterval(() => void fetchStatus(), POLL_MS);
    }
    return stopPolling;
  }, [job?.status, fetchStatus, stopPolling]);

  async function handleInvestigate() {
    setError(null);
    const req: EvidenceInvestigateRequest = {
      run_id: runId,
      case_id: outlier.case_id,
      instrument: outlier.symbol || outlier.case_id,
      family: "equity",
      symbol: outlier.symbol,
      as_of_date: outlier.as_of_date,
      signal_type: "return_outlier",
      z_score: outlier.z_score,
      severity: outlier.severity || null,
      pct_change: outlier.return_price,
      candidate_terms: buildTerms(outlier),
    };
    try {
      await dashboardApi.investigateOutlier(req);
      setJob({ status: "running" });
      pollRef.current = setInterval(() => void fetchStatus(), POLL_MS);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const status  = job?.status ?? "not_investigated";
  const isDone  = status === "done";
  const isRunning = status === "running";
  const sources  = job?.sources ?? [];
  const claims   = (job?.claims ?? []).filter((c) => c.claim_text);
  const findings = job?.llm_key_findings ?? [];

  return (
    <div className="evidence-panel">
      {/* ── header row ── */}
      <div className="evidence-header">
        <span className="evidence-label">Evidence</span>
        <span className={`pill evidence-status ${statusPillClass(status)}`}>{status}</span>
        {isDone && job?.verdict ? (
          <span className={`pill ${VERDICT_PILL[job.verdict] ?? "sev-medium"}`}>{job.verdict}</span>
        ) : null}
        {isDone && job?.confidence ? (
          <span className="muted evidence-conf">conf: {job.confidence}</span>
        ) : null}
        {!isDone && !isRunning ? (
          <button className="ghost tiny" onClick={() => void handleInvestigate()}>
            Investigate
          </button>
        ) : null}
        {isRunning ? <span className="muted evidence-spinning">running…</span> : null}
      </div>

      {error ? <div className="err">{error}</div> : null}

      {isDone ? (
        <>
          {/* ── tab bar ── */}
          <div className="ev-tabs">
            <button
              className={`ev-tab${tab === "summary" ? " active" : ""}`}
              onClick={() => setTab("summary")}
            >
              Summary
            </button>
            <button
              className={`ev-tab${tab === "sources" ? " active" : ""}`}
              onClick={() => setTab("sources")}
            >
              Sources ({sources.length})
            </button>
            <button
              className={`ev-tab${tab === "claims" ? " active" : ""}`}
              onClick={() => setTab("claims")}
            >
              Claims ({claims.length})
            </button>
          </div>

          {/* ── tab content ── */}
          {tab === "summary" ? (
            <SummaryTab job={job!} findings={findings} />
          ) : tab === "sources" ? (
            <SourcesTab sources={sources} />
          ) : (
            <ClaimsTab claims={claims} />
          )}
        </>
      ) : null}

      {status === "error" && job?.error ? (
        <div className="err">{job.error}</div>
      ) : null}
    </div>
  );
}

function SummaryTab({ job, findings }: { job: EvidenceCaseStatus["job"]; findings: string[] }) {
  return (
    <>
      {job.llm_summary ? (
        <p className="evidence-summary">{job.llm_summary}</p>
      ) : (
        <p className="muted evidence-summary">No summary available.</p>
      )}
      {findings.length > 0 ? (
        <>
          <div className="evidence-label" style={{ marginTop: 6 }}>Key findings</div>
          <ul className="evidence-findings">
            {findings.map((f, i) => <li key={i}>{f}</li>)}
          </ul>
        </>
      ) : null}
      {job.limitations && job.limitations.length > 0 ? (
        <div className="evidence-limits">
          {job.limitations.map((l) => (
            <span key={l} className="pill sev-medium ev-limit">{l}</span>
          ))}
        </div>
      ) : null}
    </>
  );
}

function SourcesTab({ sources }: { sources: EvidenceSource[] }) {
  if (sources.length === 0) {
    return <p className="muted evidence-summary">No sources recorded.</p>;
  }
  const tierOrder = ["tier1_official", "tier2_reputable", "tier3_general", "tier4_social", ""];
  const sorted = [...sources].sort(
    (a, b) => tierOrder.indexOf(a.source_tier) - tierOrder.indexOf(b.source_tier)
  );
  return (
    <ul className="ev-sources">
      {sorted.map((s, i) => (
        <li key={i} className="ev-source-row">
          <span className={`pill ev-tier ${tierBadge(s.source_tier)}`}>
            {tierLabel(s.source_tier)}
          </span>
          <a
            href={s.url}
            target="_blank"
            rel="noreferrer noopener"
            className="ev-source-link"
            title={s.url}
          >
            {s.title || domain(s.url)}
          </a>
        </li>
      ))}
    </ul>
  );
}

function ClaimsTab({ claims }: { claims: EvidenceClaim[] }) {
  if (claims.length === 0) {
    return <p className="muted evidence-summary">No claims extracted.</p>;
  }
  const sorted = [...claims].sort((a, b) => (b.support_score ?? 0) - (a.support_score ?? 0));
  return (
    <ul className="ev-claims">
      {sorted.map((c, i) => (
        <li key={i} className="ev-claim-row">
          <div className="ev-claim-meta">
            {c.support_score != null ? (
              <span className={`pill ${c.support_score >= 0.8 ? "sev-low" : c.support_score >= 0.5 ? "sev-medium" : "sev-high"}`}>
                {(c.support_score * 100).toFixed(0)}%
              </span>
            ) : null}
            {c.event_type ? <span className="muted ev-claim-type">{c.event_type}</span> : null}
          </div>
          <p className="ev-claim-text">{c.claim_text}</p>
        </li>
      ))}
    </ul>
  );
}

// ── helpers ──────────────────────────────────────────────────────────────────

function statusPillClass(status: string) {
  if (status === "done")    return "sev-low";
  if (status === "running") return "sev-medium";
  if (status === "error")   return "sev-high";
  return "";
}

function tierBadge(tier: string) {
  if (tier === "tier1_official")   return "sev-low";
  if (tier === "tier2_reputable")  return "sev-medium";
  return "";
}

function tierLabel(tier: string) {
  if (tier === "tier1_official")   return "T1";
  if (tier === "tier2_reputable")  return "T2";
  if (tier === "tier3_general")    return "T3";
  if (tier === "tier4_social")     return "T4";
  return "?";
}

function domain(url: string) {
  try { return new URL(url).hostname.replace(/^www\./, ""); }
  catch { return url.slice(0, 40); }
}

function buildTerms(o: EvidenceOutlier): string[] {
  const sym  = o.symbol;
  const ym   = o.as_of_date?.slice(0, 7);
  const terms: string[] = [];
  if (sym && ym)          terms.push(`${sym} stock ${ym}`);
  if (sym)                terms.push(`${sym} earnings`);
  if (sym && o.direction === "low")  terms.push(`${sym} stock drop`);
  if (sym && o.direction === "high") terms.push(`${sym} stock rally`);
  return terms;
}
