export type RunStatus = "queued" | "running" | "completed" | "technical_failure";
export type GateDecision = "pass" | "warn" | "fail";
export type ChatRole = "user" | "assistant" | "system";
export type ChatMessageKind = "text" | "status" | "summary" | "error";

export interface ApiProject {
  id: string;
  name: string;
  environment: string;
  services: string[];
  repository_available: boolean;
}

export interface ErrorDetail {
  code: string;
  layer: string;
  message: string;
  retryable: boolean;
}

export interface ApiRun {
  id: string;
  target_id: string;
  status: RunStatus;
  agent_mode: "stub" | "llm" | null;
  max_iterations: number;
  analysis_request: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  gate_decision: GateDecision | null;
  error: ErrorDetail | null;
}

export interface ApiFinding {
  run_id: string;
  finding_id: string;
  status: string;
  source: string;
  rule_id: string;
  title: string;
  severity: string | null;
  service: string | null;
  file: string;
  line_start: number | null;
  report_available: boolean;
}

export interface Evidence {
  id: string;
  action_id: string;
  type: string;
  summary: string;
  artifact_refs: string[];
  reliability: string;
  verdict: string | null;
  source: "static" | "runtime";
  sandbox_session_id: string | null;
  hypothesis_id: string;
  observation: {
    kind: string;
    facts: Record<string, unknown>;
    captured_at: string;
  };
  scope: {
    target: string;
    environment: string;
    service: string | null;
    description: string;
  };
  created_at: string;
}

export interface ApiEvidence {
  run_id: string;
  finding_id: string;
  evidence: Evidence;
}

export interface AgentDecisionRecord {
  step: number;
  outcome: "continue" | "stop";
  reason: string;
  evidence_ids: string[];
  plan_id: string | null;
  stop_reason: string | null;
  recorded_at: string;
}

export interface SandboxActionSummary {
  action_id: string;
  capability: string;
  target: string;
  environment: string;
  purpose: string;
  parameter_names: string[];
  execution_status: string | null;
  exit_code: number | null;
  timed_out: boolean;
  artifact_refs: string[];
}

export interface FindingTimeline {
  finding_id: string;
  agent_decisions: AgentDecisionRecord[];
  sandbox_actions: SandboxActionSummary[];
}

export interface RunTimeline {
  run_id: string;
  findings: FindingTimeline[];
}

export interface GateResult {
  decision: GateDecision;
  exit_code: number;
  decision_basis: string;
  reports_total: number;
  confirmed: number;
  rejected: number;
  inconclusive: number;
  policy_blocked: number;
  technical_errors: number;
  reasons: string[];
  stage_errors: string[];
}

export interface FinalReport {
  finding_id: string;
  status: string;
  finding: {
    title: string;
    rule_id: string;
    severity: string | null;
    service: string | null;
    file: string;
    line_start: number | null;
  };
  analysis_summary: string | null;
  hypothesis: string | null;
  hypothesis_confidence: number | null;
  evidence: Evidence[];
  sandbox_actions: SandboxActionSummary[];
  agent_decisions: AgentDecisionRecord[];
  explanation: string;
  limitations: string[];
  next_step: string;
  iterations: number;
  stop_reason: string | null;
  ci_gate_impact: {
    effect: GateDecision;
    category: string;
    reason: string;
  } | null;
}

export interface ChatSession {
  id: string;
  target_id: string;
  title: string;
  active_run_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: ChatRole;
  kind: ChatMessageKind;
  content: string;
  run_id: string | null;
  created_at: string;
}

export interface ChatSnapshot {
  session: ChatSession;
  messages: ChatMessage[];
  run: ApiRun | null;
  reports: FinalReport[];
  gate: GateResult | null;
}
