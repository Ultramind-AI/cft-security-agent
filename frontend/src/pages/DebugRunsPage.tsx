import { ArrowLeft, Bug } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listRuns } from "../api/client";
import { formatDateTime, formatDuration, shortId } from "../lib/format";

export function DebugRunsPage() {
  const runsQuery = useQuery({
    queryKey: ["runs"],
    queryFn: () => listRuns(200),
    refetchInterval: 4_000,
  });

  return (
    <div className="debug-page">
      <header className="debug-header">
        <div>
          <Link to="/" className="back-link"><ArrowLeft size={15} /> Chat</Link>
          <h1><Bug size={20} /> Runs / Debug</h1>
          <p>Operational run history. The primary product workflow stays in chat.</p>
        </div>
      </header>

      <div className="debug-table-wrap">
        <table className="debug-table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Project</th>
              <th>Status</th>
              <th>Gate</th>
              <th>Started</th>
              <th>Duration</th>
            </tr>
          </thead>
          <tbody>
            {(runsQuery.data ?? []).map((run) => (
              <tr key={run.id}>
                <td><Link to={`/runs/${run.id}`}><code>{shortId(run.id)}</code></Link></td>
                <td>{run.target_id}</td>
                <td><span className={`debug-status ${run.status}`}>{run.status}</span></td>
                <td>{run.gate_decision ? <span className={`gate-word ${run.gate_decision}`}>{run.gate_decision.toUpperCase()}</span> : "—"}</td>
                <td>{formatDateTime(run.started_at ?? run.created_at)}</td>
                <td>{formatDuration(run.started_at, run.finished_at)}</td>
              </tr>
            ))}
            {runsQuery.data?.length === 0 ? (
              <tr><td colSpan={6} className="debug-empty">No runs yet.</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
