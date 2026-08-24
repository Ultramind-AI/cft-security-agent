import type {
  AgentDecisionRecord,
  ApiRun,
  ChatMessage,
  ChatRunSnapshot,
  ChatSnapshot,
  Evidence,
  FinalReport,
  GateResult,
  RunActivityEvent,
  RunDiscoveryView,
  RunFindingProgressEvent,
  RunStageEvent,
  SandboxActionSummary,
} from "../api/types";

export interface TimelineBase {
  id: string;
  runId: string | null;
  at: string;
  sortTime: number;
  sortRank: number;
}

export type ConversationTimelineItem =
  | (TimelineBase & { kind: "message"; message: ChatMessage })
  | (TimelineBase & { kind: "run_start"; run: ApiRun })
  | (TimelineBase & { kind: "stage"; stage: RunStageEvent })
  | (TimelineBase & {
      kind: "discovery";
      discovery: RunDiscoveryView;
    })
  | (TimelineBase & {
      kind: "finding_progress";
      event: RunFindingProgressEvent;
    })
  | (TimelineBase & {
      kind: "decision";
      decision: AgentDecisionRecord;
      report: FinalReport;
    })
  | (TimelineBase & { kind: "tool"; tool: TimelineTool })
  | (TimelineBase & {
      kind: "evidence";
      evidence: Evidence;
      report: FinalReport;
    })
  | (TimelineBase & { kind: "finding"; report: FinalReport })
  | (TimelineBase & {
      kind: "gate";
      gate: GateResult;
      reports: FinalReport[];
      run: ApiRun;
    })
  | (TimelineBase & { kind: "technical_error"; run: ApiRun });

export interface TimelineTool {
  actionId: string;
  capability: string;
  purpose: string | null;
  target: string | null;
  environment: string | null;
  status: string | null;
  exitCode: number | null;
  durationMs: number | null;
  timedOut: boolean;
  parameterNames: string[];
  command: string[];
  cwd: string | null;
  stdout: string | null;
  stderr: string | null;
  sandboxSessionId: string | null;
}

const KIND_RANK = {
  run_start: 10,
  message: 20,
  stage: 30,
  discovery: 35,
  finding_progress: 40,
  decision: 50,
  tool: 60,
  evidence: 70,
  finding: 80,
  technical_error: 90,
  gate: 95,
} as const;

export function buildConversationTimeline(
  snapshot: ChatSnapshot,
): ConversationTimelineItem[] {
  const snapshots = runSnapshots(snapshot);
  const technicalRunIds = new Set(
    snapshots
      .filter(({ run }) => run.status === "technical_failure")
      .map(({ run }) => run.id),
  );
  const messages = snapshot.messages.filter(
    (message) =>
      message.kind !== "summary" ||
      message.run_id === null ||
      !technicalRunIds.has(message.run_id),
  );
  const items: ConversationTimelineItem[] = messages.map((message) => ({
    kind: "message",
    id: `message:${message.id}`,
    runId: message.run_id,
    at: message.created_at,
    sortTime: timeOf(message.created_at, 0),
    sortRank: KIND_RANK.message,
    message,
  }));

  for (const runSnapshot of snapshots) {
    appendRun(items, runSnapshot);
  }

  return items.sort((left, right) => {
    if (left.sortTime !== right.sortTime) return left.sortTime - right.sortTime;
    if (left.sortRank !== right.sortRank) return left.sortRank - right.sortRank;
    return left.id.localeCompare(right.id);
  });
}

function runSnapshots(snapshot: ChatSnapshot): ChatRunSnapshot[] {
  if (snapshot.runs.length > 0) return snapshot.runs;
  if (!snapshot.run) return [];
  return [
    {
      run: snapshot.run,
      reports: snapshot.reports,
      gate: snapshot.gate,
      progress: snapshot.progress,
      discovery: snapshot.discovery,
    },
  ];
}

function appendRun(
  items: ConversationTimelineItem[],
  snapshot: ChatRunSnapshot,
): void {
  const { run, progress, discovery, reports, gate } = snapshot;
  const startedAt = run.started_at ?? run.created_at;
  const startedMs = timeOf(startedAt, timeOf(run.created_at, 0));

  items.push({
    kind: "run_start",
    id: `run:${run.id}:start`,
    runId: run.id,
    at: startedAt,
    sortTime: startedMs,
    sortRank: KIND_RANK.run_start,
    run,
  });

  for (const [index, stage] of (progress?.stages ?? []).entries()) {
    const at = stage.at ?? startedAt;
    items.push({
      kind: "stage",
      id: `run:${run.id}:stage:${index}:${stage.stage}:${stage.status}`,
      runId: run.id,
      at,
      sortTime: timeOf(stage.at, startedMs + index + 1),
      sortRank: KIND_RANK.stage,
      stage,
    });
  }

  if (discovery) {
    const discoveryStage = [...(progress?.stages ?? [])]
      .reverse()
      .find((stage) => stage.stage === "discovery");
    const at = discoveryStage?.at ?? startedAt;
    items.push({
      kind: "discovery",
      id: `run:${run.id}:discovery`,
      runId: run.id,
      at,
      sortTime: timeOf(discoveryStage?.at, startedMs + 2),
      sortRank: KIND_RANK.discovery,
      discovery,
    });
  }

  for (const [index, event] of (progress?.finding_events ?? []).entries()) {
    const at = event.at ?? startedAt;
    items.push({
      kind: "finding_progress",
      id: `run:${run.id}:finding-progress:${event.finding_id}:${event.status}:${index}`,
      runId: run.id,
      at,
      sortTime: timeOf(event.at, startedMs + 10 + index),
      sortRank: KIND_RANK.finding_progress,
      event,
    });
  }

  appendReportEvents(items, snapshot, startedMs, startedAt);

  if (run.status === "technical_failure") {
    const at = run.finished_at ?? startedAt;
    items.push({
      kind: "technical_error",
      id: `run:${run.id}:technical-error`,
      runId: run.id,
      at,
      sortTime: timeOf(run.finished_at, startedMs + 90_000),
      sortRank: KIND_RANK.technical_error,
      run,
    });
  }

  if (gate && run.status !== "technical_failure") {
    const at = run.finished_at ?? startedAt;
    items.push({
      kind: "gate",
      id: `run:${run.id}:gate`,
      runId: run.id,
      at,
      sortTime: timeOf(run.finished_at, startedMs + 100_000),
      sortRank: KIND_RANK.gate,
      gate,
      reports,
      run,
    });
  }
}

