import { HelpCircle, RefreshCcw } from "lucide-react";

const WORDMARK = String.raw`     _   _    _   _ _   _ ____
    | | / \  | \ | | | | / ___|
 _  | |/ _ \ |  \| | | | \___ \
| |_| / ___ \| |\  | |_| |___) |
 \___/_/   \_\_| \_|\___/|____/`;

export function AppHeader({
  onHelp,
  onRefresh
}: {
  onHelp: () => void;
  onRefresh: () => void;
}) {
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
