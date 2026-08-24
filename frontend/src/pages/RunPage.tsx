import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, runEventsUrl } from "../api";
import { AgentTimeline } from "../components/AgentTimeline";
import { EvidenceCard } from "../components/EvidenceCard";
import { StatusPill } from "../components/StatusPill";
import type { ApiRun } from "../types";

function terminal(run?: ApiRun) {
  return run ? !["queued", "running"].includes(run.status) : false;
}

function formatDate(value: string | null) {
  return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(value)) : "—";
}

function duration(run?: ApiRun) {
  if (!run?.started_at) return "—";
  const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - new Date(run.started_at).getTime()) / 1000));
  return `${seconds}s`;
}

export function RunPage() {
  const { runId = "" } = useParams();
  const queryClient = useQueryClient();
  const [selectedFinding, setSelectedFinding] = useState<string | null>(null);

  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api.getRun(runId), enabled: Boolean(runId) });
  const isTerminal = terminal(run.data);
  const findings = useQuery({
    queryKey: ["findings", runId],
    queryFn: () => api.listFindings(runId),
    enabled: isTerminal,
  });
  const evidence = useQuery({
    queryKey: ["evidence", runId],
    queryFn: () => api.listEvidence(runId),
    enabled: isTerminal,
  });
  const timeline = useQuery({
    queryKey: ["timeline", runId],
    queryFn: () => api.getTimeline(runId),
    enabled: isTerminal && run.data?.status === "completed",
  });
  const gate = useQuery({
    queryKey: ["gate", runId],
    queryFn: () => api.getGate(runId),
    enabled: isTerminal,
    retry: false,
  });

  const liveStatus = run.data?.status;

  useEffect(() => {
    if (!liveStatus || !["queued", "running"].includes(liveStatus)) return;

    const source = new EventSource(runEventsUrl(runId));
    source.addEventListener("run", (event) => {
      const snapshot = JSON.parse((event as MessageEvent<string>).data) as ApiRun;
      queryClient.setQueryData(["run", runId], snapshot);
    });
    source.addEventListener("done", () => {
      source.close();
      void queryClient.invalidateQueries({ queryKey: ["run", runId] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["findings", runId] });
      void queryClient.invalidateQueries({ queryKey: ["evidence", runId] });
      void queryClient.invalidateQueries({ queryKey: ["timeline", runId] });
      void queryClient.invalidateQueries({ queryKey: ["gate", runId] });
    });
    return () => source.close();
  }, [liveStatus, queryClient, runId]);

  useEffect(() => {
    if (!selectedFinding && findings.data?.[0]) setSelectedFinding(findings.data[0].finding_id);
  }, [findings.data, selectedFinding]);

  const report = useQuery({
    queryKey: ["report", runId, selectedFinding],
    queryFn: () => api.getReport(runId, selectedFinding as string),
    enabled: Boolean(selectedFinding) && run.data?.status === "completed",
  });

  const selectedTimeline = useMemo(
    () => timeline.data?.findings.find((item) => item.finding_id === selectedFinding),
    [selectedFinding, timeline.data],
  );
  const selectedEvidence = useMemo(
    () => evidence.data?.filter((item) => item.finding_id === selectedFinding).map((item) => item.evidence) ?? [],
    [evidence.data, selectedFinding],
  );

  if (run.isLoading) return <main className="page-shell"><div className="empty-state">Loading run…</div></main>;
  if (run.error || !run.data) return <main className="page-shell"><div className="error-banner">Run not found or API unavailable.</div></main>;

  return (
    <main className="page-shell">
      <Link className="back-link" to="/">← All runs</Link>
      <section className="run-header">
        <div>
          <span className="kicker">Investigation</span>
          <h1>{run.data.target_id}</h1>
          <code>{run.data.id}</code>
        </div>
        <div className="run-status-cluster">
          <div><span>Status</span><StatusPill value={run.data.status} /></div>
          <div><span>Gate</span><StatusPill value={run.data.gate_decision} /></div>
        </div>
      </section>

      <section className="metric-grid">
        <article><span>Agent mode</span><strong>{run.data.agent_mode || "default"}</strong></article>
        <article><span>Max steps</span><strong>{run.data.max_iterations}</strong></article>
        <article><span>Started</span><strong>{formatDate(run.data.started_at)}</strong></article>
        <article><span>Duration</span><strong>{duration(run.data)}</strong></article>
      </section>

      {!isTerminal && (
        <section className="live-banner">
          <span className="pulse-dot" />
          <div>
            <strong>Live run</strong>
            <p>Status updates are arriving over Server-Sent Events. Findings and Evidence appear when the canonical pipeline persists the final report.</p>
          </div>
        </section>
      )}

      {run.data.error && (
        <section className="error-banner">
          <strong>{run.data.error.code}</strong> · {run.data.error.message}
        </section>
      )}

      {gate.data && (
        <section className="gate-card">
          <div>
            <span className="eyebrow">Deterministic CI gate</span>
            <h2>{gate.data.decision.toUpperCase()}</h2>
            <p>{gate.data.reasons[0] || gate.data.decision_basis}</p>
          </div>
          <div className="gate-metrics">
            <span><strong>{gate.data.confirmed}</strong> confirmed</span>
            <span><strong>{gate.data.rejected}</strong> rejected</span>
            <span><strong>{gate.data.inconclusive}</strong> inconclusive</span>
            <span><strong>{gate.data.technical_errors}</strong> technical</span>
          </div>
        </section>
      )}

      {isTerminal && (
        <section className="investigation-layout">
          <aside className="findings-panel">
            <div className="section-heading compact-heading">
              <div><span className="kicker">SAST + runtime</span><h2>Findings</h2></div>
              <span className="count-badge">{findings.data?.length ?? 0}</span>
            </div>
            {findings.data?.map((finding) => (
              <button
                className={`finding-row ${selectedFinding === finding.finding_id ? "selected" : ""}`}
                key={finding.finding_id}
                type="button"
                onClick={() => setSelectedFinding(finding.finding_id)}
              >
                <div><StatusPill value={finding.status} /><span className="severity">{finding.severity || "—"}</span></div>
                <strong>{finding.title}</strong>
                <span>{finding.file}:{finding.line_start ?? "?"}</span>
              </button>
            ))}
            {findings.data?.length === 0 && <div className="empty-state">No persisted findings for this run.</div>}
          </aside>

          <div className="detail-panel">
            {selectedFinding ? (
              <>
                <section className="detail-heading">
                  <div>
                    <span className="kicker">Finding detail</span>
                    <h2>{report.data?.finding.title || findings.data?.find((item) => item.finding_id === selectedFinding)?.title}</h2>
                  </div>
                  <StatusPill value={report.data?.status} />
                </section>

                <AgentTimeline timeline={selectedTimeline} report={report.data} evidence={selectedEvidence} />

                {selectedEvidence.length > 0 && (
                  <section className="section-block nested-section">
                    <div className="section-heading compact-heading"><div><span className="kicker">Provenance</span><h2>All Evidence</h2></div></div>
                    <div className="evidence-grid">
                      {selectedEvidence.map((item) => <EvidenceCard key={item.id} evidence={item} />)}
                    </div>
                  </section>
                )}

                {report.data && (
                  <section className="conclusion-card">
                    <span className="eyebrow">Conclusion</span>
                    <p>{report.data.explanation}</p>
                    <strong>Next step</strong>
                    <p>{report.data.next_step}</p>
                  </section>
                )}
              </>
            ) : (
              <div className="empty-state">Select a finding to inspect its agent timeline and Evidence.</div>
            )}
          </div>
        </section>
      )}
    </main>
  );
}