function appendReportEvents(
  items: ConversationTimelineItem[],
  snapshot: ChatRunSnapshot,
  startedMs: number,
  startedAt: string,
): void {
  const { run, reports, progress } = snapshot;
  const activities = new Map(
    (progress?.activities ?? []).map((activity) => [activity.action_id, activity]),
  );
  const summaries = new Map<string, SandboxActionSummary>();

  for (const report of reports) {
    for (const action of report.sandbox_actions) summaries.set(action.action_id, action);

    for (const decision of report.agent_decisions) {
      const at = decision.recorded_at ?? startedAt;
      items.push({
        kind: "decision",
        id: `run:${run.id}:decision:${report.finding_id}:${decision.step}:${at}`,
        runId: run.id,
        at,
        sortTime: timeOf(decision.recorded_at, startedMs + decision.step * 10 + 20),
        sortRank: KIND_RANK.decision,
        decision,
        report,
      });
    }

    for (const evidence of report.evidence) {
      items.push({
        kind: "evidence",
        id: `run:${run.id}:evidence:${evidence.id}`,
        runId: run.id,
        at: evidence.created_at,
        sortTime: timeOf(evidence.created_at, startedMs + 60_000),
        sortRank: KIND_RANK.evidence,
        evidence,
        report,
      });
    }

    const completedAt = reportCompletionTime(report, activities, run, startedMs);
    items.push({
      kind: "finding",
      id: `run:${run.id}:finding:${report.finding_id}`,
      runId: run.id,
      at: new Date(completedAt).toISOString(),
      sortTime: completedAt + 1,
      sortRank: KIND_RANK.finding,
      report,
    });
  }

  const actionIds = new Set([...activities.keys(), ...summaries.keys()]);
  for (const [index, actionId] of [...actionIds].entries()) {
    const activity = activities.get(actionId);
    const summary = summaries.get(actionId);
    const evidenceAt = reports
      .flatMap((report) => report.evidence)
      .find((evidence) => evidence.action_id === actionId)?.created_at;
    const at = activity?.at ?? evidenceAt ?? startedAt;
    items.push({
      kind: "tool",
      id: `run:${run.id}:tool:${actionId}`,
      runId: run.id,
      at,
      sortTime: timeOf(activity?.at ?? evidenceAt, startedMs + 30_000 + index),
      sortRank: KIND_RANK.tool,
      tool: toolView(actionId, summary, activity),
    });
  }
}

function toolView(
  actionId: string,
  summary: SandboxActionSummary | undefined,
  activity: RunActivityEvent | undefined,
): TimelineTool {
  return {
    actionId,
    capability: summary?.capability ?? activity?.tool ?? "sandbox action",
    purpose: summary?.purpose ?? null,
    target: summary?.target ?? activity?.target ?? null,
    environment: summary?.environment ?? null,
    status: summary?.execution_status ?? activity?.status ?? null,
    exitCode: summary?.exit_code ?? activity?.exit_code ?? null,
    durationMs: summary?.duration_ms ?? activity?.duration_ms ?? null,
    timedOut: summary?.timed_out ?? false,
    parameterNames: summary?.parameter_names ?? [],
    command: summary?.command ?? [],
    cwd: summary?.cwd ?? null,
    stdout: summary?.stdout ?? null,
    stderr: summary?.stderr ?? null,
    sandboxSessionId: summary?.sandbox_session_id ?? null,
  };
}

function reportCompletionTime(
  report: FinalReport,
  activities: Map<string, RunActivityEvent>,
  run: ApiRun,
  startedMs: number,
): number {
  const timestamps = [
    ...report.evidence.map((item) => timeOf(item.created_at, 0)),
    ...report.agent_decisions.map((item) => timeOf(item.recorded_at, 0)),
    ...report.sandbox_actions.map((item) =>
      timeOf(activities.get(item.action_id)?.at, 0),
    ),
  ].filter((value) => value > 0);
  if (timestamps.length > 0) return Math.max(...timestamps);
  return timeOf(run.finished_at, startedMs + 80_000);
}

function timeOf(value: string | null | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}
