import type {
  ApiEvidence,
  ApiFinding,
  ApiProject,
  ApiRun,
  ChatSession,
  ChatSnapshot,
  CreateRunRequest,
  FinalReport,
  GateResult,
  ImportedFileEntry,
  RunDiscoveryView,
  RunProgress,
  RunTimeline,
} from "./types";

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "/api";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function validationDetail(payload: unknown): string | null {
  if (!payload || typeof payload !== "object" || !("detail" in payload)) return null;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return null;

  const messages = detail.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const record = item as { loc?: unknown; msg?: unknown };
    if (typeof record.msg !== "string") return [];
    const location = Array.isArray(record.loc)
      ? record.loc.filter((part) => part !== "body").join(".")
      : "";
    return [location ? `${location}: ${record.msg}` : record.msg];
  });
  return messages.length > 0 ? messages.join("; ") : null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers:
      init?.body && !(init.body instanceof Blob) && !(init.body instanceof ArrayBuffer)
        ? { "Content-Type": "application/json", ...init?.headers }
        : init?.headers,
    ...init,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = validationDetail(payload) ?? detail;
    } catch {
      // Оставляем detail по умолчанию
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ----- Health и проекты -----

export function getHealth(): Promise<{ status: string }> {
  return request("/health");
}

export function listProjects(): Promise<ApiProject[]> {
  return request("/projects");
}

export async function uploadProjectZip(
  file: File | Blob,
  filename: string,
): Promise<ApiProject> {
  const buffer = await file.arrayBuffer();
  return request("/projects/import", {
    method: "POST",
    headers: {
      "Content-Type": "application/zip",
      "X-Project-Filename": filename.slice(0, 255),
    },
    body: buffer,
  });
}

export function importProjectFiles(payload: {
  name: string;
  files: ImportedFileEntry[];
}): Promise<ApiProject> {
  return request("/projects/import-files", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ----- Запуски -----

export function listRuns(limit = 100): Promise<ApiRun[]> {
  return request(`/runs?limit=${limit}`);
}

export function createRun(payload: CreateRunRequest): Promise<ApiRun> {
  return request("/runs", { method: "POST", body: JSON.stringify(payload) });
}

export function getRun(runId: string): Promise<ApiRun> {
  return request(`/runs/${runId}`);
}

export function getFindings(runId: string): Promise<ApiFinding[]> {
  return request(`/runs/${runId}/findings`);
}

export function getFinding(runId: string, findingId: string): Promise<ApiFinding> {
  return request(`/runs/${runId}/findings/${findingId}`);
}

export function getEvidence(
  runId: string,
  findingId?: string,
): Promise<ApiEvidence[]> {
  const suffix = findingId ? `?finding_id=${encodeURIComponent(findingId)}` : "";
  return request(`/runs/${runId}/evidence${suffix}`);
}

export function getTimeline(runId: string): Promise<RunTimeline> {
  return request(`/runs/${runId}/timeline`);
}

export function getReports(runId: string): Promise<FinalReport[]> {
  return request(`/runs/${runId}/reports`);
}

export function getReport(runId: string, findingId: string): Promise<FinalReport> {
  return request(`/runs/${runId}/reports/${findingId}`);
}

export function getGate(runId: string): Promise<GateResult> {
  return request(`/runs/${runId}/gate`);
}

export function getRunProgress(runId: string): Promise<RunProgress> {
  return request(`/runs/${runId}/progress`);
}

export function getRunDiscovery(runId: string): Promise<RunDiscoveryView> {
  return request(`/runs/${runId}/discovery`);
}

// ----- Чат -----

export function listChatSessions(limit = 100): Promise<ChatSession[]> {
  return request(`/chat/sessions?limit=${limit}`);
}

export function createChatSession(targetId: string, title?: string): Promise<ChatSession> {
  return request("/chat/sessions", {
    method: "POST",
    body: JSON.stringify({ target_id: targetId, title: title ?? null }),
  });
}

export function deleteChatSession(sessionId: string): Promise<void> {
  return request(`/chat/sessions/${sessionId}`, { method: "DELETE" });
}

export function getChatSnapshot(sessionId: string): Promise<ChatSnapshot> {
  return request(`/chat/sessions/${sessionId}`);
}

export function sendChatMessage(
  sessionId: string,
  content: string,
  options?: { agent_mode?: "stub" | "llm"; max_iterations?: number },
): Promise<ChatSnapshot> {
  return request(`/chat/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({
      content,
      agent_mode: options?.agent_mode ?? "llm",
      max_iterations: options?.max_iterations ?? 5,
    }),
  });
}

// ----- SSE URL -----

export function runEventsUrl(runId: string): string {
  return `${BASE_URL}/runs/${runId}/events`;
}

export function chatEventsUrl(sessionId: string): string {
  return `${BASE_URL}/chat/sessions/${sessionId}/events`;
}
