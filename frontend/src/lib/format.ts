import type { FindingStatus } from "../api/types";

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
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
  return date.toLocaleTimeString(undefined, {
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
      return "PASS";
    case "warn":
      return "WARN";
    case "fail":
      return "FAIL";
    default:
      return decision.toUpperCase();
  }
}

export function stageLabel(stage: string): string {
  switch (stage) {
    case "discovery":
      return "Discovery проекта";
    case "sandbox":
      return "Запуск sandbox-окружения";
    case "sast":
      return "SAST-анализ";
    case "verification":
      return "Проверка findings агентом";
    case "pipeline":
      return "Пайплайн";
    default:
      return stage;
  }
}

export function toolLabel(tool: string): string {
  return tool
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
