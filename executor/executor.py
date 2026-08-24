import logging
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Literal, cast
from uuid import uuid4

import yaml

from evidence.audit import JsonlAuditLog
from evidence.store import JsonExecutionEvidenceStore
from executor.approvals import InMemoryApprovalStore
from executor.runtime_service_map import RuntimeServiceMapBuilder
from executor.sandbox import (
    DockerSandbox,
    ProcessSandbox,
    RunLimiter,
    Sandbox,
    SandboxRequest,
)
from executor.sandbox_audit import AuditRecord
from executor.sandbox_manager import SandboxManager
from executor.sandbox_policy import SandboxLimits, SandboxPolicy
from executor.sandbox_runner import SandboxRunner
from executor.targets import TargetRegistry
from schemas.action import ActionProposal
from schemas.errors import ErrorDetail
from schemas.execution import ExecutionResult
from schemas.runtime import RuntimeServiceMap
from schemas.target import TargetProfile, TargetRuntimeConfig
from security.error_redaction import redact_error_message
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
    "sandbox_command": True,
    "check_sberlab_health": False,
    "get_sberlab_public_projects": False,
    "observe_http_surface": False,
    "inspect_dockerfile_user": True,
    "inspect_python_password_assignment": True,
    "inspect_react_dangerous_html_flow": True,
}

