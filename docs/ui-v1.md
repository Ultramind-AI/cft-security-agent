# UI v2 — chat-first interface with live investigation timeline

T24 makes chat the primary product surface. The dashboard remains available for debugging and run history, but the normal flow is conversational.

## Product flow

```text
Open folder / drop project ZIP / choose registered target
→ write an analysis request in chat
→ deterministic import validation + Project Discovery
→ canonical CI pipeline starts
→ chat receives live snapshots over SSE:
    stage progress (Discovery → sandbox → SAST → verification)
    per-finding FinalReports as they are written
    executed sandbox actions from the executor audit log
→ Evidence is rendered as strict observation cards, separate from LLM prose
→ deterministic Gate appears in the same conversation
→ follow-up questions are answered from the completed run data
```

The user request is passed into `AgentState` as analysis focus. It can influence what the LLM investigates, but it never expands the registered target or sandbox boundary.

## Project import: folder and ZIP converge

Two client paths, one server flow:

```text
Folder (browser <input webkitdirectory>) ─┐
                                          ├→ staging workspace → Discovery → TargetProfile → project
ZIP bytes (POST /projects/import)        ─┘
```

- `POST /projects/import` accepts raw ZIP bytes (header `X-Project-Filename`).
- `POST /projects/import-files` accepts a JSON manifest of relative paths plus base64 payloads, produced by the browser folder picker. The browser never sends absolute host paths; every path is re-validated server-side (traversal, absolute/Windows paths, depth, duplicates, size limits).
- The staged tree receives its own `git init` + index (best-effort) so git-aware tools such as Semgrep scan it instead of honouring the agent repository's ignore rules.
- Both paths run identical Discovery and register a generated `TargetProfile` under `CFT_API_PROJECT_ROOT`; profiles survive API restarts.
- Client-side collection filters junk (`node_modules`, `.git`, build output, binary assets) and enforces file-count/size limits before upload.

## Live run progress

The canonical pipeline appends stage events to `artifacts/<run>/progress.jsonl`
(`pipeline/progress.py`, best-effort — recording can never break a run). The
executor already appends one JSONL record per action decision to
`audit/executor.jsonl`. The API derives mid-run progress from these artifacts:

```text
GET /runs/{run_id}/progress   → stages + tool-call activities + findings counters
GET /runs/{run_id}/discovery  → sanitized Discovery view (never leaks local paths)
```

Chat SSE snapshots (`GET /chat/sessions/{id}/events`) embed both views and the
session's ordered run history. The presentation layer merges messages, stages,
Discovery, per-finding progress, agent decisions, sandbox actions, Evidence,
finding results and Gate into one chronological conversation.

## Uploaded projects

The server rejects traversal paths, symbolic links, oversized archives/files and uploads with no discovered runnable components. A server-generated `TargetProfile` and conservative architecture description are written under `CFT_API_PROJECT_ROOT` and reloaded after API restart.

The generated architecture file records only Discovery facts. It deliberately does not invent criticality, trust relationships or public exposure.

For live dynamic verification through the API, configure a digest-pinned probe image:

```bash
# .env
CFT_SANDBOX_IMAGE=python@sha256:...
```

## Chat behavior

A chat session is permanently attached to one project. The first user message starts a run. While that run is active, additional messages do not start a second sandbox. After completion, ordinary messages become follow-up questions over persisted Gate/FinalReport/Evidence data. `/reanalyze`, `/analyze`, `/scan`, or a natural explicit re-run request starts another analysis for the same project. Every run remains in the same restored conversation after reload.

The UI never turns LLM prose into Evidence. It renders these as different blocks:

```text
user request
agent status / interpretation
sandbox action
Evidence / observed facts
FinalReport
Gate
```

## Frontend layout

```text
frontend/src/
  api/client.ts api/types.ts      typed fetch client mirroring schemas/api.py
  hooks/useChat.ts                snapshot, SSE, polling fallback, send state
  hooks/useSse.ts                 shared SSE subscription hook
  hooks/useProjectImport.ts       folder collection + ZIP/folder mutations
  lib/timeline.ts                 chronological presentation timeline builder
  components/layout/              sidebar, project header, app shell
  components/project/             Folder / ZIP / existing-project dialog
  components/chat/                messages, composer, tool, Evidence, finding, Gate blocks
  pages/ChatPage.tsx              primary conversation workflow
  pages/DebugRunsPage.tsx         secondary operational run history
  pages/RunPage.tsx               complete single-run report
```

The normal composer intentionally has no LLM/stub selector, step counter,
quick-reply chips, or system settings. Those implementation details are not part
of the user's primary project conversation.

## Run locally

Terminal 1:

```bash
source .venv/bin/activate
cft-security-api
```

Terminal 2:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

Vite proxies `/api/*` to `http://127.0.0.1:8080/*`, so local development does not require permissive CORS. For an isolated API instance, set `CFT_UI_API_PROXY` before `npm run dev`.

Frontend checks:

```bash
npm run typecheck
npm test
npm run build
```
