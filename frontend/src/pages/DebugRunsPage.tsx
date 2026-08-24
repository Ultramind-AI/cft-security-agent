import { ArrowLeft, Bug } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listRuns } from "../api/client";
import { formatDateTime, formatDuration, gateLabel, runStatusLabel, shortId } from "../lib/format";

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
          <Link to="/" className="back-link"><ArrowLeft size={15} /> Чат</Link>
          <h1><Bug size={20} /> Прогоны и диагностика</h1>
          <p>История запусков анализа. Основная работа с агентом остаётся в чате.</p>
        </div>
      </header>

      <div className="debug-table-wrap">
        <table className="debug-table">
          <thead>
            <tr>
              <th>Прогон</th>
              <th>Проект</th>
              <th>Состояние</th>
              <th>Решение</th>
              <th>Запущен</th>
              <th>Длительность</th>
            </tr>
          </thead>
          <tbody>
            {(runsQuery.data ?? []).map((run) => (
              <tr key={run.id}>
                <td><Link to={`/runs/${run.id}`}><code>{shortId(run.id)}</code></Link></td>
                <td>{run.target_id}</td>
                <td><span className={`debug-status ${run.status}`}>{runStatusLabel(run.status)}</span></td>
                <td>{run.gate_decision ? <span className={`gate-word ${run.gate_decision}`}>{gateLabel(run.gate_decision)}</span> : "—"}</td>
                <td>{formatDateTime(run.started_at ?? run.created_at)}</td>
                <td>{formatDuration(run.started_at, run.finished_at)}</td>
              </tr>
            ))}
            {runsQuery.data?.length === 0 ? (
              <tr><td colSpan={6} className="debug-empty">Прогонов пока нет.</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
