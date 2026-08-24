import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";
import { StatusPill } from "../components/StatusPill";

function formatDate(value: string | null) {
  return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
}

export function DashboardPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [agentMode, setAgentMode] = useState<"" | "stub" | "llm">("");
  const [maxIterations, setMaxIterations] = useState(3);
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.listProjects });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.listRuns, refetchInterval: 4000 });

  const createRun = useMutation({
    mutationFn: (targetId: string) =>
      api.createRun({
        target_id: targetId,
        ...(agentMode ? { agent_mode: agentMode } : {}),
        max_iterations: maxIterations,
      }),
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${run.id}`);
    },
  });

  return (
    <main className="page-shell">
      <section className="hero-grid">
        <div>
          <span className="kicker">Security control plane</span>
          <h1>Investigate code. Watch the evidence. Decide the gate.</h1>
          <p className="hero-copy">
            One UI for registered targets, sandbox agent runs, factual Evidence and deterministic CI decisions.
          </p>
        </div>
        <div className="control-card">
          <span className="eyebrow">Run defaults</span>
          <label>
            Agent mode
            <select value={agentMode} onChange={(event) => setAgentMode(event.target.value as "" | "stub" | "llm")}>
              <option value="">Configured default</option>
              <option value="llm">LLM</option>
              <option value="stub">Stub</option>
            </select>
          </label>
          <label>
            Max investigation steps
            <input
              type="number"
              min={1}
              max={8}
              value={maxIterations}
              onChange={(event) => setMaxIterations(Math.max(1, Math.min(8, Number(event.target.value) || 1)))}
            />
          </label>
          <p>Execution remains bounded by sandbox policy and the server-side wall-clock budget.</p>
        </div>
      </section>

      <section className="section-block">
        <div className="section-heading">
          <div>
            <span className="kicker">Registered targets</span>
            <h2>Projects</h2>
          </div>
          <span className="count-badge">{projects.data?.length ?? 0}</span>
        </div>

        {projects.isLoading && <div className="empty-state">Loading registered targets…</div>}
        {projects.error && <div className="error-banner">Could not load projects: {projects.error.message}</div>}
        <div className="project-grid">
          {projects.data?.map((project) => (
            <article className="project-card" key={project.id}>
              <div className="project-card-top">
                <div>
                  <span className="eyebrow">{project.environment}</span>
                  <h3>{project.name}</h3>
                  <code>{project.id}</code>
                </div>
                <span className={`availability-dot ${project.repository_available ? "available" : "missing"}`} />
              </div>
              <div className="chip-row">
                {project.services.map((service) => <span className="service-chip" key={service}>{service}</span>)}
              </div>
              <button
                className="primary-button"
                type="button"
                disabled={!project.repository_available || createRun.isPending}
                onClick={() => createRun.mutate(project.id)}
              >
                {createRun.isPending ? "Queueing…" : "Start analysis"}
              </button>
              {!project.repository_available && <p className="muted">Repository is not available on the API host.</p>}
            </article>
          ))}
        </div>
        {createRun.error && <div className="error-banner">Could not create run: {createRun.error.message}</div>}
      </section>

      <section className="section-block">
        <div className="section-heading">
          <div>
            <span className="kicker">Recent activity</span>
            <h2>Runs</h2>
          </div>
          <button className="ghost-button" type="button" onClick={() => void runs.refetch()}>Refresh</button>
        </div>
        {runs.error && <div className="error-banner">Could not load runs: {runs.error.message}</div>}
        <div className="run-table-wrap">
          <table className="run-table">
            <thead>
              <tr>
                <th>Run</th>
                <th>Target</th>
                <th>Status</th>
                <th>Gate</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {runs.data?.map((run) => (
                <tr key={run.id} onClick={() => navigate(`/runs/${run.id}`)}>
                  <td><code>{run.id.slice(0, 16)}…</code></td>
                  <td>{run.target_id}</td>
                  <td><StatusPill value={run.status} /></td>
                  <td><StatusPill value={run.gate_decision} /></td>
                  <td>{formatDate(run.created_at)}</td>
                </tr>
              ))}
              {!runs.isLoading && runs.data?.length === 0 && (
                <tr><td colSpan={5}><div className="empty-state">No runs yet. Start one above.</div></td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
