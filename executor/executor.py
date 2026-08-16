import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Literal, cast
from uuid import uuid4

import yaml

from evidence.audit import JsonlAuditLog
from evidence.store import JsonExecutionEvidenceStore
from executor.approvals import InMemoryApprovalStore
from executor.sandbox import (
    ProcessSandbox,
    RunLimiter,
    Sandbox,
    SandboxLimits,
    SandboxRequest,
)
from executor.targets import TargetRegistry
from schemas.action import ActionProposal
from schemas.execution import ExecutionResult
from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

ExecutionStatus = Literal["completed", "failed", "denied"]
CapabilityHandler = Callable[[dict], dict]


class CapabilityInputError(ValueError):
    pass


def _bounded(text: str, limit: int) -> str:
    if len(text.encode("utf-8")) <= limit:
        return text
    encoded = text.encode("utf-8")[:limit]
    return f"{encoded.decode('utf-8', errors='replace')}\n...[truncated]"


class SafeExecutor:
    """Execute approved capabilities inside a bounded process sandbox."""

    def __init__(
        self,
        *,
        approvals: InMemoryApprovalStore,
        targets: TargetRegistry,
        evidence_store: JsonExecutionEvidenceStore,
        audit_log: JsonlAuditLog,
        sandbox: Sandbox,
        run_limiter: RunLimiter,
        limits: SandboxLimits,
        allowed_environments: set[str],
    ) -> None:
        self._approvals = approvals
        self._targets = targets
        self._evidence_store = evidence_store
        self._audit_log = audit_log
        self._sandbox = sandbox
        self._run_limiter = run_limiter
        self._limits = limits
        self._allowed_environments = set(allowed_environments)
        self._registry = ToolRegistry()
        self._registry.register("safe_noop", self._safe_noop)
        self._registry.register("check_sberlab_health", self._no_parameters)
        self._registry.register("get_sberlab_public_projects", self._no_parameters)

    @classmethod
    def from_config(
        cls,
        *,
        approvals: InMemoryApprovalStore,
        policy_file: str | Path,
        target_file: str | Path,
        evidence_directory: str | Path,
        audit_log_path: str | Path,
        workspace_directory: str | Path,
        target_base_url: str | None = None,
        timeout_seconds: float = 5.0,
        cpu_time_seconds: int = 2,
        memory_mb: int = 256,
        max_file_bytes: int = 1024 * 1024,
        max_processes: int = 8,
        max_output_bytes: int = 16_384,
        max_runs_per_action: int = 1,
        max_concurrent_runs: int = 1,
    ) -> "SafeExecutor":
        policy = yaml.safe_load(Path(policy_file).read_text(encoding="utf-8")) or {}
        allowed_environments = set(
            policy.get("environments", {}).get("allowed", [])
        )
        policy_limits = policy.get("limits", {}).get("executor", {})
        effective_timeout = min(
            timeout_seconds,
            float(policy_limits.get("wall_time_seconds", timeout_seconds)),
        )
        effective_cpu = min(
            cpu_time_seconds,
            int(policy_limits.get("cpu_time_seconds", cpu_time_seconds)),
        )
        effective_memory_mb = min(
            memory_mb,
            int(policy_limits.get("memory_mb", memory_mb)),
        )
        effective_file_bytes = min(
            max_file_bytes,
            int(policy_limits.get("max_file_bytes", max_file_bytes)),
        )
        effective_processes = min(
            max_processes,
            int(policy_limits.get("max_processes", max_processes)),
        )
        effective_output_bytes = min(
            max_output_bytes,
            int(policy_limits.get("max_output_bytes", max_output_bytes)),
        )
        effective_runs_per_action = min(
            max_runs_per_action,
            int(
                policy_limits.get(
                    "max_runs_per_action",
                    max_runs_per_action,
                )
            ),
        )
        effective_concurrent_runs = min(
            max_concurrent_runs,
            int(
                policy_limits.get(
                    "max_concurrent_runs",
                    max_concurrent_runs,
                )
            ),
        )
        limits = SandboxLimits(
            wall_time_seconds=effective_timeout,
            cpu_time_seconds=effective_cpu,
            memory_bytes=effective_memory_mb * 1024 * 1024,
            max_file_bytes=effective_file_bytes,
            max_processes=effective_processes,
            max_output_bytes=effective_output_bytes,
        )
        run_limiter = RunLimiter.shared(
            scope=workspace_directory,
            max_runs_per_action=effective_runs_per_action,
            max_concurrent_runs=effective_concurrent_runs,
        )

        return cls(
            approvals=approvals,
            targets=TargetRegistry.from_yaml(
                target_file,
                base_url_override=target_base_url,
            ),
            evidence_store=JsonExecutionEvidenceStore(evidence_directory),
            audit_log=JsonlAuditLog(audit_log_path),
            sandbox=ProcessSandbox(
                workspace_root=workspace_directory,
                limits=limits,
            ),
            run_limiter=run_limiter,
            limits=limits,
            allowed_environments=allowed_environments,
        )

    def execute(self, action: ActionProposal) -> ExecutionResult:
        started = perf_counter()
        run_id = uuid4().hex

        approved, approval_reason = self._approvals.check(action)
        if not approved:
            return self._finish(
                action,
                run_id,
                started,
                status="denied",
                exit_code=126,
                stderr=approval_reason,
                decision_reason=approval_reason,
            )

        try:
            target = self._targets.get(action.target)
        except KeyError as exc:
            return self._finish(
                action,
                run_id,
                started,
                status="denied",
                exit_code=126,
                stderr=str(exc),
                decision_reason="Unknown target",
            )

        if target.environment not in self._allowed_environments:
            reason = (
                f"Target environment '{target.environment}' is not allowed "
                "for execution"
            )
            return self._finish(
                action,
                run_id,
                started,
                status="denied",
                exit_code=126,
                stderr=reason,
                decision_reason=reason,
            )

        try:
            handler = cast(CapabilityHandler, self._registry.get(action.tool))
        except KeyError:
            reason = f"Unknown executor tool: {action.tool}"
            return self._finish(
                action,
                run_id,
                started,
                status="denied",
                exit_code=126,
                stderr=reason,
                decision_reason=reason,
            )

        try:
            parameters = handler(action.parameters)
        except CapabilityInputError as exc:
            return self._finish(
                action,
                run_id,
                started,
                status="failed",
                exit_code=2,
                stderr=str(exc),
                decision_reason="Capability input rejected",
            )

        acquired, limit_reason = self._run_limiter.acquire(action.id)
        if not acquired:
            return self._finish(
                action,
                run_id,
                started,
                status="denied",
                exit_code=75,
                stderr=limit_reason,
                decision_reason=limit_reason,
            )

        try:
            request_timeout = max(
                0.1,
                min(
                    self._limits.wall_time_seconds * 0.8,
                    self._limits.wall_time_seconds - 0.1,
                ),
            )
            sandbox_result = self._sandbox.run(
                SandboxRequest(
                    run_id=run_id,
                    tool=action.tool,
                    base_url=target.base_url,
                    parameters=parameters,
                    request_timeout_seconds=request_timeout,
                )
            )
        except Exception as exc:
            logger.exception("Sandbox raised unexpectedly for action %s", action.id)
            return self._finish(
                action,
                run_id,
                started,
                status="failed",
                exit_code=127,
                stderr=f"Sandbox failed: {type(exc).__name__}",
                decision_reason="Sandbox failure converted to result",
            )
        finally:
            self._run_limiter.release()

        return self._finish(
            action,
            run_id,
            started,
            status="completed" if sandbox_result.exit_code == 0 else "failed",
            exit_code=sandbox_result.exit_code,
            stdout=sandbox_result.stdout,
            stderr=sandbox_result.stderr,
            timed_out=sandbox_result.timed_out,
            workspace_id=sandbox_result.workspace_id,
            decision_reason=(
                "Sandbox execution completed"
                if sandbox_result.exit_code == 0
                else "Sandbox execution failed"
            ),
        )

    def registered_tools(self) -> tuple[str, ...]:
        return self._registry.names()

    @staticmethod
    def _safe_noop(parameters: dict) -> dict:
        allowed_parameters = {"message", "test_outcome"}
        unexpected = sorted(set(parameters) - allowed_parameters)
        if unexpected:
            raise CapabilityInputError(
                f"Unsupported safe_noop parameters: {unexpected}"
            )
        outcome = str(parameters.get("test_outcome", "confirmed"))
        if outcome not in {"confirmed", "rejected", "inconclusive"}:
            raise CapabilityInputError("Invalid safe_noop test_outcome")
        return {
            "message": str(parameters.get("message", "ok"))[:256],
            "test_outcome": outcome,
        }

    @staticmethod
    def _no_parameters(parameters: dict) -> dict:
        if parameters:
            raise CapabilityInputError(
                "HTTP capabilities do not accept ActionProposal parameters"
            )
        return {}

    def _finish(
        self,
        action: ActionProposal,
        run_id: str,
        started: float,
        *,
        status: ExecutionStatus,
        exit_code: int,
        decision_reason: str,
        stdout: str = "",
        stderr: str = "",
        timed_out: bool = False,
        workspace_id: str = "",
    ) -> ExecutionResult:
        bounded_stdout = _bounded(stdout, self._limits.max_output_bytes)
        bounded_stderr = _bounded(stderr, self._limits.max_output_bytes)
        duration_ms = int((perf_counter() - started) * 1000)
        audit_ref = f"audit:{run_id}"

        try:
            evidence_ref, artifact_path = self._evidence_store.put_execution(
                {
                    "run_id": run_id,
                    "action_id": action.id,
                    "tool": action.tool,
                    "target": action.target,
                    "status": status,
                    "exit_code": exit_code,
                    "stdout": bounded_stdout,
                    "stderr": bounded_stderr,
                    "duration_ms": duration_ms,
                    "timed_out": timed_out,
                    "workspace_id": workspace_id,
                    "audit_ref": audit_ref,
                    "limits": asdict(self._limits),
                }
            )
            artifacts = [artifact_path]
        except Exception:
            logger.exception("Failed to persist executor evidence for %s", action.id)
            evidence_ref = f"evidence-unavailable-{uuid4().hex}"
            artifacts = []
            status = "failed"
            exit_code = 1
            bounded_stderr = _bounded(
                f"{bounded_stderr}\nExecutor evidence persistence failed".strip(),
                self._limits.max_output_bytes,
            )

        try:
            audit_ref = self._audit_log.append(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "event": "executor_result",
                    "run_id": run_id,
                    "action_id": action.id,
                    "tool": action.tool,
                    "target": action.target,
                    "status": status,
                    "exit_code": exit_code,
                    "timed_out": timed_out,
                    "duration_ms": duration_ms,
                    "evidence_ref": evidence_ref,
                    "decision_reason": decision_reason,
                }
            )
        except Exception:
            logger.exception("Failed to append executor audit for %s", action.id)
            audit_ref = f"audit-unavailable:{run_id}"
            status = "failed"
            exit_code = 1
            bounded_stderr = _bounded(
                f"{bounded_stderr}\nExecutor audit persistence failed".strip(),
                self._limits.max_output_bytes,
            )

        logger.info(
            "executor_result run_id=%s action_id=%s tool=%s target=%s "
            "status=%s exit_code=%s duration_ms=%s evidence_ref=%s audit_ref=%s",
            run_id,
            action.id,
            action.tool,
            action.target,
            status,
            exit_code,
            duration_ms,
            evidence_ref,
            audit_ref,
        )

        return ExecutionResult(
            run_id=run_id,
            action_id=action.id,
            status=status,
            exit_code=exit_code,
            stdout=bounded_stdout,
            stderr=bounded_stderr,
            timed_out=timed_out,
            evidence_ref=evidence_ref,
            audit_ref=audit_ref,
            artifacts=artifacts,
            duration_ms=duration_ms,
        )
