import { describe, expect, it } from "vitest";
import type { ChatSnapshot } from "../api/types";
import { buildConversationTimeline } from "./timeline";

const snapshot = {
  session: { id: "chat-1", target_id: "demo", title: "Demo", active_run_id: "run-1", created_at: "2026-08-24T10:00:00Z", updated_at: "2026-08-24T10:02:00Z" },
  messages: [
    { id: "m1", session_id: "chat-1", role: "user", kind: "text", content: "Check it", run_id: "run-1", created_at: "2026-08-24T10:00:01Z" },
  ],
  run: null,
  reports: [],
  gate: null,
  progress: null,
  discovery: null,
  runs: [{
    run: { id: "run-1", target_id: "demo", status: "completed", agent_mode: "stub", max_iterations: 3, analysis_request: "Check it", created_at: "2026-08-24T10:00:00Z", started_at: "2026-08-24T10:00:00Z", finished_at: "2026-08-24T10:02:00Z", exit_code: 0, gate_decision: "pass", error: null },
    progress: {
      stages: [{ stage: "discovery", status: "completed", detail: "done", at: "2026-08-24T10:00:02Z" }],
      activities: [{ action_id: "a1", tool: "sandbox_command", target: "demo", status: "success", exit_code: 0, duration_ms: 12, at: "2026-08-24T10:01:00Z" }],
      finding_events: [{ finding_id: "f1", status: "started", title: "Unsafe config", severity: "HIGH", rule_id: "R1", file: "Dockerfile", index: 1, total: 1, result: null, at: "2026-08-24T10:00:30Z" }],
      findings_total: 1, findings_done: 1, current_finding: null,
    },
    discovery: { components: [], services: ["api"], technologies: ["Python"], warnings: [] },
    reports: [{
      finding_id: "f1",
      finding: { id: "f1", source: "sast", rule_id: "R1", title: "Unsafe config", severity: "HIGH", file: "Dockerfile" },
      status: "confirmed",
      sandbox_actions: [{ action_id: "a1", capability: "sandbox_command", purpose: "Inspect", parameter_names: ["argv"], execution_status: "success", exit_code: 0, timed_out: false, duration_ms: 12, command: ["rg", "USER", "/target/Dockerfile"], cwd: "/target", stdout: "USER app", stderr: null, sandbox_session_id: "s1", artifact_refs: [] }],
      evidence: [{ id: "e1", action_id: "a1", type: "source", summary: "USER found", artifact_refs: [], reliability: "high", verdict: "confirmed", source: "static", sandbox_session_id: "s1", hypothesis_id: "h1", action: { id: "a1", tool: "sandbox_command" }, observation: { kind: "source", facts: { user: true } }, scope: { target: "demo", environment: "local", description: "Dockerfile" }, artifacts: [], created_at: "2026-08-24T10:01:01Z" }],
      agent_decisions: [], analysis_summary: null, risk_signals: [], code_context: null, hypothesis: null, hypothesis_confidence: null, verification: { validator_decision: "approved", evidence_count: 1, evidence_types: ["source"], decision_basis: "evidence" }, cvss: null, context_priority: null, policy_decisions: [], explanation: "Confirmed", limitations: [], next_step: "Fix", iterations: 1, stop_reason: "terminal_evidence", schema_version: "1",
    }],
    gate: { schema_version: "1", decision: "pass", exit_code: 0, decision_basis: "policy", reports_total: 1, confirmed: 1, rejected: 0, inconclusive: 0, policy_blocked: 0, technical_errors: 0, reasons: [], findings: [] },
  }],
} as ChatSnapshot;

describe("buildConversationTimeline", () => {
  it("combines messages, run progress, tools, evidence, findings and gate chronologically", () => {
    const items = buildConversationTimeline(snapshot);
    expect(items.map((item) => item.kind)).toEqual([
      "run_start", "message", "stage", "discovery", "finding_progress",
      "tool", "evidence", "finding", "gate",
    ]);
  });

  it("deduplicates a tool seen in progress and the final report", () => {
    const tools = buildConversationTimeline(snapshot).filter((item) => item.kind === "tool");
    expect(tools).toHaveLength(1);
    expect(tools[0].kind === "tool" && tools[0].tool.command).toEqual(["rg", "USER", "/target/Dockerfile"]);
  });

  it("shows a technical failure without a duplicate security gate", () => {
    const failed = structuredClone(snapshot);
    failed.runs[0].run.status = "technical_failure";
    failed.runs[0].run.exit_code = 2;
    failed.runs[0].run.gate_decision = "fail";
    failed.runs[0].reports = [];
    failed.messages.push({
      id: "m-technical-summary",
      session_id: "chat-1",
      role: "assistant",
      kind: "summary",
      content: "Анализ завершён. Gate: FAIL.",
      run_id: "run-1",
      created_at: "2026-08-24T10:02:00Z",
    });
    const technicalGate = failed.runs[0].gate!;
    failed.runs[0].gate = {
      ...technicalGate,
      exit_code: 2,
      decision: "fail",
      decision_basis: "technical_pipeline_error",
      reports_total: 0,
      confirmed: 0,
      technical_errors: 1,
    };

    const kinds = buildConversationTimeline(failed).map((item) => item.kind);

    expect(kinds).toContain("technical_error");
    expect(kinds).not.toContain("gate");
    expect(
      buildConversationTimeline(failed).some(
        (item) => item.kind === "message" && item.message.kind === "summary",
      ),
    ).toBe(false);
  });
});
