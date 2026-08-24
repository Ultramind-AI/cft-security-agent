import { Link } from "react-router-dom";

import type { ChatSnapshot, FinalReport } from "../types";
import { EvidenceCard } from "./EvidenceCard";
import { StatusPill } from "./StatusPill";

function reportKey(report: FinalReport) {
  return `${report.finding_id}-${report.status}`;
}

export function ChatAnalysisPanel({ snapshot }: { snapshot: ChatSnapshot }) {
  const { run, reports, gate } = snapshot;
  if (!run) return null;

  return (
    <div className="chat-analysis">
      <div className="chat-analysis-head">
        <div>
          <span className="eyebrow">analysis run</span>
          <strong>{run.id}</strong>
        </div>
        <div className="chat-analysis-status">
          <StatusPill value={run.status} />
          {gate && <StatusPill value={gate.decision} />}
          <Link to={`/runs/${encodeURIComponent(run.id)}`}>details</Link>
        </div>
      </div>

      {run.analysis_request && (
        <div className="chat-request-focus">
          <span>request</span>
          <p>{run.analysis_request}</p>
        </div>
      )}

      {run.status === "technical_failure" && run.error && (
        <div className="chat-error-box">{run.error.message}</div>
      )}

      {reports.length === 0 && run.status === "running" && (
        <div className="chat-progress-line">
          <span className="pulse-dot" />
          Discovery / sandbox / SAST выполняются. Первый отчёт появится здесь автоматически.
        </div>
      )}

      {reports.map((report) => (
        <article className="chat-report" key={reportKey(report)}>
          <div className="chat-report-title">
            <div>
              <span className="eyebrow">finding</span>
              <h3>{report.finding.title}</h3>
            </div>
            <StatusPill value={report.status} />
          </div>
          <div className="chat-report-meta">
            <code>
              {report.finding.file}
              {report.finding.line_start ? `:${report.finding.line_start}` : ""}
            </code>
            <span>{report.finding.severity || "severity N/A"}</span>
            <span>{report.finding.rule_id}</span>
          </div>

          {report.hypothesis && (
            <div className="chat-reason-block">
              <strong>Hypothesis</strong>
              <p>{report.hypothesis}</p>
            </div>
          )}

          {report.agent_decisions.map((decision) => (
            <div className="chat-step" key={`${report.finding_id}-decision-${decision.step}`}>
              <div className="chat-step-label">reason · step {decision.step}</div>
              <p>{decision.reason}</p>
              {decision.stop_reason && <small>stop: {decision.stop_reason}</small>}
            </div>
          ))}

          {report.sandbox_actions.map((action) => (
            <div className="chat-step" key={action.action_id}>
              <div className="chat-step-label">sandbox action</div>
              <strong>{action.capability}</strong>
              <p>{action.purpose}</p>
              <small>
                {action.execution_status || "planned"}
                {action.exit_code != null ? ` · exit ${action.exit_code}` : ""}
              </small>
            </div>
          ))}

          {report.evidence.map((evidence) => (
            <EvidenceCard key={evidence.id} evidence={evidence} />
          ))}

          <div className="chat-conclusion">
            <strong>Conclusion</strong>
            <p>{report.explanation}</p>
            <small>{report.next_step}</small>
          </div>
        </article>
      ))}

      {gate && (
        <div className="chat-gate">
          <div>
            <span className="eyebrow">deterministic gate</span>
            <h3>{gate.decision.toUpperCase()}</h3>
          </div>
          <p>{gate.reasons.join(" ") || gate.decision_basis}</p>
        </div>
      )}
    </div>
  );
}
