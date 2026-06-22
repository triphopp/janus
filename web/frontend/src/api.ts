import type { BreaksResponse, RawRow, RunDetail, RunsResponse, TrendDay } from "./types";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // Keep the HTTP status when the body is not JSON.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const dashboardApi = {
  runs: () => api<RunsResponse>("/api/runs"),
  runDetail: (runId: string) => api<RunDetail>(`/api/runs/${encodeURIComponent(runId)}`),
  breaks: (status?: string, severity?: string) => {
    const qs = new URLSearchParams();
    if (status) qs.set("status", status);
    if (severity) qs.set("severity", severity);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return api<BreaksResponse>(`/api/breaks${suffix}`);
  },
  trend: () => api<{ trend: TrendDay[] }>("/api/trend"),
  rawRow: (runId: string, symbol: string, asOfDate: string) => {
    const qs = new URLSearchParams({ symbol, as_of_date: asOfDate });
    return api<RawRow>(`/api/runs/${encodeURIComponent(runId)}/raw-row?${qs.toString()}`);
  },
  diffMeta: (runId: string) =>
    api<Record<string, unknown>>(`/api/runs/${encodeURIComponent(runId)}/diff-meta`),
  diffSummary: (runId: string) =>
    api<Record<string, unknown>>(`/api/runs/${encodeURIComponent(runId)}/diff-summary`),
  transitionBreak: (
    runId: string,
    breakId: string,
    body: {
      to_status: string;
      actor_id: string;
      actor_role: string;
      reason_code?: string | null;
      note?: string | null;
    }
  ) =>
    api<{ ok: boolean }>(`/api/breaks/${encodeURIComponent(runId)}/${encodeURIComponent(breakId)}/transition`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    })
};
