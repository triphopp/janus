import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";

import { t19 } from "../format";
import type { BreakRow } from "../types";
import { SeverityPill, StatusPill } from "./Badges";

const PAGE_SIZE = 200;

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
  const [page, setPage] = useState(0);

  // Reset to first page when filters change
  useEffect(() => { setPage(0); }, [breaks.length, status, severity]);

  const totalPages = Math.max(1, Math.ceil(breaks.length / PAGE_SIZE));
  const pageBreaks = breaks.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const from = breaks.length ? page * PAGE_SIZE + 1 : 0;
  const to = Math.min((page + 1) * PAGE_SIZE, breaks.length);

  return (
    <section>
      <div className="shead">
        <h2>Breaks</h2>
        <span className="shint">flagged anomalies and their signed lifecycle</span>
        <span className="c">{breaks.length} total</span>
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
          {pageBreaks.length ? (
            pageBreaks.map((item) => {
              const stageFrom = item.stage_from || item.stage || "-";
              const stageTo = item.stage_to || "-";
              return (
                <div className="brow" key={`${item.run_id}:${item.break_id}`} onClick={() => onOpenBreak(item)}>
                  <SeverityPill severity={item.severity} />
                  <div className="break-main">
                    <div className="btype">{item.type || "break"}</div>
                    <div className="bid mono">{item.break_id}</div>
                  </div>
                  <div className="bflow">
                    {stageFrom} <ChevronRight size={13} /> {stageTo}
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
        {totalPages > 1 && (
          <div className="shead" style={{ borderTop: "1px solid #23283a", paddingTop: 8, marginTop: 0 }}>
            <button
              className="ghost"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
            >
              <ChevronLeft size={14} />
            </button>
            <span className="muted" style={{ fontSize: 12 }}>
              {from}–{to} of {breaks.length}
            </span>
            <button
              className="ghost"
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
            >
              <ChevronRight size={14} />
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
