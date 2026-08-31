// Зеркало контрактов FastAPI из schemas/api.py и связанных schema modules

export type ApiRunStatus = "queued" | "running" | "completed" | "technical_failure";
export type GateDecision = "pass" | "warn" | "fail" | null;
export type FindingStatus =
  | "confirmed"
  | "rejected"
  | "inconclusive"
  | "policy_blocked";

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
  status: ApiRunStatus;
  agent_mode: "stub" | "llm" | null;
  max_iterations: number;
  analysis_request: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  gate_decision: GateDecision;
  error: ErrorDetail | null;
}

export interface CreateRunRequest {
  target_id: string;
  agent_mode?: "stub" | "llm" | null;
  max_iterations?: number;
  analysis_request?: string | null;
}

// ----- Evidence -----

export interface EvidenceAction {
  id: string;
  tool: string;
  run_id?: string | null;
}

export interface EvidenceObservation {
  kind: string;
  facts: Record<string, unknown>;
  captured_at?: string | null;
}

export interface EvidenceScope {
  target: string;
  environment: string;
  service?: string | null;
  description: string;
}

export interface EvidenceArtifact {
  ref: string;
  role: string;
}

export interface Evidence {
  id: string;
  action_id: string;
  type: string;
  summary: string;
  artifact_refs: string[];
  reliability: "high" | "medium" | "low" | "unknown";
  verdict: "confirmed" | "rejected" | "inconclusive" | null;
  source: "static" | "runtime";
  sandbox_session_id: string | null;
  hypothesis_id: string;
  action: EvidenceAction;
  observation: EvidenceObservation;
  scope: EvidenceScope;
  artifacts: EvidenceArtifact[];
  created_at: string;
}

export interface ApiEvidence {
  run_id: string;
  finding_id: string;
  evidence: Evidence;
}

export interface ApiFinding {
  run_id: string;
  finding_id: string;
  status: FindingStatus | string;
  source: string;
  rule_id: string;
  title: string;
  severity: string | null;
  service: string | null;
  file: string;
  line_start: number | null;
  report_available: boolean;
}

export interface AgentDecisionTimelineItem {
  step: number;
  outcome: "continue" | "stop";
  reason: string;
  evidence_ids: string[];
  plan_id: string | null;
  stop_reason: string | null;
  recorded_at: string;
}

export interface FindingTimeline {
  finding_id: string;
  agent_decisions: AgentDecisionTimelineItem[];
  sandbox_actions: SandboxActionSummary[];
}

export interface RunTimeline {
  run_id: string;
  findings: FindingTimeline[];
}

// ----- Отчеты -----

export interface ReportFinding {
  id: string;
  source: string;
  rule_id: string;
  title: string;
  description?: string | null;
  severity?: string | null;
  service?: string | null;
  file: string;
  line_start?: number | null;
  line_end?: number | null;
}

export interface SandboxActionSummary {
  action_id: string;
  capability: string;
  target?: string | null;
  environment?: string | null;
  purpose: string;
  parameter_names: string[];
  execution_status: string | null;
  exit_code: number | null;
  timed_out: boolean;
  duration_ms: number | null;
  command: string[];
  cwd: string | null;
  stdout: string | null;
  stderr: string | null;
  sandbox_session_id: string | null;
  artifact_refs: string[];
}

export interface AgentDecisionRecord {
  step: number;
  outcome: "continue" | "stop";
  reason: string;
  evidence_ids: string[];
  plan_id: string | null;
  stop_reason:
    | "terminal_evidence"
    | "policy_blocked"
    | "plan_rejected"
    | "step_budget_exhausted"
    | "wall_clock_budget_exhausted"
    | "execution_failed"
    | "insufficient_evidence"
    | null;
  recorded_at: string;
}

export interface VerificationSummary {
  action_id?: string | null;
  capability?: string | null;
  target?: string | null;
  environment?: string | null;
  validator_decision: "approved" | "denied" | "not_run";
  validator_reason?: string | null;
  evidence_count: number;
  evidence_types: string[];
  decision_basis: string;
}

export interface CVSSResult {
  vector: string | null;
  score: number | null;
  severity: string | null;
  qualitative_severity?: string | null;
  reasoning?: string | null;
  status?: string | null;
}

export interface ContextPriority {
  level: string;
  score?: number | null;
  reasons?: string[] | null;
}

export interface FinalReport {
  schema_version: string;
  finding_id: string;
  finding: ReportFinding;
  status: FindingStatus;
  analysis_summary: string | null;
  risk_signals: string[];
  code_context: string | null;
  hypothesis: string | null;
  hypothesis_confidence: number | null;
  verification: VerificationSummary;
  cvss: CVSSResult | null;
  context_priority: ContextPriority | null;
  evidence: Evidence[];
  sandbox_actions: SandboxActionSummary[];
  policy_decisions: {
    action_id: string;
    decision: string;
    reason: string;
    rules: string[];
  }[];
  agent_decisions: AgentDecisionRecord[];
  explanation: string;
  limitations: string[];
  next_step: string;
  iterations: number;
  stop_reason: string | null;
}

// ----- Gate -----

export interface PipelineFindingResult {
  finding_id: string;
  status: string;
  gate_effect: "pass" | "warn" | "fail";
  category: string;
  reason: string;
  context_priority?: string | null;
  cvss_severity?: string | null;
}

export interface GateResult {
  schema_version: string;
  decision: "pass" | "warn" | "fail";
  exit_code: number;
  decision_basis: string;
  reports_total: number;
  confirmed: number;
  rejected: number;
  inconclusive: number;
  policy_blocked: number;
  technical_errors: number;
  reasons: string[];
  findings: PipelineFindingResult[];
}

// ----- Прогресс и discovery активного запуска -----

export interface RunStageEvent {
  stage: string;
  status: string;
  detail: string | null;
  at: string | null;
}

export interface RunActivityEvent {
  action_id: string;
  tool: string;
  target: string | null;
  status: string | null;
  exit_code: number | null;
  duration_ms: number | null;
  at: string | null;
}

export interface RunFindingProgressEvent {
  finding_id: string;
  status: "started" | "finished";
  title: string | null;
  severity: string | null;
  rule_id: string | null;
  file: string | null;
  index: number | null;
  total: number | null;
  result: string | null;
  at: string | null;
}

export interface RunProgress {
  stages: RunStageEvent[];
  activities: RunActivityEvent[];
  finding_events: RunFindingProgressEvent[];
  findings_total: number | null;
  findings_done: number;
  current_finding: string | null;
}

export interface DiscoveryComponentView {
  id: string;
  root: string;
  technologies: string[];
  frameworks: string[];
  dependency_files: string[];
  dockerfiles: string[];
  local_addresses: string[];
}

export interface RunDiscoveryView {
  components: DiscoveryComponentView[];
  services: string[];
  technologies: string[];
  warnings: string[];
}

// ----- Чат -----

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
  role: "user" | "assistant" | "system";
  kind: "text" | "status" | "summary" | "error";
  content: string;
  run_id: string | null;
  created_at: string;
}

export interface ChatRunSnapshot {
  run: ApiRun;
  reports: FinalReport[];
  gate: GateResult | null;
  progress: RunProgress | null;
  discovery: RunDiscoveryView | null;
}

export interface ChatSnapshot {
  session: ChatSession;
  messages: ChatMessage[];
  run: ApiRun | null;
  reports: FinalReport[];
  gate: GateResult | null;
  progress: RunProgress | null;
  discovery: RunDiscoveryView | null;
  runs: ChatRunSnapshot[];
}

export interface ImportedFileEntry {
  path: string;
  content_base64: string;
}
