import React, { useState } from "react";
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

// Technical/config keys — shown collapsed in a "details" toggle, not in primary grid.
const METRIC_GRID_SECONDARY = new Set([
  "output_dir", "run_id",
  "passed_stability_score", "fold_metric_scope", "metrics_mode",
  "metrics_return_col", "derived_return_col", "strategy_return_col",
  "return_outlier_policy", "metric_warning", "sample_floor_breached",
  "allow_retro_adjusted_prices",
  "adj_factor_min", "adj_factor_max",
  "mean_abs_price_std_vs_provider_adjusted",
  "max_abs_price_std_vs_provider_adjusted",
  // pipeline infra / artifact paths
  "guard_status", "split_adjustments", "coverage_gate", "data_cache_mode",
  "attribution", "audit_snapshots", "data_export",
  "artifacts", "manifest", "manifest_path",
  "summary_report", "html_report",
]);

function MetricGrid({ section }: { section: DashboardSection }) {
  const [showTech, setShowTech] = useState(false);
  const payload = section.payload as Record<string, unknown> | null;
  if (!payload) {
    return <p style={{ color: "#9ca3af", fontSize: 12 }}>{section.empty_reason ?? "No data"}</p>;
  }

  const flat = Object.entries(payload).filter(([, v]) => typeof v !== "object" || v === null);
  const primary   = flat.filter(([k]) => !METRIC_GRID_SECONDARY.has(k));
  const secondary = flat.filter(([k]) =>  METRIC_GRID_SECONDARY.has(k));

  return (
    <div>
      {primary.length > 0 ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 20px", marginBottom: secondary.length ? 6 : 0 }}>
          {primary.map(([k, v]) => (
            <span key={k} style={{ fontSize: 12, color: "#d1d5db" }}>
              <span style={{ color: "#9ca3af" }}>{k}:</span>{" "}
              <strong>{String(v ?? "—")}</strong>
            </span>
          ))}
        </div>
      ) : null}

      {secondary.length > 0 ? (
        <>
          <button
            onClick={() => setShowTech((s) => !s)}
            style={{
              background: "none", border: "none", cursor: "pointer",
              color: "#6b7280", fontSize: 10, padding: "2px 0",
              display: "flex", alignItems: "center", gap: 4,
            }}
          >
            <span style={{ transform: showTech ? "rotate(90deg)" : "none", display: "inline-block", transition: "transform 0.15s" }}>▶</span>
            {showTech ? "hide" : `${secondary.length} technical fields`}
          </button>
          {showTech ? (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px", marginTop: 4, padding: "6px 8px", background: "rgba(0,0,0,.15)", borderRadius: 4 }}>
              {secondary.map(([k, v]) => (
                <span key={k} style={{ fontSize: 10, color: "#6b7280", fontFamily: "monospace" }}>
                  {k}=<span style={{ color: "#9ca3af" }}>{String(v ?? "—")}</span>
                </span>
              ))}
            </div>
          ) : null}
        </>
      ) : null}

      {primary.length === 0 && secondary.length === 0 ? (
        <p style={{ color: "#9ca3af", fontSize: 12 }}>—</p>
      ) : null}
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
