import type { Evidence, FindingTimeline, FinalReport } from "../types";
import { EvidenceCard } from "./EvidenceCard";
import { StatusPill } from "./StatusPill";

export function AgentTimeline({
  timeline,
  report,
  evidence,
}: {
  timeline?: FindingTimeline;
  report?: FinalReport;
  evidence: Evidence[];
}) {
  if (!timeline && !report) {
    return <div className="empty-state">Timeline will appear after the run produces a report.</div>;
  }

  const actions = timeline?.sandbox_actions ?? report?.sandbox_actions ?? [];
  const decisions = timeline?.agent_decisions ?? report?.agent_decisions ?? [];
  const stepCount = Math.max(actions.length, decisions.length);

  return (
    <div className="timeline-stack">
      {(report?.analysis_summary || report?.hypothesis) && (
        <article className="reason-card">
          <div className="eyebrow-row">
            <span className="eyebrow reason-label">Agent reasoning</span>
            {report?.hypothesis_confidence != null && (
              <span className="confidence">{Math.round(report.hypothesis_confidence * 100)}% confidence</span>
            )}
          </div>
          {report?.analysis_summary && <p>{report.analysis_summary}</p>}
          {report?.hypothesis && (
            <p className="hypothesis">
              <strong>Hypothesis:</strong> {report.hypothesis}
            </p>
          )}
        </article>
      )}

      {Array.from({ length: stepCount }, (_, index) => {
        const action = actions[index];
        const decision = decisions[index];
        const actionEvidence = action
          ? evidence.filter((item) => item.action_id === action.action_id)
          : [];
        const step = decision?.step || index + 1;

        return (
          <section className="timeline-step" key={`${step}-${action?.action_id || "decision"}`}>
            <div className="timeline-rail">
              <span>{step}</span>
            </div>
            <div className="timeline-content">
              {action && (
                <article className="action-card">
                  <div className="eyebrow-row">
                    <span className="eyebrow action-label">Sandbox action</span>
                    <StatusPill value={action.execution_status || "planned"} />
                  </div>
                  <h4>{action.capability}</h4>
                  <p>{action.purpose}</p>
                  <div className="action-meta">
                    <span>{action.target}</span>
                    <span>{action.environment}</span>
                    {action.exit_code != null && <span>exit {action.exit_code}</span>}
                  </div>
                </article>
              )}

              {actionEvidence.map((item) => (
                <EvidenceCard key={item.id} evidence={item} />
              ))}

              {decision && (
                <article className="reason-card">
                  <div className="eyebrow-row">
                    <span className="eyebrow reason-label">Agent decision</span>
                    <StatusPill value={decision.outcome} />
                  </div>
                  <p>{decision.reason}</p>
                  {decision.stop_reason && <div className="stop-reason">stop: {decision.stop_reason}</div>}
                </article>
              )}
            </div>
          </section>
        );
      })}

      {stepCount === 0 && evidence.map((item) => <EvidenceCard key={item.id} evidence={item} />)}
    </div>
  );
}