# Определяет, какой сетевой доступ разрешён для каждой capability
CAPABILITY_NETWORK_ACCESS: dict[str, str] = {
    "safe_noop": "none",
    "sandbox_command": "none",
    "check_sberlab_health": "target",  # Только к доверенному target
    "get_sberlab_public_projects": "target",
    "observe_http_surface": "target",
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
        self._registry.register("sandbox_command", self._sandbox_command_parameters)
        self._registry.register("check_sberlab_health", self._no_parameters, endpoint="/health/")
        self._registry.register("get_sberlab_public_projects", self._no_parameters, endpoint="/api/projects/")
        self._registry.register("observe_http_surface", self._no_parameters)
        self._active_runtime_services: RuntimeServiceMap | None = None
        self._registry.register("inspect_dockerfile_user", self._artifact_id_parameter)
        self._registry.register("inspect_python_password_assignment", self._artifact_id_parameter)
        self._registry.register("inspect_react_dangerous_html_flow", self._react_html_flow_parameters)
        self._runner = SandboxRunner(
            approvals=self._approvals,
            registry=self._registry,
            execute_one=self._execute_one,
            deny_one=self._runner_denied,
        )

    def registered_tools(self) -> tuple[str, ...]:
        return self._registry.names()

    @classmethod
    def from_config(
        cls,
        *,
        approvals: InMemoryApprovalStore,
        policy_file: str | Path,
        target_file: str | Path | None = None,
        target_profile: TargetProfile | None = None,
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

        sandbox_image = os.getenv("CFT_SANDBOX_IMAGE") or runtime_policy.get(
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

        if target_profile is not None:
            effective_profile = target_profile
            updates = {}
            if target_repository_path is not None:
                updates["repository_path"] = Path(target_repository_path).expanduser().resolve()
            if target_base_url is not None:
                updates["runtime"] = effective_profile.runtime.model_copy(
                    update={
                        "base_url": TargetRuntimeConfig(
                            base_url=target_base_url
                        ).base_url
                    }
                )
            if updates:
                effective_profile = effective_profile.model_copy(update=updates)
            targets = TargetRegistry([effective_profile])
        elif target_file is not None:
            targets = TargetRegistry.from_yaml(
                target_file,
                base_url_override=target_base_url,
                repository_path_override=target_repository_path,
            )
        else:
            raise ValueError("target_profile or target_file is required")

        return cls(
            approvals=approvals,
            targets=targets,
            evidence_store=JsonExecutionEvidenceStore(evidence_directory),
            audit_log=JsonlAuditLog(audit_log_path),
            sandbox=sandbox,
            run_limiter=run_limiter,
            policy=policy,
        )

    def execute(self, action: ActionProposal) -> ExecutionResult:
        if action.tool == "observe_http_surface":
            return self.execute_runtime_observation(action)
        result = self.execute_sequence([action])
        return result.results[0].execution

    def execute_runtime_observation(self, action: ActionProposal) -> ExecutionResult:
        """HTTP-проверка выполняется только в управляемой sandbox-сессии."""
        run_id = uuid4().hex
        if not isinstance(self._sandbox, DockerSandbox):
            return self._runner_denied(
                action,
                run_id,
                "HTTP runtime observation requires the Docker sandbox backend",
                False,
            )
        if action.service is None or action.endpoint is None:
            return self._runner_denied(
                action,
                run_id,
                "HTTP runtime observation requires an approved service and endpoint",
                False,
            )
        approved, reason = self._approvals.check(action)
        if not approved:
            return self._runner_denied(action, run_id, reason, False)
        try:
            target = self._targets.get(action.target)
        except KeyError:
            return self._runner_denied(action, run_id, "Unknown registered target", False)

        try:
            with SandboxManager(policy=self._policy).open(target) as session:
                runtime_services = RuntimeServiceMapBuilder().build(target, session)
                result = self.execute_sequence([action], runtime_services=runtime_services)
        except Exception:
            logger.exception("Managed runtime observation could not be established")
            return self._finish(
                action,
                run_id,
                perf_counter(),
                status="failed",
                exit_code=127,
                stderr="Managed runtime observation could not be established",
                decision_reason="Managed runtime session failed",
                error=_system_error(
                    "EXECUTION_FAILED",
                    message="Managed runtime observation could not be established",
                ),
            )

        if not result.results:
            return self._runner_denied(
                action,
                run_id,
                "Runtime observation produced no execution result",
                False,
            )
        return result.results[0].execution

    def execute_sequence(
        self,
        actions: list[ActionProposal],
        *,
        runtime_services: RuntimeServiceMap | None = None,
    ):
        requires_target_network = any(
            CAPABILITY_NETWORK_ACCESS.get(action.tool) == "target" for action in actions
        )
        has_sandbox_command = any(action.tool == "sandbox_command" for action in actions)
        if has_sandbox_command and not isinstance(self._sandbox, DockerSandbox):
            return self._runner.run(actions, runtime_services=runtime_services)
        # Generic commands are deliberately networkless. They must never inherit the
        # target Compose network just because a RuntimeServiceMap exists for the run.
        if has_sandbox_command and requires_target_network:
            raise ValueError(
                "sandbox_command cannot share one execution sequence with target-network capabilities"
            )
        # Existing HTTP observation capability remains Docker-only.
        if any(action.tool == "observe_http_surface" for action in actions) and not isinstance(
            self._sandbox, DockerSandbox
        ):
            return self._runner.run(actions, runtime_services=None)
        if (
            runtime_services is None
            or not isinstance(self._sandbox, DockerSandbox)
            or not requires_target_network
        ):
            self._active_runtime_services = runtime_services
            try:
                return self._runner.run(actions, runtime_services=runtime_services)
            finally:
                self._active_runtime_services = None
        if runtime_services.network_name is None:
            raise RuntimeError("RuntimeServiceMap has no trusted sandbox network")
        target = self._targets.get(actions[0].target) if actions else None
        repository = str(target.repository_path) if target and target.repository_path else ""
        original_sandbox = self._sandbox
        with original_sandbox.open_sequence(
            network_name=runtime_services.network_name,
            repository_path=repository,
        ) as runtime:
            self._sandbox = runtime  # type: ignore[assignment]
            self._active_runtime_services = runtime_services
            try:
                result = self._runner.run(actions, runtime_services=runtime_services)
            finally:
                self._sandbox = original_sandbox
                self._active_runtime_services = None
        updated = [
            item.model_copy(update={"runtime_instance_id": runtime.runtime_instance_id})
            for item in result.results
        ]
        return result.model_copy(
            update={"runtime_instance_id": runtime.runtime_instance_id, "results": updated}
        )

    def _execute_one(
        self,
        action: ActionProposal,
        run_id: str,
        session_id: str | None = None,
    ) -> ExecutionResult:
        started = perf_counter()

        approved, approval_reason = self._approvals.check(action)
        if not approved:
            return self._finish(action, run_id, started, status="denied", exit_code=126, stderr=approval_reason, decision_reason=approval_reason, session_id=session_id)

        try:
            target = self._targets.get(action.target)
        except KeyError:
            message = f"Unknown executor tool: {action.tool}"
            return self._finish(action, run_id, started, status="denied", exit_code=126, stderr=message, decision_reason="Unknown capability", session_id=session_id)

        try:
            self._policy.validate_target_environment(target.environment)
        except ValueError as exc:
            return self._finish(action, run_id, started, status="denied", exit_code=126, stderr=str(exc), decision_reason=str(exc), session_id=session_id)

        try:
            handler = self._registry.get(action.tool)
        except KeyError:
            message = f"Unknown executor tool: {action.tool}"
            return self._finish(action, run_id, started, status="denied", exit_code=126, stderr=message, decision_reason="Unknown capability", session_id=session_id)

        if action.tool == "sandbox_command" and not isinstance(self._sandbox, DockerSandbox):
            reason = "sandbox_command requires the Docker security boundary"
            return self._finish(
                action,
                run_id,
                started,
                status="denied",
                exit_code=126,
                stderr=reason,
                decision_reason=reason,
                session_id=session_id,
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
                decision_reason="Capability input validation failed",
                error=_system_error(
                    "VALIDATION_ERROR",
                    message="Capability input validation failed",
                ),
                session_id=session_id,
            )
        acquired, limit_reason = self._run_limiter.acquire(action.id)
        if not acquired:
            return self._finish(action, run_id, started, status="denied", exit_code=75, stderr=limit_reason, decision_reason=limit_reason, session_id=session_id)

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
                    session_id=session_id,
                )

            try:
                base_url = target.base_url
                request_host = None
                if self._active_runtime_services is not None and action.service is not None:
                    service = self._active_runtime_services.services[action.service]
                    base_url = service.address
                    request_host = service.request_host
                sandbox_result = self._sandbox.run(
                    SandboxRequest(
                        run_id=run_id,
                        tool=action.tool,
                        base_url=base_url,
                        endpoint=self._registry.endpoint(action.tool) or action.endpoint,
                        request_host=request_host,
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
            except Exception:
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
                    session_id=session_id,
                )
        finally:
            self._run_limiter.release()

        stdout = sandbox_result.stdout
        stderr = sandbox_result.stderr
        if action.tool == "sandbox_command":
            stdout = redact_error_message(
                stdout,
                max_length=self._policy.limits.max_output_bytes,
            )
            stderr = redact_error_message(
                stderr,
                max_length=self._policy.limits.max_output_bytes,
            )

        return self._finish(
            action,
            run_id,
            started,
            status="completed" if sandbox_result.exit_code == 0 else "failed",
            exit_code=sandbox_result.exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=sandbox_result.timed_out,
            workspace_id=sandbox_result.workspace_id,
            decision_reason="Sandbox execution completed" if sandbox_result.exit_code == 0 else "Sandbox execution failed",
            session_id=session_id,
        )

    def _runner_denied(
        self,
        action: ActionProposal,
        run_id: str,
        reason: str,
        timed_out: bool,
    ) -> ExecutionResult:
        return self._finish(
            action,
            run_id,
            perf_counter(),
            status="failed" if timed_out else "denied",
            exit_code=124 if timed_out else 126,
            stderr=reason,
            timed_out=timed_out,
            decision_reason=reason,
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
        session_id: str | None = None,
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
            "session_id": session_id,
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
            # Старый контракт evidence использует limits сверху, policy остается source of truth
            "limits": policy_dict["limits"],
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
        except Exception:
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
            session_id=session_id,
        )
        try:
            audit_ref = self._audit_log.append(audit_record.to_dict())
        except Exception:
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
            except Exception:
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
    def _sandbox_command_parameters(parameters: dict) -> dict:
        if set(parameters) != {"argv", "cwd"}:
            raise CapabilityInputError("sandbox_command requires exactly argv and cwd")
        argv = parameters.get("argv")
        cwd = parameters.get("cwd")
        if not isinstance(argv, list) or not argv or len(argv) > 32:
            raise CapabilityInputError("sandbox_command argv must contain 1-32 items")
        if any(not isinstance(item, str) or not item or "\x00" in item for item in argv):
            raise CapabilityInputError("sandbox_command argv items must be non-empty strings")
        if any(len(item) > 1024 for item in argv) or sum(len(item) for item in argv) > 8192:
            raise CapabilityInputError("sandbox_command argv exceeds bounded command size")
        if cwd not in {"/target", "/workspace"}:
            raise CapabilityInputError("sandbox_command cwd is outside the disposable lab")
        return {"argv": list(argv), "cwd": cwd}

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
