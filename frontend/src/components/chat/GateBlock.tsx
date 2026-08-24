import { ArrowSquareOut, ShieldCheck } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import type { ApiRun, FinalReport, GateResult } from "../../api/types";
import { severityTone } from "../../lib/format";

export function GateBlock({
  gate,
  reports,
  run,
}: {
  gate: GateResult;
  reports: FinalReport[];
  run: ApiRun;
}) {
  const technicalFailure = run.status === "technical_failure" || gate.exit_code === 2;
  const top = [...reports]
    .filter((report) => report.status === "confirmed")
    .sort((left, right) => severityRank(right.finding.severity) - severityRank(left.finding.severity))
    .slice(0, 3);

  return (
    <article className={`analysis-summary gate-${technicalFailure ? "error" : gate.decision}`}>
      <div className="analysis-summary-head">
        <span className="summary-icon">
          <ShieldCheck size={19} weight="duotone" />
        </span>
        <div>
          <span>{technicalFailure ? "Analysis stopped" : "Analysis complete"}</span>
          <strong>{technicalFailure ? "TECHNICAL ERROR" : gate.decision.toUpperCase()}</strong>
        </div>
      </div>

      {!technicalFailure ? <div className="summary-counts" aria-label="Finding counts">
        <span><strong>{gate.confirmed}</strong> confirmed</span>
        <span><strong>{gate.rejected}</strong> rejected</span>
        <span><strong>{gate.inconclusive}</strong> inconclusive</span>
        {gate.policy_blocked > 0 ? (
          <span><strong>{gate.policy_blocked}</strong> policy blocked</span>
        ) : null}
      </div> : null}

      {top.length > 0 ? (
        <div className="top-findings">
          <span>Top findings</span>
          <ol>
            {top.map((report) => (
              <li key={report.finding_id}>
                <span className={`severity ${severityTone(report.finding.severity)}`}>
                  {report.finding.severity?.toUpperCase() ?? "UNKNOWN"}
                </span>
                <span>{report.finding.title}</span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      <Link className="summary-link" to={`/runs/${run.id}`}>
        Open full report
        <ArrowSquareOut size={15} />
      </Link>
    </article>
  );
}

function severityRank(severity: string | null | undefined): number {
  switch ((severity ?? "").toUpperCase()) {
    case "CRITICAL":
      return 4;
    case "HIGH":
      return 3;
    case "MEDIUM":
      return 2;
    case "LOW":
      return 1;
    default:
      return 0;
  }
}
