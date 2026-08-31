import {
  ArrowClockwise,
  CaretRight,
  CheckCircle,
  CircleNotch,
  Compass,
  GitBranch,
  ListChecks,
  WarningCircle,
} from "@phosphor-icons/react";
import type {
  AgentDecisionRecord,
  ApiRun,
  FinalReport,
  RunDiscoveryView,
  RunFindingProgressEvent,
  RunStageEvent,
} from "../../api/types";
import {
  findingStatusLabel,
  findingTitle,
  stageDetailLabel,
  stageLabel,
} from "../../lib/format";

export function RunStartBlock({ run }: { run: ApiRun }) {
  return (
    <div className="timeline-row run-start-row">
      <GitBranch size={15} />
      <span>Анализ запущен</span>
      {run.analysis_request ? <small>{run.analysis_request}</small> : null}
    </div>
  );
}

export function StageBlock({ stage }: { stage: RunStageEvent }) {
  const running = stage.status === "running";
  const failed = stage.status === "failed";
  return (
    <div className={`timeline-row stage-row ${stage.status}`}>
      {running ? (
        <CircleNotch size={15} className="spin" />
      ) : failed ? (
        <WarningCircle size={15} />
      ) : (
        <CheckCircle size={15} />
      )}
      <span>{stageLabel(stage.stage)}</span>
      <small>{stageDetailLabel(stage.detail, stage.status)}</small>
    </div>
  );
}

export function DiscoveryBlock({ discovery }: { discovery: RunDiscoveryView }) {
  const stack = discovery.technologies.join(" · ") || "Стек технологий не определен";
  return (
    <details className="timeline-disclosure discovery-block">
      <summary>
        <CaretRight size={14} className="details-caret" />
        <Compass size={16} weight="duotone" />
        <span>Исследование завершено</span>
        <strong>{stack}</strong>
      </summary>
      <div className="discovery-details">
        {discovery.components.map((component) => (
          <div key={component.id}>
            <strong>{component.id}</strong>
            <span>
              {[...component.frameworks, ...component.technologies].join(" · ") || component.root}
            </span>
          </div>
        ))}
        {discovery.warnings.length > 0 ? (
          <ul>
            {discovery.warnings.map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        ) : null}
      </div>
    </details>
  );
}

export function FindingProgressBlock({ event }: { event: RunFindingProgressEvent }) {
  return (
    <div className={`timeline-row finding-progress-row ${event.status}`}>
      {event.status === "started" ? (
        <CircleNotch size={15} className="spin" />
      ) : (
        <ListChecks size={15} />
      )}
      <span>
        {event.status === "started" ? "Исследование находки" : "Проверка находки завершена"}
      </span>
      <small>
        {event.status === "finished" && event.result
          ? findingResultLabel(event.result)
          : event.rule_id
            ? findingTitle(event.rule_id, event.title ?? event.finding_id)
            : event.title ?? event.finding_id}
      </small>
    </div>
  );
}

export function AgentDecisionBlock({
  decision,
  report,
}: {
  decision: AgentDecisionRecord;
  report: FinalReport;
}) {
  return (
    <article className="agent-decision">
      <div className="message-label">
        <span>Решение агента</span>
        <small>{findingTitle(report.finding.rule_id, report.finding.title)}</small>
      </div>
      <p>{decisionReason(decision.reason)}</p>
      {decision.outcome === "stop" && decision.stop_reason ? (
        <code>{stopReason(decision.stop_reason)}</code>
      ) : null}
    </article>
  );
}

function decisionReason(reason: string): string {
  const verdict = reason.match(
    /^Capability-specific structured Evidence established the finding verdict: (confirmed|rejected)\.$/,
  );
  if (verdict) {
    return verdict[1] === "confirmed"
      ? "Структурированные доказательства подтвердили находку."
      : "Структурированные доказательства опровергли находку.";
  }
  return reason;
}

function stopReason(reason: string): string {
  const labels: Record<string, string> = {
    terminal_evidence: "достаточно доказательств",
    policy_blocked: "заблокировано политикой",
    plan_rejected: "план отклонен",
    iteration_limit: "достигнут предел итераций",
  };
  return labels[reason] ?? reason.replaceAll("_", " ");
}

function findingResultLabel(result: string): string {
  if (["confirmed", "rejected", "inconclusive", "policy_blocked"].includes(result)) {
    return findingStatusLabel(result as FinalReport["status"]);
  }
  return result === "error" ? "Ошибка" : result;
}

export function TechnicalErrorBlock({
  run,
  onRetry,
}: {
  run: ApiRun;
  onRetry: () => void;
}) {
  return (
    <article className="inline-error-block technical">
      <WarningCircle size={18} weight="duotone" />
      <div>
        <span>Техническая ошибка</span>
        <strong>{run.error?.message ?? "Пайплайн анализа неожиданно остановился."}</strong>
        <small>{run.error ? `${run.error.layer} · ${run.error.code}` : "pipeline"}</small>
      </div>
      <button type="button" onClick={onRetry}>
        <ArrowClockwise size={15} />
        Повторить
      </button>
    </article>
  );
}
