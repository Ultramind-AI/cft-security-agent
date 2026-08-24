import {
  CaretRight,
  CheckCircle,
  Question,
  ShieldWarning,
  XCircle,
} from "@phosphor-icons/react";
import type { FinalReport } from "../../api/types";
import {
  findingStatusLabel,
  findingTitle,
  severityLabel,
  severityTone,
} from "../../lib/format";

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
          <strong>{findingTitle(report.finding.rule_id, report.finding.title)}</strong>
          <small>
            Доказательств: {report.evidence.length}
            {location ? ` · ${location}` : ""}
          </small>
        </span>
        <span className={`severity ${severityTone(report.finding.severity)}`}>
          {severityLabel(report.finding.severity)}
        </span>
      </summary>

      <div className="finding-details">
        {report.analysis_summary ? <p>{report.analysis_summary}</p> : null}
        {report.hypothesis ? (
          <section>
            <span>Гипотеза</span>
            <p>{report.hypothesis}</p>
          </section>
        ) : null}
        <section>
          <span>Вывод на основе доказательств</span>
          <p>{report.explanation}</p>
        </section>
        {report.limitations.length > 0 ? (
          <section>
              <span>Ограничения</span>
            <ul>
              {report.limitations.map((limitation) => (
                <li key={limitation}>{limitation}</li>
              ))}
            </ul>
          </section>
        ) : null}
        <section>
          <span>Следующий шаг</span>
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
