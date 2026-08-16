# Bounded Executor and sandbox v0.3

## Decision boundary

Executor does not plan, reinterpret intent or call an LLM. It receives one
already approved `ActionProposal` and follows a deterministic path:

```text
approval digest check
→ trusted target and environment lookup
→ registered capability lookup
→ strict parameter validation
→ run-limit reservation
→ isolated worker process
→ bounded stdout/stderr and exit code
→ persistent JSON evidence
→ append-only JSONL audit event
→ structured ExecutionResult
```

No proposal field is used as shell text, executable path, HTTP method or URL.

## Sandbox v0.3

`ProcessSandbox` launches only the repository-owned `executor/worker.py` using
an argv list and `shell=False`. Each run receives:

- a random `run_id`;
- a new temporary working directory under `executor_data/workspaces/`;
- an empty stdin except for one bounded JSON capability request;
- a minimal environment;
- dedicated stdout and stderr files;
- cleanup of the temporary directory after output collection.

The worker contains a second fixed dispatch table for:

```text
safe_noop
check_sberlab_health
get_sberlab_public_projects
```

It is not a generic command runner. Unknown names exit with a failure and no
fallback interpretation.

## Default limits

| Limit | Default | Enforcement |
|---|---:|---|
| Wall time | 5 seconds | parent kills the worker process group |
| CPU time | 2 seconds | `RLIMIT_CPU` on Linux/WSL |
| Address space | 256 MiB | `RLIMIT_AS` on Linux/WSL |
| File size | 1 MiB | `RLIMIT_FSIZE` on Linux/WSL |
| Child processes | 8 | `RLIMIT_NPROC` on Linux/WSL |
| Captured stdout/stderr | 16 KiB each | bounded read and truncation marker |
| Runs per `action_id` | 1 | in-process `RunLimiter` |
| Concurrent runs | 1 | shared in-process semaphore |
| Workflow iterations | 5 | LangGraph state/stop condition |

The settings are explicit `CFT_EXECUTOR_*` variables in `.env.example`.
`policies/default.yaml` also sets upper bounds; environment settings may make a
limit stricter but cannot weaken the policy value.
Linux/WSL is the reference environment for resource limits. This is an MVP
sandbox, not a production security boundary: it does not replace a hardened
container runtime, seccomp profile, namespace isolation or a remote worker.

## Timeout and failure semantics

A wall timeout kills the complete worker process group and returns:

```text
status=failed
exit_code=124
timed_out=true
```

A non-zero worker exit, resource-limit termination, malformed worker response
or sandbox setup problem is also converted into `ExecutionResult(status=failed)`.
These failures do not escape from the Executor node as exceptions.

The agent stores the failed evidence, reevaluates the finding and may issue a
new approved action with a new id. When `max_iterations` is exhausted, the
workflow returns `inconclusive`. One hung or broken run therefore cannot block
or crash the complete pipeline.

## Approval and run limits

`InMemoryApprovalStore` stores a SHA-256 digest of the complete proposal.
Executor refuses a missing approval or any proposal changed after approval.

`RunLimiter` allows one start for the same `action_id` and one concurrent start
by default. Its counters are intentionally process-local for the prototype and
reset when the service restarts. A production implementation should move
approval and quota state to a transactional persistent store.

## Target isolation

The proposal contains the logical target id `sberlab-local`, never a URL. The
trusted target registry resolves its base URL. The worker itself maps capability
names to fixed paths:

```text
check_sberlab_health        → GET /health/
get_sberlab_public_projects → GET /api/projects/
```

Both HTTP capabilities reject every proposal parameter. `production` is absent
from the environment allowlist.

## Evidence and audit lifecycle

Disposable working directories never contain the authoritative result.
Persistent runtime data is split by purpose:

```text
executor_data/
├── evidence/
│   └── execution-<uuid>.json
├── audit/
│   └── executor.jsonl
└── workspaces/
    └── temporary run directories, removed after each run
```

Each evidence JSON contains:

```text
run_id, action_id, tool, target, status, exit_code,
stdout, stderr, timed_out, duration_ms, workspace_id,
limits, audit_ref, created_at
```

Each audit event contains timestamp, decision metadata, `exit_code`, timeout
state and the resulting `evidence_ref`. It intentionally does not duplicate
potentially large stdout/stderr.

`collect_evidence()` creates a fresh `JsonExecutionEvidenceStore`, reads the
saved JSON by its validated `evidence_ref`, checks `run_id` and `action_id`, and
only then gives successful evidence high reliability. A missing or corrupted
file becomes low-reliability evidence without crashing the graph.

Runtime data is excluded by `.gitignore`; it should be mounted to persistent
storage when the agent itself is containerized.

## Explicitly unsupported

Executor has no capability for:

- arbitrary shell or subprocess text from the agent;
- arbitrary Python;
- caller-controlled URLs, paths or HTTP methods;
- production targets;
- automatic privilege escalation;
- unbounded retries or parallel starts.
