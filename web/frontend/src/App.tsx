import { useCallback, useEffect, useState } from "react";

import { dashboardApi } from "./api";
import { AppHeader } from "./components/AppHeader";
import { BreakDetailModal } from "./components/BreakDetailModal";
import { BreakTimeline } from "./components/BreakTimeline";
import { BreaksList } from "./components/BreaksList";
import { DiffModal } from "./components/DiffModal";
import { HelpModal } from "./components/HelpModal";
import { RunDetailModal } from "./components/RunDetailModal";
import { RunsTable } from "./components/RunsTable";
import { StatsBar } from "./components/StatsBar";
import type { BreakRow, FleetSummary, RunRow, TrendDay } from "./types";

type ModalState =
  | { kind: "none" }
  | { kind: "help" }
  | { kind: "run"; runId: string }
  | { kind: "diff"; runId: string; backRunId?: string }
  | { kind: "break"; breakRow: BreakRow };

export function App() {
  const [summary, setSummary] = useState<FleetSummary | null>(null);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [trend, setTrend] = useState<TrendDay[]>([]);
  const [breaks, setBreaks] = useState<BreakRow[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [severityFilter, setSeverityFilter] = useState("");
  const [modal, setModal] = useState<ModalState>({ kind: "none" });
  const [error, setError] = useState<string | null>(null);

  const loadRunsAndTrend = useCallback(async () => {
    const [runsResponse, trendResponse] = await Promise.all([dashboardApi.runs(), dashboardApi.trend()]);
    setSummary(runsResponse.summary);
    setRuns(runsResponse.runs);
    setTrend(trendResponse.trend);
  }, []);

  const loadBreaks = useCallback(async () => {
    const breaksResponse = await dashboardApi.breaks(statusFilter, severityFilter);
    setBreaks(breaksResponse.breaks);
  }, [severityFilter, statusFilter]);

  const loadAll = useCallback(async () => {
    setError(null);
    try {
      await Promise.all([loadRunsAndTrend(), loadBreaks()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [loadBreaks, loadRunsAndTrend]);

  useEffect(() => {
    void loadRunsAndTrend().catch((err: Error) => setError(err.message));
    const handle = window.setInterval(() => {
      void loadRunsAndTrend().catch((err: Error) => setError(err.message));
    }, 5000);
    return () => window.clearInterval(handle);
  }, [loadRunsAndTrend]);

  useEffect(() => {
    void loadBreaks().catch((err: Error) => setError(err.message));
    const handle = window.setInterval(() => {
      void loadBreaks().catch((err: Error) => setError(err.message));
    }, 5000);
    return () => window.clearInterval(handle);
  }, [loadBreaks]);

  return (
    <div className="wrap">
      <AppHeader onHelp={() => setModal({ kind: "help" })} onRefresh={loadAll} />
      {error ? <div className="top-error">{error}</div> : null}
      <StatsBar summary={summary} />
      <BreakTimeline trend={trend} />
      <RunsTable
        runs={runs}
        onOpenRun={(runId) => setModal({ kind: "run", runId })}
        onOpenDiff={(runId) => setModal({ kind: "diff", runId })}
      />
      <BreaksList
        breaks={breaks}
        status={statusFilter}
        severity={severityFilter}
        onStatusChange={setStatusFilter}
        onSeverityChange={setSeverityFilter}
        onOpenBreak={(breakRow) => setModal({ kind: "break", breakRow })}
      />

      {modal.kind === "help" ? <HelpModal onClose={() => setModal({ kind: "none" })} /> : null}
      {modal.kind === "run" ? (
        <RunDetailModal
          runId={modal.runId}
          onClose={() => setModal({ kind: "none" })}
          onOpenDiff={(runId) => setModal({ kind: "diff", runId, backRunId: runId })}
        />
      ) : null}
      {modal.kind === "diff" ? (
        <DiffModal
          runId={modal.runId}
          onBack={modal.backRunId ? () => setModal({ kind: "run", runId: modal.backRunId! }) : undefined}
          onClose={() => setModal({ kind: "none" })}
        />
      ) : null}
      {modal.kind === "break" ? (
        <BreakDetailModal
          breakRow={modal.breakRow}
          onClose={() => setModal({ kind: "none" })}
          onApplied={() => {
            setModal({ kind: "none" });
            void loadAll();
          }}
        />
      ) : null}
    </div>
  );
}
