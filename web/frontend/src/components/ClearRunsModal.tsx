import { X } from "lucide-react";
import { useState } from "react";

import { Modal } from "./Modal";

const CONFIRM_WORD = "clear";

export function ClearRunsModal({
  onConfirm,
  onClose,
}: {
  onConfirm: () => Promise<void>;
  onClose: () => void;
}) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const ready = input === CONFIRM_WORD;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal onClose={onClose}>
      <div className="mhead">
        <h3>Clear all pipeline data</h3>
        <button className="x" onClick={onClose} title="Close">
          <X size={18} />
        </button>
      </div>
      <div className="block">
        <p style={{ fontSize: 13, color: "#d1d5db", marginBottom: 12 }}>
          This will permanently delete all pipeline runs, diffs, manifests, and breaks.
          Source data and audit logs are untouched.
        </p>
        <p style={{ fontSize: 13, color: "#9ca3af", marginBottom: 8 }}>
          Type <code style={{ color: "#ef4444" }}>{CONFIRM_WORD}</code> to confirm:
        </p>
        <form onSubmit={handleSubmit} style={{ display: "flex", gap: 8 }}>
          <input
            autoFocus
            className="ghost"
            style={{ flex: 1, fontFamily: "monospace", fontSize: 13, padding: "4px 8px" }}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={CONFIRM_WORD}
            disabled={busy}
            spellCheck={false}
          />
          <button
            type="submit"
            className="ghost icon-button"
            style={{
              color: ready ? "#ef4444" : "#4b5563",
              border: `1px solid ${ready ? "#ef4444" : "#374151"}`,
              padding: "4px 14px",
              fontSize: 12,
              cursor: ready ? "pointer" : "default",
              transition: "color 0.15s, border-color 0.15s",
            }}
            disabled={!ready || busy}
          >
            {busy ? "Clearing…" : "Clear all"}
          </button>
        </form>
      </div>
    </Modal>
  );
}
