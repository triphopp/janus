import type { TrendDay } from "../types";

export function BreakTimeline({ trend }: { trend: TrendDay[] }) {
  const max = Math.max(1, ...trend.map((day) => day.total));
  const px = (value: number) => Math.round((value / max) * 44);

  return (
    <section>
      <div className="shead">
        <h2>Break timeline</h2>
        <span className="shint">data anomalies flagged per day, by severity</span>
        <span className="c align-right">{trend.length ? `${trend[0].day} -> ${trend[trend.length - 1].day}` : ""}</span>
      </div>
      <div className="card">
        <div className="tl">
          {trend.length ? (
            trend.map((day) => (
              <div
                className="bar"
                key={day.day}
                title={`${day.day}: ${day.total} (H${day.high} M${day.medium} L${day.low})`}
                style={{ height: `${Math.max(3, px(day.total))}px` }}
              >
                <div className="s-low" style={{ height: `${px(day.low)}px` }} />
                <div className="s-medium" style={{ height: `${px(day.medium)}px` }} />
                <div className="s-high" style={{ height: `${px(day.high)}px` }} />
              </div>
            ))
          ) : (
            <span className="faint timeline-empty">No breaks recorded yet</span>
          )}
        </div>
        <div className="tllab">{trend.length ? `${trend.length} day(s)` : ""}</div>
      </div>
    </section>
  );
}
