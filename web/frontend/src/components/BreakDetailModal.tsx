import { X } from "lucide-react";
import { useMemo, useState } from "react";

import { dashboardApi } from "../api";
import { h8, t19, valueText } from "../format";
import type { BreakRow } from "../types";
import { SeverityPill, StatusPill } from "./Badges";
import { Modal } from "./Modal";
import { RawSourcePanel } from "./RawSourcePanel";

const NEXT: Record<string, string[]> = {
  DETECTED: ["TRIAGED"],
  TRIAGED: ["AUTO_RESOLVED", "ACKNOWLEDGED", "ESCALATED"],
  AUTO_RESOLVED: ["CLOSED"],
  ACKNOWLEDGED: ["CLOSED"],
  ESCALATED: ["ACKNOWLEDGED", "CLOSED"],
  CLOSED: []
};

const TERMINAL = new Set(["CLOSED"]);

export function BreakDetailModal({
  breakRow,
  onClose,
  onApplied
}: {
  breakRow: BreakRow;
  onClose: () => void;
  onApplied: () => void;
}) {
  const next = NEXT[breakRow.status] || [];
  const [toStatus, setToStatus] = useState(next[0] || "");
  const [actorId, setActorId] = useState("");
  const [actorRole, setActorRole] = useState("analyst");
  const [reasonCode, setReasonCode] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const keyText = useMemo(
    () =>
      Object.entries(breakRow.key || {})
        .map(([key, value]) => `${key}=${valueText(value)}`)
        .join(" | ") || "-",
    [breakRow.key]
  );
  const symbol = breakRow.key?.symbol != null ? String(breakRow.key.symbol) : "";
  const asOfDate = breakRow.key?.as_of_date != null ? String(breakRow.key.as_of_date).slice(0, 10) : "";

  async function submit() {
    if (!actorId.trim()) {
      setError("actor_id required");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await dashboardApi.transitionBreak(breakRow.run_id, breakRow.break_id, {
        to_status: toStatus,
        actor_id: actorId.trim(),
        actor_role: actorRole,
        reason_code: reasonCode.trim() || null,
        note: note.trim() || null
      });
      onApplied();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal onClose={onClose}>
      <div className="mhead">
        <SeverityPill severity={breakRow.severity} />
        <h3 className="mono">{breakRow.break_id}</h3>
        <button className="x" onClick={onClose} title="Close">
          <X size={18} />
        </button>
      </div>
      <div className="msub">
        {breakRow.type || "break"} | current <StatusPill status={breakRow.status} /> |{" "}
        <span className={breakRow.chain_valid === false ? "chain-bad" : "chain-ok"}>
          {breakRow.chain_valid === false ? "chain tampered" : "chain verified"}
        </span>
      </div>

      <div className="block">
        <div className="bt">Where it broke</div>
        <div className="flow">
          <FlowNode label="row key" value={keyText} mono />
          <FlowNode label="from stage" value={breakRow.stage_from || breakRow.stage || "-"} tone="stage" />
          <FlowNode label="to stage" value={breakRow.stage_to || "-"} tone="stage" />
          <FlowNode label={`change${breakRow.field ? ` | ${breakRow.field}` : ""}`} value={`${valueText(breakRow.before)} -> ${valueText(breakRow.after)}`} />
        </div>
      </div>

      <div className="block">
        <div className="bt">Lifecycle chain (signed)</div>
        <div className="chain">
          {(breakRow.history || []).map((entry, index) => {
            const term = TERMINAL.has(entry.to_status);
            const done = index < (breakRow.history || []).length - 1 || term;
            return (
              <div className="cstep" key={`${entry.to_status}:${entry.entry_hash}:${index}`}>
                <div className={`cnode ${term ? "term" : done ? "done" : ""}`}>{term ? "ok" : index + 1}</div>
                <div className="chain-status">
                  <StatusPill status={entry.to_status} />
                </div>
                <div className="cmeta">
                  <div className="who">{entry.actor_id || "-"}</div>
                  <div>
                    {entry.actor_role || "-"} | {t19(entry.at).slice(11) || t19(entry.at)}
                  </div>
                  {entry.reason_code ? <div className="rc">{entry.reason_code}</div> : null}
                  {entry.note ? <div>{entry.note}</div> : null}
                </div>
                <div className="clink" title="prev_hash -> entry_hash">
                  {`${h8(entry.prev_hash)} -> ${h8(entry.entry_hash)}`}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="block">
        <div className="bt">Downstream impact (lineage)</div>
        {breakRow.lineage_impact?.length ? (
          <div className="imp">
            <span className="col">{breakRow.field || "row"}</span>
            <span className="arrow">-&gt;</span>
            {breakRow.lineage_impact.map((field) => (
              <span className="tag" key={field}>
                {field}
              </span>
            ))}
          </div>
        ) : (
          <span className="muted">no downstream columns recorded</span>
        )}
      </div>

      {symbol && asOfDate ? (
        <div className="block">
          <div className="bt">Raw source</div>
          <RawSourcePanel runId={breakRow.run_id} symbol={symbol} asOfDate={asOfDate} />
        </div>
      ) : null}

      <div className="block">
        <div className="bt">Triage</div>
        {next.length ? (
          <div className="form">
            <div className="row">
              <div className="f">
                <label>Transition to</label>
                <select value={toStatus} onChange={(event) => setToStatus(event.target.value)}>
                  {next.map((status) => (
                    <option key={status}>{status}</option>
                  ))}
                </select>
              </div>
              <div className="f">
                <label>Actor ID</label>
                <input value={actorId} onChange={(event) => setActorId(event.target.value)} placeholder="alice@desk" />
              </div>
              <div className="f">
                <label>Role</label>
                <select value={actorRole} onChange={(event) => setActorRole(event.target.value)}>
                  <option>analyst</option>
                  <option>lead</option>
                  <option>system</option>
                </select>
              </div>
            </div>
            <div className="row">
              <div className="f">
                <label>Reason code</label>
                <input value={reasonCode} onChange={(event) => setReasonCode(event.target.value)} placeholder="benign_provider_revision" />
              </div>
              <div className="f">
                <label>Note</label>
                <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="optional" />
              </div>
            </div>
            <button className="btn" onClick={submit} disabled={busy}>
              {busy ? "Applying..." : "Sign and apply transition"}
            </button>
            {error ? <div className="err">{error}</div> : null}
          </div>
        ) : (
          <div className="term-note">Terminal state - chain closed, no further transitions.</div>
        )}
      </div>
    </Modal>
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
  tone?: "stage";
}) {
  return (
    <div className={`fnode ${tone || ""}`}>
      <div className="fl">{label}</div>
      <div className={`fv ${mono ? "mono" : ""}`}>{value}</div>
    </div>
  );
}
