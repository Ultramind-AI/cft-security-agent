import {
  CircleNotch,
  SidebarSimple,
  WarningCircle,
} from "@phosphor-icons/react";
import type { ApiProject, ChatSnapshot } from "../../api/types";
import type { SseState } from "../../hooks/useSse";
import { gateLabel } from "../../lib/format";

export function ProjectHeader({
  project,
  snapshot,
  streamState,
  onOpenSidebar,
}: {
  project: ApiProject | null;
  snapshot: ChatSnapshot | null;
  streamState: SseState;
  onOpenSidebar: () => void;
}) {
  const run = snapshot?.run ?? null;
  const active = run?.status === "queued" || run?.status === "running";

  return (
    <header className="project-header">
      <button
        type="button"
        className="icon-button mobile-menu"
        onClick={onOpenSidebar}
        aria-label="Открыть меню"
      >
        <SidebarSimple size={19} />
      </button>
      <div className="project-heading">
        <strong>{project?.name ?? "Агент безопасности"}</strong>
        <span>
          {project
            ? [project.environment, ...project.services.slice(0, 3)].join(" · ")
            : "Откройте проект, чтобы начать"}
        </span>
      </div>
      <div className="project-status">
        {active ? (
          <span className="run-state live">
            <CircleNotch size={14} className="spin" />
            {streamState === "error" ? "Переподключение" : "Агент работает"}
          </span>
        ) : run?.status === "technical_failure" ? (
          <span className="run-state technical">
            <WarningCircle size={14} />
            Техническая ошибка
          </span>
        ) : snapshot?.gate ? (
          <span className={`gate-word ${snapshot.gate.decision}`}>
            {gateLabel(snapshot.gate.decision)}
          </span>
        ) : null}
      </div>
    </header>
  );
}
