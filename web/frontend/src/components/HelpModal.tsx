import { X } from "lucide-react";

import { SeverityPill } from "./Badges";
import { Modal } from "./Modal";

export function HelpModal({ onClose }: { onClose: () => void }) {
  return (
    <Modal onClose={onClose}>
      <div className="mhead">
        <h3>Field guide</h3>
        <button className="x" onClick={onClose} title="Close">
          <X size={18} />
        </button>
      </div>
      <div className="msub">What the numbers and labels on this dashboard mean.</div>
      <div className="block">
        <div className="bt">Concepts</div>
        <dl className="glos">
          <dt>Run</dt>
          <dd>One pipeline execution over an instrument's data. Click a run row to see its full lineage.</dd>
          <dt>Stage change</dt>
          <dd>{"A value the pipeline altered as data flowed ingestion -> adapter -> validators."}</dd>
          <dt>Unattributed</dt>
          <dd>A change with no recorded reason. These are the ones to investigate first.</dd>
          <dt>Break</dt>
          <dd>A flagged data anomaly with a signed lifecycle.</dd>
          <dt>Adj. warning</dt>
          <dd>A retroactive price adjustment the pipeline blocked for point-in-time safety.</dd>
          <dt>Tagged outlier</dt>
          <dd>An extreme daily return the validators flagged for reviewer follow-up.</dd>
          <dt>Raw source</dt>
          <dd>The provider fields behind one symbol/date row.</dd>
        </dl>
      </div>
      <div className="block">
        <div className="bt">Severity</div>
        <div className="action-row">
          <SeverityPill severity="high" />
          <SeverityPill severity="medium" />
          <SeverityPill severity="low" />
        </div>
      </div>
      <div className="block">
        <div className="bt">Break lifecycle</div>
        <div className="msub compact">
          {"DETECTED -> TRIAGED -> (AUTO_RESOLVED, ACKNOWLEDGED, ESCALATED) -> CLOSED. Each transition is signed by an actor and chained by hash."}
        </div>
      </div>
    </Modal>
  );
}
