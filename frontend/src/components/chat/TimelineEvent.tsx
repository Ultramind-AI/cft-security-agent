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
import { stageLabel } from "../../lib/format";

export function RunStartBlock({ run }: { run: ApiRun }) {
  return (
    <div className="timeline-row run-start-row">
      <GitBranch size={15} />
      <span>Analysis run started</span>
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
      <small>{stage.detail ?? stage.status}</small>
    </div>
  );
}

export function DiscoveryBlock({ discovery }: { discovery: RunDiscoveryView }) {
  const stack = discovery.technologies.join(" · ") || "Technology stack unavailable";
  return (
    <details className="timeline-disclosure discovery-block">
      <summary>
        <CaretRight size={14} className="details-caret" />
        <Compass size={16} weight="duotone" />
        <span>Discovery completed</span>
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
        {event.status === "started" ? "Investigating finding" : "Finding verification finished"}
      </span>
      <small>{event.title ?? event.result ?? event.finding_id}</small>
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
        <span>Agent decision</span>
        <small>{report.finding.title}</small>
      </div>
      <p>{decision.reason}</p>
      {decision.outcome === "stop" && decision.stop_reason ? (
        <code>{decision.stop_reason.replaceAll("_", " ")}</code>
      ) : null}
    </article>
  );
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
        <span>Technical failure</span>
        <strong>{run.error?.message ?? "The analysis pipeline stopped unexpectedly."}</strong>
        <small>{run.error ? `${run.error.layer} · ${run.error.code}` : "pipeline"}</small>
      </div>
      <button type="button" onClick={onRetry}>
        <ArrowClockwise size={15} />
        Retry
      </button>
    </article>
  );
}
