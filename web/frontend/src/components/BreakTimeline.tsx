import type { TrendDay } from "../types";

export function BreakTimeline({ trend }: { trend: TrendDay[] }) {
  const max = Math.max(1, ...trend.map((day) => day.total));
  const px = (value: number) => Math.round((value / max) * 44);
  const last = trend.length - 1;

  return (
    <section>
      <div className="shead">
        <h2>Break timeline</h2>
        <span className="shint">breaks detected per pipeline-run day, by severity</span>
        <span className="c align-right">
          {trend.length
            ? trend.length === 1
              ? `${trend[0].day} · 1 day`
              : `${trend[0].day} → ${trend[last].day} · ${trend.length} day(s)`
            : ""}
        </span>
      </div>
      <div className="card">
        <div className="tl">
          {trend.length ? (
            trend.map((day, i) => {
              const showLabel = trend.length === 1 || i === 0 || i === last;
              return (
                <div
                  key={day.day}
                  style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}
                >
                  <div
                    className="bar"
                    title={`${day.day}: ${day.total} break(s)  H${day.high} M${day.medium} L${day.low}`}
                    style={{ height: `${Math.max(3, px(day.total))}px` }}
                  >
                    <div className="s-low" style={{ height: `${px(day.low)}px` }} />
                    <div className="s-medium" style={{ height: `${px(day.medium)}px` }} />
                    <div className="s-high" style={{ height: `${px(day.high)}px` }} />
                  </div>
                  {showLabel && (
                    <span style={{ fontSize: 9, color: "#6b7280", whiteSpace: "nowrap", transform: "rotate(-35deg)", transformOrigin: "top left", marginTop: 2 }}>
                      {day.day.slice(5)}
                    </span>
                  )}
                </div>
              );
            })
          ) : (
            <span className="faint timeline-empty">No breaks recorded yet</span>
          )}
        </div>
      </div>
    </section>
  );
}
