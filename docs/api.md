# Service API v1.1 — runs + chat

The API wraps the canonical `app.ci_pipeline.run_ci_pipeline` flow. Chat and uploads do not create a second security pipeline.

## Boundaries

- Static targets still come from trusted profiles under `targets/`.
- A browser may upload a ZIP, but never an arbitrary server path or profile path.
- Uploaded ZIPs are path/symlink/size checked before extraction.
- Project Discovery creates a server-owned `TargetProfile`; only that generated profile is registered.
- User chat text may focus the investigation but cannot widen target/sandbox scope.
- Run/Project/Finding/Evidence/chat metadata is stored in SQLite.
- Heavy artifacts remain under the per-run artifact directory and local paths are not exposed by the API.
- Runs remain serialized until T29 owns parallel scheduling/cancellation/resource quotas.

## Configuration

```text
CFT_API_DATABASE_PATH=api_data/cft-security.sqlite3
CFT_API_ARTIFACT_ROOT=artifacts/api-runs
CFT_API_PROJECT_ROOT=api_data/projects
CFT_API_MAX_UPLOAD_BYTES=104857600
CFT_API_TARGET_PROFILES=targets/sberlab.yaml
CFT_API_HOST=127.0.0.1
CFT_API_PORT=8080
```

## Main endpoints

```text
GET  /health
GET  /projects
POST /projects/import

GET  /chat/sessions
POST /chat/sessions
GET  /chat/sessions/{session_id}
POST /chat/sessions/{session_id}/messages
GET  /chat/sessions/{session_id}/events

GET  /runs
POST /runs
GET  /runs/{run_id}
GET  /runs/{run_id}/events
GET  /runs/{run_id}/findings
GET  /runs/{run_id}/evidence
GET  /runs/{run_id}/timeline
GET  /runs/{run_id}/reports
GET  /runs/{run_id}/gate
```

ZIP upload uses the raw request body so the service does not need multipart parsing:

```bash
curl -X POST http://127.0.0.1:8080/projects/import \
  -H 'content-type: application/zip' \
  -H 'x-project-filename: project.zip' \
  --data-binary @project.zip
```

The returned project id can be attached to a chat session. The first message starts the same canonical pipeline used by `POST /runs`.

Run status remains separate from the security Gate. `exit_code=1` means a completed security/policy failure; `exit_code=2` means technical pipeline failure.
