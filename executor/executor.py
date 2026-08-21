import logging
import shutil
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
    DockerSandbox,
    ProcessSandbox,
    RunLimiter,
    Sandbox,
    SandboxRequest,
)
from executor.sandbox_audit import AuditRecord, calculate_sha256_digest
from executor.sandbox_policy import SandboxLimits, SandboxPolicy
from executor.targets import TargetRegistry
from schemas.action import ActionProposal
from schemas.errors import ErrorDetail
from schemas.execution import ExecutionResult
from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

ExecutionStatus = Literal["completed", "failed", "denied"]


class CapabilityInputError(ValueError):
    pass


def _system_error(
    code: Literal[
        "VALIDATION_ERROR",
        "TIMEOUT",
        "PERSISTENCE_ERROR",
        "EXECUTION_FAILED",
    ],
    *,
    layer: Literal["executor", "storage"] = "executor",
    message: str,
    retryable: bool = False,
) -> ErrorDetail:
    return ErrorDetail(
        code=code,
        layer=layer,
        message=message,
        retryable=retryable,
    )

CAPABILITY_REPOSITORY_ACCESS: dict[str, bool] = {
    "safe_noop": False,
    "check_sberlab_health": False,
    "get_sberlab_public_projects": False,
    "inspect_dockerfile_user": True,
    "inspect_python_password_assignment": True,
    "inspect_react_dangerous_html_flow": True,
}

# Определяет, какой сетевой доступ разрешён для каждой capability
CAPABILITY_NETWORK_ACCESS: dict[str, str] = {
    "safe_noop": "none",
    "check_sberlab_health": "target",  # Только к доверенному target
    "get_sberlab_public_projects": "target",
    "inspect_dockerfile_user": "none",  # Только чтение файлов, без сети
    "inspect_python_password_assignment": "none",
    "inspect_react_dangerous_html_flow": "none",
}

