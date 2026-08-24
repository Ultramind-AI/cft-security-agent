import type {
  ApiEvidence,
  ApiFinding,
  ApiProject,
  ApiRun,
  ChatSession,
  ChatSnapshot,
  FinalReport,
  GateResult,
  RunTimeline,
} from "./types";

const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim();
export const API_BASE = (configuredBase || "/api").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function parseError(response: Response): Promise<never> {
  let detail = `${response.status} ${response.statusText}`;
  try {
    const payload = (await response.json()) as { detail?: string };
    if (payload.detail) detail = payload.detail;
  } catch {
    // Keep transport fallback when the response is not JSON.
  }
  throw new ApiError(detail, response.status);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) return parseError(response);
  return (await response.json()) as T;
}

export const api = {
  listProjects: () => request<ApiProject[]>("/projects"),
  uploadProject: async (file: File) => {
    const response = await fetch(`${API_BASE}/projects/import`, {
      method: "POST",
      headers: {
        "Content-Type": "application/zip",
        "X-Project-Filename": file.name,
      },
      body: file,
    });
    if (!response.ok) return parseError(response);
    return (await response.json()) as ApiProject;
  },
  listRuns: () => request<ApiRun[]>("/runs"),
  getRun: (runId: string) => request<ApiRun>(`/runs/${encodeURIComponent(runId)}`),
  createRun: (input: {
    target_id: string;
    agent_mode?: "stub" | "llm";
    max_iterations: number;
    analysis_request?: string;
  }) =>
    request<ApiRun>("/runs", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  listFindings: (runId: string) =>
    request<ApiFinding[]>(`/runs/${encodeURIComponent(runId)}/findings`),
  listEvidence: (runId: string) =>
    request<ApiEvidence[]>(`/runs/${encodeURIComponent(runId)}/evidence`),
  getTimeline: (runId: string) =>
    request<RunTimeline>(`/runs/${encodeURIComponent(runId)}/timeline`),
  getGate: (runId: string) =>
    request<GateResult>(`/runs/${encodeURIComponent(runId)}/gate`),
  getReport: (runId: string, findingId: string) =>
    request<FinalReport>(
      `/runs/${encodeURIComponent(runId)}/reports/${encodeURIComponent(findingId)}`,
    ),
  listChatSessions: () => request<ChatSession[]>("/chat/sessions"),
  createChatSession: (input: { target_id: string; title?: string }) =>
    request<ChatSession>("/chat/sessions", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  getChatSnapshot: (sessionId: string) =>
    request<ChatSnapshot>(`/chat/sessions/${encodeURIComponent(sessionId)}`),
  sendChatMessage: (
    sessionId: string,
    input: { content: string; agent_mode: "stub" | "llm"; max_iterations: number },
  ) =>
    request<ChatSnapshot>(
      `/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
      { method: "POST", body: JSON.stringify(input) },
    ),
};

export function runEventsUrl(runId: string): string {
  return `${API_BASE}/runs/${encodeURIComponent(runId)}/events`;
}

export function chatEventsUrl(sessionId: string): string {
  return `${API_BASE}/chat/sessions/${encodeURIComponent(sessionId)}/events`;
}
