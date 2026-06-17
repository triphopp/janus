import { ChevronRight } from "lucide-react";

import { t19 } from "../format";
import type { BreakRow } from "../types";
import { SeverityPill, StatusPill } from "./Badges";

export function BreaksList({
  breaks,
  status,
  severity,
  onStatusChange,
  onSeverityChange,
  onOpenBreak
}: {
  breaks: BreakRow[];
  status: string;
  severity: string;
  onStatusChange: (value: string) => void;
  onSeverityChange: (value: string) => void;
  onOpenBreak: (breakRow: BreakRow) => void;
}) {
  return (
    <section>
      <div className="shead">
        <h2>Breaks</h2>
        <span className="shint">flagged anomalies and their signed lifecycle</span>
        <span className="c">{breaks.length} shown</span>
        <span className="spacer" />
        <select className="ghost" value={status} onChange={(event) => onStatusChange(event.target.value)}>
          <option value="">All status</option>
          <option>DETECTED</option>
          <option>TRIAGED</option>
          <option>ESCALATED</option>
          <option>ACKNOWLEDGED</option>
          <option>AUTO_RESOLVED</option>
          <option>CLOSED</option>
        </select>
        <select className="ghost" value={severity} onChange={(event) => onSeverityChange(event.target.value)}>
          <option value="">All severity</option>
          <option>high</option>
          <option>medium</option>
          <option>low</option>
        </select>
      </div>
      <div className="card">
        <div className="blist">
          {breaks.length ? (
            breaks.map((item) => {
              const from = item.stage_from || item.stage || "-";
              const to = item.stage_to || "-";
              return (
                <div className="brow" key={`${item.run_id}:${item.break_id}`} onClick={() => onOpenBreak(item)}>
                  <SeverityPill severity={item.severity} />
                  <div className="break-main">
                    <div className="btype">{item.type || "break"}</div>
                    <div className="bid mono">{item.break_id}</div>
                  </div>
                  <div className="bflow">
                    {from} <ChevronRight size={13} /> {to}
                  </div>
                  <span className="spacer" />
                  <span className="muted">{t19(item.detected_at)}</span>
                  <StatusPill status={item.status} />
                </div>
              );
            })
          ) : (
            <div className="empty">
              <div className="et">No breaks found</div>
              <div className="ed">The current filters do not match any break records.</div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
