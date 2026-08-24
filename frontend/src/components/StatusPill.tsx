import type { GateDecision, RunStatus } from "../types";

type StatusValue = RunStatus | GateDecision | string | null | undefined;

export function StatusPill({ value }: { value: StatusValue }) {
  const normalized = value || "unknown";
  const tone =
    normalized === "pass" || normalized === "completed" || normalized === "confirmed"
      ? "good"
      : normalized === "warn" || normalized === "running" || normalized === "inconclusive"
        ? "warn"
        : normalized === "fail" ||
            normalized === "technical_failure" ||
            normalized === "policy_blocked"
          ? "bad"
          : "neutral";

  return <span className={`status-pill status-${tone}`}>{normalized.replaceAll("_", " ")}</span>;
}
