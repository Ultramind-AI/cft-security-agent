import {
  CaretRight,
  CheckCircle,
  Question,
  ShieldWarning,
  XCircle,
} from "@phosphor-icons/react";
import type { FinalReport } from "../../api/types";
import { findingStatusLabel, severityTone } from "../../lib/format";

export function FindingBlock({ report }: { report: FinalReport }) {
  const location = [
    report.finding.file,
    report.finding.line_start ? String(report.finding.line_start) : null,
  ]
    .filter(Boolean)
    .join(":");

  return (
    <details className={`finding-block ${report.status}`}>
      <summary>
        <CaretRight size={14} className="details-caret" />
        <span className="finding-status-icon">{statusIcon(report.status)}</span>
        <span className="finding-main">
          <span className="finding-kicker">{findingStatusLabel(report.status)}</span>
          <strong>{report.finding.title}</strong>
          <small>
            {report.evidence.length} evidence item{report.evidence.length === 1 ? "" : "s"}
            {location ? ` · ${location}` : ""}
          </small>
        </span>
        <span className={`severity ${severityTone(report.finding.severity)}`}>
          {report.finding.severity?.toUpperCase() ?? "UNKNOWN"}
        </span>
      </summary>

      <div className="finding-details">
        {report.analysis_summary ? <p>{report.analysis_summary}</p> : null}
        {report.hypothesis ? (
          <section>
            <span>Hypothesis</span>
            <p>{report.hypothesis}</p>
          </section>
        ) : null}
        <section>
          <span>Evidence-based conclusion</span>
          <p>{report.explanation}</p>
        </section>
        {report.limitations.length > 0 ? (
          <section>
            <span>Limitations</span>
            <ul>
              {report.limitations.map((limitation) => (
                <li key={limitation}>{limitation}</li>
              ))}
            </ul>
          </section>
        ) : null}
        <section>
          <span>Next step</span>
          <p>{report.next_step}</p>
        </section>
      </div>
    </details>
  );
}

function statusIcon(status: FinalReport["status"]) {
  switch (status) {
    case "confirmed":
      return <ShieldWarning size={18} weight="duotone" />;
    case "rejected":
      return <CheckCircle size={18} weight="duotone" />;
    case "policy_blocked":
      return <XCircle size={18} weight="duotone" />;
    default:
      return <Question size={18} weight="duotone" />;
  }
}
