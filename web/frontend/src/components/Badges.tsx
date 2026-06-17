import type { ReactNode } from "react";

import type { Severity } from "../types";

export function SeverityPill({ severity }: { severity?: Severity | null }) {
  const sev = severity || "unknown";
  return (
    <span className={`pill sev-${sev}`}>
      <span className={`sdot ${String(sev).slice(0, 1)}`} />
      {sev}
    </span>
  );
}

export function StatusPill({ status }: { status?: string | null }) {
  return <span className={`pill st-${status || "UNKNOWN"}`}>{status || "UNKNOWN"}</span>;
}

export function MetricPill({
  strategyMetricsAvailable
}: {
  strategyMetricsAvailable?: boolean | null;
}) {
  const label = strategyMetricsAvailable ? "strategy" : "market";
  const title = strategyMetricsAvailable ? "strategy return stream" : "market return diagnostic";
  return (
    <span className={`pill ${strategyMetricsAvailable ? "sev-low" : "sev-medium"}`} title={title}>
      {label}
    </span>
  );
}

export function StatCard({
  value,
  label,
  tone
}: {
  value: ReactNode;
  label: string;
  tone?: "warn" | "good";
}) {
  return (
    <div className={`stat ${tone || ""}`}>
      <div className="n">{value}</div>
      <div className="l">{label}</div>
    </div>
  );
}
