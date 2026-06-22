import React from "react";
import type { DashboardSection } from "../types";

type Props = {
  section: DashboardSection;
};

function ScoreStatus({ status }: { status: string | null }) {
  const color =
    status === "pass" ? "#22c55e"
    : status === "warn" ? "#f59e0b"
    : status === "fail" ? "#ef4444"
    : "#9ca3af";
  return <span style={{ color, fontWeight: 600 }}>{status ?? "—"}</span>;
}

function MetricGrid({ section }: { section: DashboardSection }) {
  const payload = section.payload as Record<string, unknown> | null;
  if (!payload) {
    return <p style={{ color: "#9ca3af", fontSize: 12 }}>{section.empty_reason ?? "No data"}</p>;
  }
  const entries = Object.entries(payload).filter(
    ([, v]) => typeof v !== "object" || v === null
  );
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "8px 24px" }}>
      {entries.map(([k, v]) => (
        <span key={k} style={{ fontSize: 12, color: "#d1d5db" }}>
          <span style={{ color: "#9ca3af" }}>{k}:</span>{" "}
          <strong>{String(v ?? "—")}</strong>
        </span>
      ))}
    </div>
  );
}

function ScorecardBody({ section }: { section: DashboardSection }) {
  const dq = section.payload as {
    status?: string;
    enforcement?: string;
    worst_dimension?: string;
    dimensions?: Array<{ name: string; rate: number; status: string }>;
  } | null;
  if (!dq) {
    return (
      <p style={{ color: "#9ca3af", fontSize: 12 }}>
        {section.empty_reason ?? "No scorecard recorded"}
      </p>
    );
  }
  return (
    <div>
      <div style={{ marginBottom: 6, fontSize: 12, color: "#d1d5db" }}>
        <strong>Status:</strong> <ScoreStatus status={dq.status ?? null} />{" "}
        {dq.enforcement && (
          <span style={{ color: "#9ca3af", marginLeft: 8 }}>enforcement: {dq.enforcement}</span>
        )}
        {dq.worst_dimension && (
          <span style={{ color: "#9ca3af", marginLeft: 8 }}>worst: {dq.worst_dimension}</span>
        )}
      </div>
      {(dq.dimensions ?? []).length > 0 && (
        <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ color: "#9ca3af" }}>
              {["dimension", "rate", "n_defect / n_total", "status"].map((h) => (
                <th key={h} style={{ textAlign: "left", padding: "2px 6px" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(dq.dimensions ?? []).map((d) => (
              <tr key={d.name} style={{ borderTop: "1px solid #374151" }}>
                <td style={{ padding: "2px 6px", color: "#d1d5db" }}>{d.name}</td>
                <td style={{ padding: "2px 6px", color: "#d1d5db" }}>
                  {(d.rate * 100).toFixed(2)}%
                </td>
                <td style={{ padding: "2px 6px", color: "#d1d5db" }}>
                  {(d as Record<string, unknown>).n_defect as number} /{" "}
                  {(d as Record<string, unknown>).n_total as number}
                </td>
                <td style={{ padding: "2px 6px" }}>
                  <ScoreStatus status={d.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ArtifactLink({ section }: { section: DashboardSection }) {
  const vs = section.payload as Record<string, unknown> | null;
  return (
    <div style={{ fontSize: 12, color: "#d1d5db" }}>
      {vs ? (
        <span>
          {vs.heatmap_count != null && <span>heatmaps: {String(vs.heatmap_count)} </span>}
          {vs.latest_as_of != null && <span>· latest: {String(vs.latest_as_of)}</span>}
        </span>
      ) : (
        <span style={{ color: "#9ca3af" }}>{section.empty_reason ?? "—"}</span>
      )}
    </div>
  );
}

function RawJson({ section }: { section: DashboardSection }) {
  return (
    <pre
      style={{
        fontSize: 10,
        color: "#9ca3af",
        overflowX: "auto",
        maxHeight: 120,
        margin: 0,
      }}
    >
      {JSON.stringify(section.payload, null, 2)}
    </pre>
  );
}

export function SectionPanel({ section }: Props) {
  const body = (() => {
    if (section.kind === "scorecard") return <ScorecardBody section={section} />;
    if (section.kind === "metric_grid") return <MetricGrid section={section} />;
    if (section.kind === "artifact_link") return <ArtifactLink section={section} />;
    return <RawJson section={section} />;
  })();

  return (
    <div style={{ marginBottom: 16 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 6,
          borderBottom: "1px solid #374151",
          paddingBottom: 4,
        }}
      >
        <span style={{ fontWeight: 600, fontSize: 13, color: "#f3f4f6" }}>
          {section.title}
        </span>
        {section.status && section.kind !== "scorecard" && (
          <ScoreStatus status={section.status} />
        )}
      </div>
      {body}
    </div>
  );
}