class SafeExecutor:
    """Выполняет утвержденные функции в рамках «песочницы» безопасности."""

    def __init__(
        self,
        *,
        approvals: InMemoryApprovalStore,
        targets: TargetRegistry,
        evidence_store: JsonExecutionEvidenceStore,
        audit_log: JsonlAuditLog,
        sandbox: Sandbox,
        run_limiter: RunLimiter,
        policy: SandboxPolicy,
    ) -> None:
        self._approvals = approvals
        self._targets = targets
        self._evidence_store = evidence_store
        self._audit_log = audit_log
        self._sandbox = sandbox
        self._run_limiter = run_limiter
        self._policy = policy
        self._registry = ToolRegistry()
        self._registry.register("safe_noop", self._safe_noop)
        self._registry.register("check_sberlab_health", self._no_parameters)
        self._registry.register("get_sberlab_public_projects", self._no_parameters)
        self._registry.register("inspect_dockerfile_user", self._artifact_id_parameter)
        self._registry.register("inspect_python_password_assignment", self._artifact_id_parameter)
        self._registry.register("inspect_react_dangerous_html_flow", self._react_html_flow_parameters)

    def registered_tools(self) -> tuple[str, ...]:
        return self._registry.names()

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
        target_repository_path: str | Path | None = None,
        backend_override: str | None = None,
    ) -> "SafeExecutor":
        policy_data = yaml.safe_load(Path(policy_file).read_text(encoding="utf-8")) or {}
        policy_limits = policy_data.get("limits", {}).get("executor", {})

        limits = SandboxLimits(
            wall_time_seconds=float(policy_limits.get("wall_time_seconds", 5.0)),
            cpu_time_seconds=int(policy_limits.get("cpu_time_seconds", 2)),
            memory_bytes=int(policy_limits.get("memory_mb", 256)) * 1024 * 1024,
            max_file_bytes=int(policy_limits.get("max_file_bytes", 1024 * 1024)),
            max_processes=int(policy_limits.get("max_processes", 8)),
            max_output_bytes=int(policy_limits.get("max_output_bytes", 16384)),
            max_cpus=float(policy_limits.get("max_cpus", 1.0)),
        )

        backend = backend_override or policy_data.get("runtime", {}).get(
            "backend",
            "process",
        )

        if backend not in {"docker", "process"}:
            raise ValueError(
                f"Unsupported sandbox backend: {backend!r}. "
                "Allowed values: 'docker', 'process'."
            )

        docker_available = shutil.which("docker") is not None

        runtime_policy = policy_data.get("runtime", {})

        sandbox_image = runtime_policy.get(
            "sandbox_image",
            policy_data.get("sandbox_image", ""),
        )

        if backend == "docker" and shutil.which("docker") is None:
            raise RuntimeError(
                "Refusing to fall back to ProcessSandbox because Docker is unavailable"
            )

        policy = SandboxPolicy(
            backend=cast(Literal["docker", "process"], backend),
            network_mode=runtime_policy.get(
                "network_mode",
                policy_data.get("network_mode", "none"),
            ),
            allowed_internal_network=runtime_policy.get(
                "internal_network",
                policy_data.get(
                    "internal_network",
                    "cft_internal_security_net",
                ),
            ),
            allowed_environments=set(
                policy_data.get("environments", {}).get(
                    "allowed",
                    ["local", "sandbox", "staging"],
                )
            ),
            limits=limits,
            sandbox_image=str(sandbox_image),
        )

        policy.validate_for_production(
            is_docker_available=docker_available,
        )

        run_limiter = RunLimiter.shared(
            scope=workspace_directory,
            max_runs_per_action=int(
                policy_limits.get("max_runs_per_action", 1)
            ),
            max_concurrent_runs=int(
                policy_limits.get("max_concurrent_runs", 1)
            ),
        )

        if policy.backend == "docker":
            if not docker_available:
                raise RuntimeError(
                    "Docker sandbox backend is required by policy, "
                    "but the Docker executable is unavailable. "
                    "Refusing to fall back to ProcessSandbox."
                )

            sandbox: Sandbox = DockerSandbox(policy=policy)

        elif policy.backend == "process":
            sandbox = ProcessSandbox(
                workspace_root=workspace_directory,
                limits=limits,
            )

        else:
            raise RuntimeError(
                f"Unsupported sandbox backend after validation: "
                f"{policy.backend!r}"
            )

        return cls(
            approvals=approvals,
            targets=TargetRegistry.from_yaml(
                target_file,
                base_url_override=target_base_url,
                repository_path_override=target_repository_path,
            ),
            evidence_store=JsonExecutionEvidenceStore(evidence_directory),
            audit_log=JsonlAuditLog(audit_log_path),
            sandbox=sandbox,
            run_limiter=run_limiter,
            policy=policy,
        )

    def execute(self, action: ActionProposal) -> ExecutionResult:
        started = perf_counter()
        run_id = uuid4().hex

        approved, approval_reason = self._approvals.check(action)
        if not approved:
            return self._finish(action, run_id, started, status="denied", exit_code=126, stderr=approval_reason, decision_reason=approval_reason)

        try:
            target = self._targets.get(action.target)
        except KeyError:
            message = f"Unknown executor tool: {action.tool}"
            return self._finish(action, run_id, started, status="denied", exit_code=126, stderr=message, decision_reason="Unknown capability",)

        try:
            self._policy.validate_target_environment(target.environment)
        except ValueError as exc:
            return self._finish(action, run_id, started, status="denied", exit_code=126, stderr=str(exc), decision_reason=str(exc))

        try:
            handler = self._registry.get(action.tool)
        except KeyError:
            message = f"Unknown executor tool: {action.tool}"
            return self._finish(action, run_id, started, status="denied", exit_code=126, stderr=message, decision_reason="Unknown capability", )

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
                decision_reason="Capability input validation failed",
                error=_system_error(
                    "VALIDATION_ERROR",
                    message="Capability input validation failed",
                ),
            )
        acquired, limit_reason = self._run_limiter.acquire(action.id)
        if not acquired:
            return self._finish(action, run_id, started, status="denied", exit_code=75, stderr=limit_reason, decision_reason=limit_reason)

        try:
            try:
                network_access = CAPABILITY_NETWORK_ACCESS[action.tool]
                repository_access = CAPABILITY_REPOSITORY_ACCESS[action.tool]
            except KeyError:
                return self._finish(
                    action,
                    run_id,
                    started,
                    status="denied",
                    exit_code=126,
                    stderr=(
                        f"Missing trusted capability policy for: "
                        f"{action.tool}"
                    ),
                    decision_reason="Capability policy missing",
                )

            try:
                sandbox_result = self._sandbox.run(
                    SandboxRequest(
                        run_id=run_id,
                        tool=action.tool,
                        base_url=target.base_url,
                        parameters=parameters,
                        request_timeout_seconds=max(
                            0.1,
                            self._policy.limits.wall_time_seconds * 0.8,
                        ),
                        network_access=network_access,
                        repository_path=(
                            str(target.repository_path)
                            if repository_access and target.repository_path
                            else ""
                        ),
                        artifacts=target.worker_artifacts(),
                    )
                )
            except Exception as exc:
                logger.exception(
                    "Unexpected sandbox failure for action %s",
                    action.id,
                )
                return self._finish(
                    action,
                    run_id,
                    started,
                    status="failed",
                    exit_code=127,
                    stderr="Sandbox failed",
                    decision_reason="Unexpected sandbox error",
                    error=_system_error(
                        "EXECUTION_FAILED",
                        message="Sandbox execution failed",
                    ),
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
            decision_reason="Sandbox execution completed" if sandbox_result.exit_code == 0 else "Sandbox execution failed",
        )

    @staticmethod
    def _json_safe(value):
        if isinstance(value, set):
            return sorted(value)

        if isinstance(value, dict):
            return {
                str(key): SafeExecutor._json_safe(item)
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple)):
            return [
                SafeExecutor._json_safe(item)
                for item in value
            ]

        return value

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
        error: ErrorDetail | None = None,
    ) -> ExecutionResult:
        duration_ms = int((perf_counter() - started) * 1000)
        policy_dict = self._json_safe(asdict(self._policy))

        if status == "failed" and error is None:
            error = (
                _system_error(
                    "TIMEOUT",
                    message="Sandbox execution timed out",
                    retryable=True,
                )
                if timed_out
                else _system_error(
                    "EXECUTION_FAILED",
                    message="Sandbox execution failed",
                )
            )

        evidence_payload = {
            "run_id": run_id,
            "action_id": action.id,
            "tool": action.tool,
            "target": action.target,
            "status": status,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": duration_ms,
            "timed_out": timed_out,
            "workspace_id": workspace_id,
            "limits": self._json_safe(asdict(self._policy.limits)),
            "policy": policy_dict,
            "audit_ref": f"audit:{run_id}",
            "error": error.model_dump(mode="json") if error else None,
        }

        artifacts: list[str] = []
        try:
            evidence_ref, artifact_path = self._evidence_store.put_execution(
                evidence_payload
            )
            artifacts.append(artifact_path)
        except Exception:  # noqa: BLE001 - fail-closed persistence boundary
            logger.exception("Failed to persist executor evidence for action %s", action.id)
            status = "failed"
            exit_code = 1
            error = _system_error(
                "PERSISTENCE_ERROR",
                layer="storage",
                message="Execution evidence could not be persisted",
            )
            stderr = _append_diagnostic(stderr, "Executor evidence persistence failed")
            evidence_ref = f"evidence-unavailable-{uuid4().hex}"
            evidence_payload.update(
                status=status,
                exit_code=exit_code,
                stderr=stderr,
                error=error.model_dump(mode="json"),
            )

        audit_record = AuditRecord.create(
            run_id=run_id,
            action_id=action.id,
            tool=action.tool,
            target=action.target,
            status=status,
            exit_code=exit_code,
            duration_ms=duration_ms,
            action_proposal_dict=(
                action.model_dump()
                if hasattr(action, "model_dump")
                else action.dict()
            ),
            evidence_dict=evidence_payload,
            policy_dict=policy_dict,
            runtime_backend=self._policy.backend,
            network_mode=self._policy.network_mode,
            evidence_ref=evidence_ref,
        )
        try:
            audit_ref = self._audit_log.append(audit_record.to_dict())
        except Exception:  # noqa: BLE001 - fail-closed persistence boundary
            logger.exception("Failed to persist executor audit for action %s", action.id)
            status = "failed"
            exit_code = 1
            error = _system_error(
                "PERSISTENCE_ERROR",
                layer="storage",
                message="Execution audit could not be persisted",
            )
            stderr = _append_diagnostic(stderr, "Executor audit persistence failed")
            audit_ref = f"audit-unavailable:{run_id}"

            final_evidence = {
                **evidence_payload,
                "status": status,
                "exit_code": exit_code,
                "stderr": stderr,
                "audit_ref": audit_ref,
                "error": error.model_dump(mode="json"),
            }
            try:
                evidence_ref, artifact_path = self._evidence_store.put_execution(
                    final_evidence
                )
                artifacts = [artifact_path]
            except Exception:  # noqa: BLE001 - no trusted fallback remains
                logger.exception(
                    "Failed to persist final audit-failure evidence for action %s",
                    action.id,
                )
                evidence_ref = f"evidence-unavailable-{uuid4().hex}"
                artifacts = []

        return ExecutionResult(
            run_id=run_id,
            action_id=action.id,
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            evidence_ref=evidence_ref,
            audit_ref=audit_ref,
            artifacts=artifacts,
            duration_ms=duration_ms,
            error=error,
        )

    @staticmethod
    def _safe_noop(parameters: dict) -> dict:
        allowed = {"message", "test_outcome"}
        unexpected = sorted(set(parameters) - allowed)
        if unexpected:
            raise CapabilityInputError(f"Unsupported safe_noop parameters: {unexpected}")
        outcome = str(parameters.get("test_outcome", "confirmed"))
        if outcome not in {"confirmed", "rejected", "inconclusive"}:
            raise CapabilityInputError("Invalid safe_noop test_outcome")
        return {"message": str(parameters.get("message", "ok"))[:256], "test_outcome": outcome}

    @staticmethod
    def _no_parameters(parameters: dict) -> dict:
        if parameters:
            raise CapabilityInputError("HTTP capabilities do not accept parameters")
        return {}

    @staticmethod
    def _artifact_id_parameter(parameters: dict) -> dict:
        if set(parameters) != {"artifact_id"}:
            raise CapabilityInputError("Requires exactly one artifact_id parameter")
        return {"artifact_id": str(parameters["artifact_id"]).strip()}

    @staticmethod
    def _react_html_flow_parameters(parameters: dict) -> dict:
        required = {"frontend_artifact_id", "model_artifact_id", "serializer_artifact_id", "view_artifact_id", "field"}
        if set(parameters) != required:
            raise CapabilityInputError("React HTML flow requires the fixed parameter set")
        return {k: str(parameters[k]).strip() for k in required}


def _append_diagnostic(stderr: str, message: str) -> str:
    return "\n".join(part for part in (stderr.strip(), message) if part)
