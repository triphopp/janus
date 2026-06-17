import { useEffect, useState } from "react";

import { dashboardApi } from "../api";
import { fmtNum, pct, valueText } from "../format";
import type { RawRow } from "../types";

export function RawSourcePanel({
  runId,
  symbol,
  asOfDate
}: {
  runId: string;
  symbol: string;
  asOfDate: string;
}) {
  const [row, setRow] = useState<RawRow | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setRow(null);
    setError(null);
    dashboardApi
      .rawRow(runId, symbol, asOfDate)
      .then((data) => active && setRow(data))
      .catch((err: Error) => active && setError(err.message));
    return () => {
      active = false;
    };
  }, [asOfDate, runId, symbol]);

  if (error) return <div className="err">raw row not available ({error})</div>;
  if (!row) return <div className="loading">Loading raw source...</div>;

  const tagged = Boolean(row._return_outlier_flag);

  return (
    <div className="flow adjflow raw-flow">
      <FlowNode label="provider" value={valueText(row.provider)} mono />
      <FlowNode label="raw_close" value={fmtNum(row.raw_close)} mono />
      <FlowNode
        label={`adj_factor${row.price_adjustment_warning ? " warning" : ""}`}
        value={fmtNum(row.adj_factor)}
        mono
        tone={row.price_adjustment_warning ? "bad" : undefined}
      />
      <FlowNode label="price_std" value={fmtNum(row.price_std)} mono />
      <FlowNode label="return_raw" value={pct(row.return_raw)} mono tone={tagged ? "bad" : undefined} />
      <FlowNode label="return_std" value={pct(row.return_std)} mono tone="ok" />
      {row.return_winsorized != null ? <FlowNode label="return_winsorized" value={pct(row.return_winsorized)} mono /> : null}
    </div>
  );
}

function FlowNode({
  label,
  value,
  mono,
  tone
}: {
  label: string;
  value: string;
  mono?: boolean;
  tone?: "bad" | "ok";
}) {
  return (
    <div className="fnode">
      <div className="fl">{label}</div>
      <div className={`fv ${mono ? "mono" : ""} ${tone || ""}`}>{value}</div>
    </div>
  );
}
