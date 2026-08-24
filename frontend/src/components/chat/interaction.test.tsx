import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { ApiProject } from "../../api/types";
import { Composer } from "./Composer";
import { EvidenceBlock } from "./EvidenceBlock";
import { FindingBlock } from "./FindingBlock";
import { GateBlock } from "./GateBlock";
import { ToolCall } from "./ToolCall";
import { SuggestedActions } from "./SuggestedActions";

const project: ApiProject = { id: "demo", name: "Demo", environment: "local", services: ["api"], repository_available: true };

describe("chat interactions", () => {
  it("sends a plain chat message with Enter", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    render(<Composer project={project} onSend={send} onOpenProject={() => undefined} />);
    const input = screen.getByLabelText("Сообщение Security Agent");
    await userEvent.type(input, "Проверь проект{enter}");
    expect(send).toHaveBeenCalledWith("Проверь проект");
  });

  it("reveals the real sandbox command and output on demand", () => {
    render(<ToolCall tool={{ actionId: "a1", capability: "sandbox_command", purpose: "Inspect", target: "demo", environment: "local", status: "success", exitCode: 0, durationMs: 12, timedOut: false, parameterNames: ["argv"], command: ["rg", "USER", "/target/Dockerfile"], cwd: "/target", stdout: "USER app", stderr: null, sandboxSessionId: "s1" }} />);
    fireEvent.click(screen.getByText("rg USER /target/Dockerfile"));
    expect(screen.getByText("$ rg USER /target/Dockerfile")).toBeTruthy();
    expect(screen.getByText("USER app")).toBeTruthy();
  });

  it("renders gate, finding and evidence as compact conversation blocks", () => {
    const report = {
      finding_id: "f1",
      finding: { id: "f1", source: "sast", rule_id: "R1", title: "Unsafe config", severity: "HIGH", file: "Dockerfile" },
      status: "confirmed",
      evidence: [], sandbox_actions: [], agent_decisions: [], analysis_summary: null,
      risk_signals: [], code_context: null, hypothesis: null, hypothesis_confidence: null,
      verification: { validator_decision: "approved", evidence_count: 0, evidence_types: [], decision_basis: "evidence" },
      cvss: null, context_priority: null, policy_decisions: [], explanation: "Confirmed by source inspection",
      limitations: [], next_step: "Set a non-root user", iterations: 1, stop_reason: "terminal_evidence", schema_version: "1",
    } as never;
    const evidence = {
      id: "e1", action_id: "a1", type: "source", summary: "No USER directive", artifact_refs: [],
      reliability: "high", verdict: "confirmed", source: "static", sandbox_session_id: "s1", hypothesis_id: "h1",
      action: { id: "a1", tool: "inspect_dockerfile_user" }, observation: { kind: "source", facts: { user: false } },
      scope: { target: "demo", environment: "local", description: "Dockerfile" }, artifacts: [], created_at: "2026-08-24T10:00:00Z",
    } as never;
    const gate = { schema_version: "1", decision: "fail", exit_code: 1, decision_basis: "policy", reports_total: 1, confirmed: 1, rejected: 0, inconclusive: 0, policy_blocked: 0, technical_errors: 0, reasons: [], findings: [] } as never;
    const run = { id: "run-1", target_id: "demo", status: "completed", created_at: "2026-08-24T10:00:00Z" } as never;

    render(
      <MemoryRouter>
        <FindingBlock report={report} />
        <EvidenceBlock evidence={evidence} />
        <GateBlock gate={gate} reports={[report]} run={run} />
      </MemoryRouter>,
    );

    expect(screen.getAllByText("Unsafe config").length).toBeGreaterThan(0);
    expect(screen.getByText("No USER directive")).toBeTruthy();
    expect(screen.getByText("FAIL")).toBeTruthy();
  });

  it("starts a new chat from a suggested action", async () => {
    const select = vi.fn();
    render(<SuggestedActions onSelect={select} />);

    await userEvent.click(screen.getByRole("button", { name: "Authentication and sessions" }));

    expect(select).toHaveBeenCalledWith(
      "Проверь authentication, authorization и управление сессиями",
    );
  });
});
