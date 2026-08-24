import { CaretRight, Fingerprint } from "@phosphor-icons/react";
import type { Evidence } from "../../api/types";
import { toolLabel } from "../../lib/format";

export function EvidenceBlock({ evidence }: { evidence: Evidence }) {
  return (
    <details className={`evidence-block ${evidence.source}`}>
      <summary>
        <CaretRight size={14} className="details-caret" />
        <Fingerprint size={17} weight="duotone" />
        <span>Evidence</span>
        <strong>{evidence.summary}</strong>
        <span className={`reliability ${evidence.reliability}`}>
          {evidence.reliability}
        </span>
      </summary>

      <div className="evidence-details">
        <dl className="evidence-grid">
          <div>
            <dt>Source</dt>
            <dd>{evidence.source}</dd>
          </div>
          <div>
            <dt>Observation</dt>
            <dd>{evidence.observation.kind}</dd>
          </div>
          <div>
            <dt>Reliability</dt>
            <dd>{evidence.reliability}</dd>
          </div>
          <div>
            <dt>Action</dt>
            <dd>{toolLabel(evidence.action.tool)}</dd>
          </div>
          <div>
            <dt>Scope</dt>
            <dd>{evidence.scope.description}</dd>
          </div>
          {evidence.scope.service ? (
            <div>
              <dt>Service</dt>
              <dd>{evidence.scope.service}</dd>
            </div>
          ) : null}
        </dl>
        <div className="raw-evidence">
          <span>Raw observation</span>
          <pre>{JSON.stringify(evidence.observation.facts, null, 2)}</pre>
        </div>
      </div>
    </details>
  );
}
