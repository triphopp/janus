import { useCallback, useEffect, useRef, useState } from "react";
import { dashboardApi } from "../api";
import type { EvidenceCaseStatus, EvidenceInvestigateRequest, EvidenceOutlier } from "../types";

const POLL_MS = 3000;

const VERDICT_CLASS: Record<string, string> = {
  supported_event: "ok",
  contradicted: "bad",
  insufficient_evidence: "warn",
  failed: "bad",
};

function verdictClass(v: string | null | undefined) {
  return VERDICT_CLASS[v ?? ""] ?? "muted";
}

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
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await dashboardApi.evidenceCaseStatus(outlier.case_id);
      setJob(res.job);
      if (res.job.status === "done" || res.job.status === "error") {
        stopPolling();
      }
    } catch {
      // keep polling — transient network error
    }
  }, [outlier.case_id, stopPolling]);

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

  const status = job?.status ?? "not_investigated";
  const isDone = status === "done";
  const isRunning = status === "running";

  return (
    <div className="evidence-panel">
      <div className="evidence-header">
        <span className="evidence-label">Evidence</span>
        <span className={`pill evidence-status ${statusPillClass(status)}`}>{status}</span>
        {isDone && job?.verdict ? (
          <span className={`pill ${verdictClass(job.verdict)}`}>{job.verdict}</span>
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

      {isDone && job?.llm_summary ? (
        <div className="evidence-summary">{job.llm_summary}</div>
      ) : null}

      {isDone && job?.llm_key_findings && job.llm_key_findings.length > 0 ? (
        <ul className="evidence-findings">
          {job.llm_key_findings.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      ) : null}

      {status === "error" && job?.error ? (
        <div className="err">{job.error}</div>
      ) : null}
    </div>
  );
}

function statusPillClass(status: string) {
  if (status === "done") return "sev-low";
  if (status === "running") return "sev-medium";
  if (status === "error") return "sev-high";
  return "";
}

function buildTerms(o: EvidenceOutlier): string[] {
  const sym = o.symbol;
  const date = o.as_of_date?.slice(0, 7); // YYYY-MM
  const terms: string[] = [];
  if (sym && date) terms.push(`${sym} stock ${date}`);
  if (sym) terms.push(`${sym} earnings`);
  if (sym && o.direction === "low") terms.push(`${sym} stock drop`);
  if (sym && o.direction === "high") terms.push(`${sym} stock rally`);
  return terms;
}
