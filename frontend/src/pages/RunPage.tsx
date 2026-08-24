import { ArrowLeft, CircleNotch } from "@phosphor-icons/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import {
  getGate,
  getReports,
  getRun,
  getRunProgress,
  runEventsUrl,
} from "../api/client";
import type { ApiRun } from "../api/types";
import { EvidenceBlock } from "../components/chat/EvidenceBlock";
import { FindingBlock } from "../components/chat/FindingBlock";
import { GateBlock } from "../components/chat/GateBlock";
import { StageBlock } from "../components/chat/TimelineEvent";
import { ToolCall } from "../components/chat/ToolCall";
import { useSse } from "../hooks/useSse";
import { formatDateTime, formatDuration, runStatusLabel, shortId } from "../lib/format";

export function RunPage() {
  const { runId } = useParams();
  const queryClient = useQueryClient();
  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRun(runId!),
    enabled: Boolean(runId),
  });
  const run = runQuery.data ?? null;
  const active = run?.status === "queued" || run?.status === "running";

  useSse<ApiRun>(
    active ? runEventsUrl(runId!) : null,
    "run",
    (payload) => queryClient.setQueryData(["run", runId], payload),
    (payload) => {
      queryClient.setQueryData(["run", runId], payload);
      void queryClient.invalidateQueries({ queryKey: ["reports", runId] });
      void queryClient.invalidateQueries({ queryKey: ["gate", runId] });
    },
  );

  const progressQuery = useQuery({
    queryKey: ["progress", runId],
    queryFn: () => getRunProgress(runId!),
    enabled: Boolean(runId),
    refetchInterval: active ? 1_500 : false,
  });
  const reportsQuery = useQuery({
    queryKey: ["reports", runId],
    queryFn: () => getReports(runId!),
    enabled: Boolean(runId) && !active,
  });
  const gateQuery = useQuery({
    queryKey: ["gate", runId],
    queryFn: () => getGate(runId!),
    enabled: Boolean(runId) && !active,
  });

  if (!run) return <div className="workspace-state">Загрузка прогона…</div>;
  const reports = reportsQuery.data ?? [];

  return (
    <div className="full-report-page">
      <header className="full-report-header">
        <div>
          <Link to="/debug/runs" className="back-link"><ArrowLeft size={15} /> Прогоны и диагностика</Link>
          <h1>Анализ безопасности</h1>
          <p>{run.target_id} · <code>{shortId(run.id)}</code></p>
        </div>
        <div className="run-facts">
          <span>{runStatusLabel(run.status)}</span>
          <span>{formatDateTime(run.started_at ?? run.created_at)}</span>
          <span>{formatDuration(run.started_at, run.finished_at)}</span>
        </div>
      </header>

      <main className="full-report-content">
        {active ? (
          <section className="report-live">
            <div className="report-live-title"><CircleNotch size={17} className="spin" /> Анализ выполняется</div>
            {(progressQuery.data?.stages ?? []).map((stage, index) => (
              <StageBlock key={`${stage.stage}-${stage.status}-${index}`} stage={stage} />
            ))}
          </section>
        ) : null}

        {run.error ? (
          <div className="inline-notice technical">
            <strong>Техническая ошибка</strong>
            <span>{run.error.message}</span>
          </div>
        ) : null}

        {gateQuery.data ? <GateBlock gate={gateQuery.data} reports={reports} run={run} /> : null}

        <div className="full-report-findings">
          {reports.map((report) => (
            <section className="full-report-finding" key={report.finding_id}>
              <FindingBlock report={report} />
              {report.sandbox_actions.map((action) => (
                <ToolCall
                  key={action.action_id}
                  tool={{
                    actionId: action.action_id,
                    capability: action.capability,
                    purpose: action.purpose,
                    target: action.target ?? null,
                    environment: action.environment ?? null,
                    status: action.execution_status,
                    exitCode: action.exit_code,
                    durationMs: action.duration_ms,
                    timedOut: action.timed_out,
                    parameterNames: action.parameter_names,
                    command: action.command,
                    cwd: action.cwd,
                    stdout: action.stdout,
                    stderr: action.stderr,
                    sandboxSessionId: action.sandbox_session_id,
                  }}
                />
              ))}
              {report.evidence.map((evidence) => (
                <EvidenceBlock key={evidence.id} evidence={evidence} />
              ))}
            </section>
          ))}
        </div>
      </main>
    </div>
  );
}
