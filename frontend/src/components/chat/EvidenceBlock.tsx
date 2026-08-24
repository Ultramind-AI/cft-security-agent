import { CaretRight, Fingerprint } from "@phosphor-icons/react";
import type { Evidence } from "../../api/types";
import {
  evidenceSourceLabel,
  evidenceSummary,
  reliabilityLabel,
  toolLabel,
} from "../../lib/format";

export function EvidenceBlock({ evidence }: { evidence: Evidence }) {
  return (
    <details className={`evidence-block ${evidence.source}`}>
      <summary>
        <CaretRight size={14} className="details-caret" />
        <Fingerprint size={17} weight="duotone" />
        <span>Доказательство</span>
        <strong>{evidenceSummary(evidence.summary)}</strong>
        <span className={`reliability ${evidence.reliability}`}>
          {reliabilityLabel(evidence.reliability)}
        </span>
      </summary>

      <div className="evidence-details">
        <dl className="evidence-grid">
          <div>
            <dt>Источник</dt>
            <dd>{evidenceSourceLabel(evidence.source)}</dd>
          </div>
          <div>
            <dt>Наблюдение</dt>
            <dd>{evidence.observation.kind}</dd>
          </div>
          <div>
            <dt>Надёжность</dt>
            <dd>{reliabilityLabel(evidence.reliability)}</dd>
          </div>
          <div>
            <dt>Действие</dt>
            <dd>{toolLabel(evidence.action.tool)}</dd>
          </div>
          <div>
            <dt>Область проверки</dt>
            <dd>{evidence.scope.description}</dd>
          </div>
          {evidence.scope.service ? (
            <div>
              <dt>Сервис</dt>
              <dd>{evidence.scope.service}</dd>
            </div>
          ) : null}
        </dl>
        <div className="raw-evidence">
          <span>Исходные данные наблюдения</span>
          <pre>{JSON.stringify(evidence.observation.facts, null, 2)}</pre>
        </div>
      </div>
    </details>
  );
}
