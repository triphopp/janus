import { HelpCircle, RefreshCcw, Trash2 } from "lucide-react";
import { useState } from "react";

const WORDMARK = String.raw`     _   _    _   _ _   _ ____
    | | / \  | \ | | | | / ___|
 _  | |/ _ \ |  \| | | | \___ \
| |_| / ___ \| |\  | |_| |___) |
 \___/_/   \_\_| \_|\___/|____/`;

export function AppHeader({
  onHelp,
  onRefresh,
  onClearRuns,
}: {
  onHelp: () => void;
  onRefresh: () => void;
  onClearRuns: () => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [clearing, setClearing] = useState(false);

  async function handleConfirm() {
    setClearing(true);
    try {
      await onClearRuns();
    } finally {
      setClearing(false);
      setConfirming(false);
    }
  }

  return (
    <>
      <header className="appbar">
        <pre className="logo-art" title="Janus">
          {WORDMARK}
        </pre>
        <span className="dot" title="live auto-refresh 5s" />
        <button className="ghost icon-button" onClick={onRefresh} title="Refresh">
          <RefreshCcw size={15} />
        </button>
        {confirming ? (
          <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
            <span style={{ color: "#ef4444" }}>Clear all pipeline runs?</span>
            <button
              className="ghost icon-button"
              style={{ color: "#ef4444", fontSize: 11, padding: "2px 8px" }}
              onClick={handleConfirm}
              disabled={clearing}
            >
              {clearing ? "Clearing…" : "Yes, clear"}
            </button>
            <button
              className="ghost icon-button"
              style={{ fontSize: 11, padding: "2px 8px" }}
              onClick={() => setConfirming(false)}
              disabled={clearing}
            >
              Cancel
            </button>
          </span>
        ) : (
          <button
            className="ghost icon-button"
            onClick={() => setConfirming(true)}
            title="Clear all pipeline data"
          >
            <Trash2 size={15} />
          </button>
        )}
        <button className="ghost icon-button" onClick={onHelp} title="Field guide">
          <HelpCircle size={15} />
        </button>
      </header>
      <div className="legend">
        <span className="lg">
          <span className="sdot h" /> high
        </span>
        <span className="lg">
          <span className="sdot m" /> medium
        </span>
        <span className="lg">
          <span className="sdot l" /> low
        </span>
        <span className="sep">|</span>
        <span className="lg">{"break lifecycle: DETECTED -> TRIAGED -> ACKNOWLEDGED / ESCALATED -> CLOSED"}</span>
        <span className="spacer" />
        <button className="linkbtn" onClick={onHelp}>
          What do these terms mean?
        </button>
      </div>
    </>
  );
}
