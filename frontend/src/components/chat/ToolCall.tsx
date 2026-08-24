import {
  CaretRight,
  CheckCircle,
  TerminalWindow,
  WarningCircle,
} from "@phosphor-icons/react";
import type { TimelineTool } from "../../lib/timeline";
import { formatMs, toolLabel } from "../../lib/format";

export function ToolCall({ tool }: { tool: TimelineTool }) {
  const failed =
    tool.status === "failed" ||
    tool.status === "denied" ||
    (tool.exitCode !== null && tool.exitCode !== 0);
  const command = tool.command.length > 0 ? formatCommand(tool.command) : null;

  return (
    <details className={`tool-call ${failed ? "failed" : "ok"}`}>
      <summary>
        <CaretRight size={14} className="details-caret" />
        <TerminalWindow size={16} />
        <span className="tool-verb">{toolVerb(tool.capability, command)}</span>
        <code>{command ?? toolLabel(tool.capability)}</code>
        <span className="tool-result">
          {failed ? <WarningCircle size={15} /> : <CheckCircle size={15} />}
          {tool.exitCode !== null ? `код ${tool.exitCode}` : toolStatus(tool.status)}
          {tool.durationMs !== null ? ` · ${formatMs(tool.durationMs)}` : ""}
        </span>
      </summary>

      <div className="tool-details">
        {tool.purpose ? (
          <div className="tool-purpose">
            <span>Цель</span>
            <p>{tool.purpose}</p>
          </div>
        ) : null}

        {command ? (
          <div className="terminal-block">
            <div className="terminal-title">команда</div>
            <pre>$ {command}</pre>
          </div>
        ) : null}
        {tool.stdout ? (
          <div className="terminal-block">
            <div className="terminal-title">stdout</div>
            <pre>{tool.stdout}</pre>
          </div>
        ) : null}
        {tool.stderr ? (
          <div className="terminal-block stderr">
            <div className="terminal-title">stderr</div>
            <pre>{tool.stderr}</pre>
          </div>
        ) : null}

        <dl className="compact-metadata">
          <div>
            <dt>Возможность</dt>
            <dd>{tool.capability}</dd>
          </div>
          {tool.cwd ? (
            <div>
              <dt>Рабочая папка</dt>
              <dd>{tool.cwd}</dd>
            </div>
          ) : null}
          {tool.target ? (
            <div>
              <dt>Цель</dt>
              <dd>{tool.target}</dd>
            </div>
          ) : null}
          {tool.environment ? (
            <div>
              <dt>Окружение</dt>
              <dd>{tool.environment}</dd>
            </div>
          ) : null}
          {tool.sandboxSessionId ? (
            <div>
              <dt>Сессия песочницы</dt>
              <dd>{tool.sandboxSessionId}</dd>
            </div>
          ) : null}
          <div>
            <dt>Действие</dt>
            <dd>{tool.actionId}</dd>
          </div>
        </dl>
      </div>
    </details>
  );
}

function toolVerb(capability: string, command: string | null): string {
  if (command) return "Выполнено";
  if (capability.startsWith("inspect_")) return "Проверено";
  if (capability.startsWith("observe_") || capability.startsWith("check_")) {
    return "Проверено";
  }
  return "Вызвано";
}

function toolStatus(status: string | null): string {
  switch (status) {
    case "completed": return "завершено";
    case "failed": return "ошибка";
    case "denied": return "отклонено";
    case "running": return "выполняется";
    default: return status ?? "записано";
  }
}

function formatCommand(argv: string[]): string {
  return argv.map((part) => (/\s/.test(part) ? JSON.stringify(part) : part)).join(" ");
}
