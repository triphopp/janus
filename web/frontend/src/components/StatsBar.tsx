import { StatCard } from "./Badges";

import type { FleetSummary } from "../types";

export function StatsBar({ summary }: { summary?: FleetSummary | null }) {
  const s =
    summary ||
    ({
      n_runs: 0,
      total_changes: 0,
      total_adjustment_warnings: 0,
      total_unattributed: 0,
      breaks_total: 0,
      breaks_open: 0,
      sev_high: 0
    } satisfies FleetSummary);

  return (
    <div className="stats">
      <StatCard value={s.n_runs} label="runs" />
      <StatCard value={s.total_changes} label="stage changes" />
      <StatCard
        value={s.total_adjustment_warnings || 0}
        label="adj warnings"
        tone={s.total_adjustment_warnings ? "warn" : "good"}
      />
      <StatCard value={s.dq_runs_failing || 0} label="DQ fail" tone={s.dq_runs_failing ? "warn" : "good"} />
      <StatCard value={s.dq_runs_warning || 0} label="DQ warn" tone={s.dq_runs_warning ? "warn" : "good"} />
      <StatCard value={s.total_unattributed} label="unattributed" tone={s.total_unattributed ? "warn" : "good"} />
      <StatCard value={s.breaks_total} label="breaks" />
      <StatCard value={s.breaks_open} label="open" tone={s.breaks_open ? "warn" : "good"} />
      <StatCard value={s.sev_high} label="high severity" tone={s.sev_high ? "warn" : "good"} />
    </div>
  );
}
