import type { Evidence } from "../types";
import { StatusPill } from "./StatusPill";

export function EvidenceCard({ evidence }: { evidence: Evidence }) {
  return (
    <article className="evidence-card">
      <div className="eyebrow-row">
        <span className="eyebrow evidence-label">Evidence / fact</span>
        <StatusPill value={evidence.verdict || evidence.reliability} />
      </div>
      <h4>{evidence.type}</h4>
      <p>{evidence.summary}</p>
      <div className="meta-grid compact-meta">
        <span>source</span>
        <strong>{evidence.source}</strong>
        <span>service</span>
        <strong>{evidence.scope.service || "—"}</strong>
        <span>scope</span>
        <strong>{evidence.scope.description}</strong>
      </div>
      <details>
        <summary>Observed facts</summary>
        <pre>{JSON.stringify(evidence.observation.facts, null, 2)}</pre>
      </details>
    </article>
  );
}
