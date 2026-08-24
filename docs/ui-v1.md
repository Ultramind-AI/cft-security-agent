# UI v1 — T24 chat-first interface

T24 makes chat the primary product surface. The dashboard remains available for debugging and run history, but the normal flow is conversational.

## Product flow

```text
Drop project ZIP / choose registered target
→ write an analysis request in chat
→ deterministic ZIP validation + Project Discovery
→ canonical CI pipeline starts
→ chat receives live run/report snapshots over SSE
→ agent reasoning, sandbox actions and Evidence are rendered separately
→ deterministic Gate appears in the same conversation
→ follow-up questions are answered from the completed run data
```

The user request is passed into `AgentState` as analysis focus. It can influence what the LLM investigates, but it never expands the registered target or sandbox boundary.

## Uploaded projects

`POST /projects/import` accepts ZIP bytes, never a client filesystem path. The server rejects traversal paths, symbolic links, oversized archives/files and ZIPs with no discovered runnable components. A server-generated `TargetProfile` and conservative architecture description are written under `CFT_API_PROJECT_ROOT` and reloaded after API restart.

The generated architecture file records only Discovery facts. It deliberately does not invent criticality, trust relationships or public exposure.

## Chat behavior

A chat session is permanently attached to one project. The first user message starts a run. While that run is active, additional messages do not start a second sandbox. After completion, ordinary messages become follow-up questions over persisted Gate/FinalReport/Evidence data. `/reanalyze`, `/analyze`, `/scan`, or a natural explicit re-run request starts another analysis for the same project.

The UI never turns LLM prose into Evidence. It renders these as different blocks:

```text
user request
agent status / interpretation
sandbox action
Evidence / observed facts
FinalReport
Gate
```

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

Vite proxies `/api/*` to `http://127.0.0.1:8080/*`, so local development does not require permissive CORS.
