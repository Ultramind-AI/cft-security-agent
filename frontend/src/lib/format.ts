import type { FindingStatus } from "../api/types";

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatClock(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDuration(startedAt: string | null, finishedAt: string | null): string {
  if (!startedAt || !finishedAt) return "—";
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  if (Number.isNaN(ms) || ms < 0) return "—";
  if (ms < 1000) return `${ms} мс`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} с`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} мин ${Math.round(seconds % 60)} с`;
}

export function formatMs(ms: number | null): string {
  if (ms === null || ms === undefined) return "";
  if (ms < 1000) return `${ms} мс`;
  return `${(ms / 1000).toFixed(1)} с`;
}

export function shortId(id: string): string {
  return id.length > 14 ? `${id.slice(0, 10)}…` : id;
}

export function severityTone(severity: string | null | undefined): string {
  const value = (severity ?? "").toUpperCase();
  if (["CRITICAL", "HIGH"].includes(value)) return "critical";
  if (value === "MEDIUM" || value === "MODERATE") return "medium";
  if (value === "LOW") return "low";
  return "none";
}

export function findingStatusLabel(status: FindingStatus): string {
  switch (status) {
    case "confirmed":
      return "Подтверждён";
    case "rejected":
      return "Отклонён";
    case "inconclusive":
      return "Недостаточно данных";
    case "policy_blocked":
      return "Заблокирован политикой";
  }
}

export function gateLabel(decision: string): string {
  switch (decision) {
    case "pass":
      return "ПРОЙДЕН";
    case "warn":
      return "С ПРЕДУПРЕЖДЕНИЯМИ";
    case "fail":
      return "НЕ ПРОЙДЕН";
    default:
      return decision.toUpperCase();
  }
}

export function stageLabel(stage: string): string {
  switch (stage) {
    case "discovery":
      return "Исследование проекта";
    case "sandbox":
      return "Запуск sandbox-окружения";
    case "sast":
      return "SAST-анализ";
    case "verification":
      return "Проверка находок агентом";
    case "pipeline":
      return "Пайплайн";
    default:
      return stage;
  }
}

export function toolLabel(tool: string): string {
  const labels: Record<string, string> = {
    safe_noop: "Безопасная контрольная проверка",
    observe_http_surface: "Проверка HTTP-поверхности",
    sandbox_command: "Команда в песочнице",
    inspect_dockerfile_user: "Проверка пользователя Dockerfile",
    inspect_python_password_assignment: "Проверка назначения пароля",
    inspect_react_dangerous_html_flow: "Проверка опасного HTML-потока React",
  };
  if (labels[tool]) return labels[tool];
  return tool
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function severityLabel(severity: string | null | undefined): string {
  switch ((severity ?? "").toUpperCase()) {
    case "CRITICAL": return "КРИТИЧЕСКАЯ";
    case "ERROR":
    case "HIGH": return "ВЫСОКАЯ";
    case "WARNING":
    case "MEDIUM":
    case "MODERATE": return "СРЕДНЯЯ";
    case "INFO":
    case "LOW": return "НИЗКАЯ";
    default: return "НЕ ОПРЕДЕЛЕНА";
  }
}

export function runStatusLabel(status: string): string {
  switch (status) {
    case "queued": return "В очереди";
    case "running": return "Выполняется";
    case "completed": return "Завершён";
    case "technical_failure": return "Техническая ошибка";
    default: return status;
  }
}

export function stageStatusLabel(status: string): string {
  switch (status) {
    case "running": return "выполняется";
    case "done":
    case "completed": return "завершено";
    case "failed": return "ошибка";
    case "started": return "начато";
    default: return status;
  }
}

export function stageDetailLabel(detail: string | null, status: string): string {
  if (!detail) return stageStatusLabel(status);
  const components = detail.match(/^(\d+) components?: (.+)$/i);
  if (components) return `${components[1]} компонента: ${components[2]}`;
  const services = detail.match(/^(\d+) services? ready$/i);
  if (services) return `Готово сервисов: ${services[1]}`;
  const findings = detail.match(/^(\d+) findings?$/i);
  if (findings) return `Находок: ${findings[1]}`;
  return detail;
}

export function reliabilityLabel(reliability: string): string {
  switch (reliability) {
    case "high": return "высокая";
    case "medium": return "средняя";
    case "low": return "низкая";
    default: return reliability;
  }
}

export function evidenceSourceLabel(source: string): string {
  switch (source) {
    case "static": return "Статический анализ";
    case "runtime": return "Среда выполнения";
    case "sandbox": return "Песочница";
    default: return source;
  }
}

export function findingTitle(ruleId: string, fallback: string): string {
  const rule = ruleId.toLowerCase();
  if (rule.includes("missing-user")) {
    return "В Dockerfile не указан непривилегированный пользователь";
  }
  if (rule.includes("unvalidated-password")) {
    return "Пароль назначается без встроенной проверки Django";
  }
  if (rule.includes("react-dangerouslysetinnerhtml")) {
    return "Использование dangerouslySetInnerHTML может привести к XSS";
  }
  if (rule.includes("mutable-action-tag")) {
    return "GitHub Action использует изменяемый тег вместо SHA-коммита";
  }
  return fallback;
}

export function evidenceSummary(summary: string): string {
  if (summary.includes("does not establish an explicit non-root USER")) {
    return "В финальном слое Dockerfile не задан непривилегированный USER. Подтверждена проблема в исходном файле; UID процесса во время выполнения не проверялся.";
  }
  return summary;
}
