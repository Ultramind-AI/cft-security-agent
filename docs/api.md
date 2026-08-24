# Service API v1

T23 wraps the existing `app.ci_pipeline.run_ci_pipeline` flow instead of implementing a second security pipeline.
That keeps CLI and API verdicts aligned: the same Discovery, SandboxManager, SAST, agent loop, FinalReport and deterministic Gate code is used in both entrypoints.

## Boundaries

- The API accepts only a registered `target_id`; clients cannot submit an arbitrary repository or profile path.
- Trusted profiles are loaded from the configured `targets/` root.
- Run/Project/Finding/Evidence metadata is stored in SQLite.
- Large artifacts, reports, telemetry and executor logs stay under the per-run artifact directory and are referenced from metadata.
- API responses do not expose the local artifact directory.
- Runs are placed in a bounded FIFO queue. The scheduler limits worker count,
  concurrent sandboxes and the shared CPU/RAM reservation.
- Cancellation is cooperative during discovery, SAST, the agent loop and sandbox
  commands. A run becomes `cancelled` only after teardown and resource release.

## Configuration

Defaults:

```text
CFT_API_DATABASE_PATH=api_data/cft-security.sqlite3
CFT_API_ARTIFACT_ROOT=artifacts/api-runs
CFT_API_TARGET_PROFILES=targets/sberlab.yaml
CFT_API_HOST=127.0.0.1
CFT_API_PORT=8080
CFT_API_MAX_CONCURRENT_RUNS=2
CFT_API_MAX_CONCURRENT_SANDBOXES=2
CFT_API_TOTAL_CPU_BUDGET=2.0
CFT_API_TOTAL_MEMORY_MB=1024
CFT_API_RUN_CPU=1.0
CFT_API_RUN_MEMORY_MB=512
```

Register multiple trusted targets with a comma-separated list:

```bash
export CFT_API_TARGET_PROFILES="targets/sberlab.yaml,targets/autodealer.yaml"
```

Every listed file must stay under the trusted `targets/` directory. The target repository path itself comes from the trusted `TargetProfile`; it is never accepted from an API request.

## Start

```bash
python -m pip install -e ".[dev]"
cft-security-api
```

The default bind address is loopback-only.

## Endpoints

```text
GET  /health
GET  /projects
GET  /runs
POST /runs
POST /runs/{run_id}/cancel
GET  /runs/{run_id}
GET  /runs/{run_id}/findings
GET  /runs/{run_id}/findings/{finding_id}
GET  /runs/{run_id}/evidence
GET  /runs/{run_id}/timeline
GET  /runs/{run_id}/reports
GET  /runs/{run_id}/reports/{finding_id}
GET  /runs/{run_id}/gate
```

Create a run:

```bash
curl -sS -X POST http://127.0.0.1:8080/runs \
  -H 'content-type: application/json' \
  -d '{"target_id":"sberlab-local","agent_mode":"stub","max_iterations":3}'
```

`POST /runs` returns `202` with a run record. Poll `GET /runs/{run_id}` until
`status` is no longer `queued`, `running` or `cancelling`.

Cancel a queued or active run:

```bash
curl -sS -X POST http://127.0.0.1:8080/runs/<run_id>/cancel
```

A queued run moves directly to `cancelled`. A running run first moves to
`cancelling`; its terminal state becomes `cancelled` with exit code `130` only
after sandbox cleanup. Repeating the request is safe.

Run status is intentionally separate from the security Gate:

```text
status=completed + gate_decision=fail + exit_code=1
```

means the pipeline completed successfully and found a blocking security condition.

```text
status=technical_failure + exit_code=2
```

means the pipeline itself failed before a trustworthy security verdict.
