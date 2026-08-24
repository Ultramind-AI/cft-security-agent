import { ArrowSquareOut, ShieldCheck } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import type { ApiRun, FinalReport, GateResult } from "../../api/types";
import { findingTitle, gateLabel, severityLabel, severityTone } from "../../lib/format";

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
          <span>{technicalFailure ? "Анализ остановлен" : "Анализ завершён"}</span>
          <strong>{technicalFailure ? "ТЕХНИЧЕСКАЯ ОШИБКА" : gateLabel(gate.decision)}</strong>
        </div>
      </div>

      {!technicalFailure ? <div className="summary-counts" aria-label="Finding counts">
        <span><strong>{gate.confirmed}</strong> подтверждено</span>
        <span><strong>{gate.rejected}</strong> опровергнуто</span>
        <span><strong>{gate.inconclusive}</strong> недостаточно данных</span>
        {gate.policy_blocked > 0 ? (
          <span><strong>{gate.policy_blocked}</strong> заблокировано политикой</span>
        ) : null}
      </div> : null}

      {top.length > 0 ? (
        <div className="top-findings">
          <span>Главные находки</span>
          <ol>
            {top.map((report) => (
              <li key={report.finding_id}>
                <span className={`severity ${severityTone(report.finding.severity)}`}>
                  {severityLabel(report.finding.severity)}
                </span>
                <span>{findingTitle(report.finding.rule_id, report.finding.title)}</span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      <Link className="summary-link" to={`/runs/${run.id}`}>
        Открыть полный отчёт
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
