import { ExternalLink, X } from "lucide-react";

import { Modal } from "./Modal";

export function DiffModal({
  runId,
  onBack,
  onClose
}: {
  runId: string;
  onBack?: () => void;
  onClose: () => void;
}) {
  return (
    <Modal onClose={onClose} wide>
      <div className="mhead">
        {onBack ? (
          <button className="linkbtn" onClick={onBack}>
            back to run
          </button>
        ) : null}
        <h3 className="mono diff-title">stage diff | {runId}</h3>
        <a className="linkout" href={`/diff/${encodeURIComponent(runId)}`} target="_blank" rel="noreferrer">
          <ExternalLink size={14} /> open in tab
        </a>
        <button className="x" onClick={onClose} title="Close">
          <X size={18} />
        </button>
      </div>
      <iframe className="navframe" src={`/diff/${encodeURIComponent(runId)}`} title={`stage diff ${runId}`} />
    </Modal>
  );
}
