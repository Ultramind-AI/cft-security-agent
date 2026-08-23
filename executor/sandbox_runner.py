"""Запуск последовательности в рамках ограниченного, заранее утвержденного пути выполнения в «песочнице»."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from time import monotonic
from uuid import uuid4

from executor.approvals import InMemoryApprovalStore
from schemas.action import ActionProposal
from schemas.execution import ExecutionResult
from schemas.runtime import RuntimeServiceMap
from schemas.sandbox_runner import SandboxActionResult, SandboxRunResult
from security.error_redaction import redact_error_message
from tools.registry import ToolRegistry

ExecuteOne = Callable[[ActionProposal, str, str | None], ExecutionResult]
DenyOne = Callable[[ActionProposal, str, str, bool], ExecutionResult]
_OUTPUT_LIMIT = 16_384


def _bounded_output(value: str) -> str:
    return redact_error_message(value, max_length=_OUTPUT_LIMIT)


class SandboxRunner:
    """Выполняет только одобренные операции с реестром; в случае сбоя выполнение политики фиксированной последовательности прекращается"""

    def __init__(
        self,
        *,
        approvals: InMemoryApprovalStore,
        registry: ToolRegistry,
        execute_one: ExecuteOne,
        deny_one: DenyOne | None = None,
        max_actions: int = 8,
        sequence_timeout: float = 30.0,
    ) -> None:
        if max_actions < 1 or sequence_timeout <= 0:
            raise ValueError("Sandbox runner limits must be positive")
        self._approvals = approvals
        self._registry = registry
        self._execute_one = execute_one
        self._deny_one = deny_one
        self.max_actions = max_actions
        self.sequence_timeout = sequence_timeout

    def run(
        self,
        actions: Sequence[ActionProposal],
        *,
        runtime_services: RuntimeServiceMap | None = None,
    ) -> SandboxRunResult:
        run_id = uuid4().hex
        if len(actions) > self.max_actions:
            return SandboxRunResult(
                run_id=run_id,
                status="denied",
                results=[self._denied(actions[0], run_id, "Sequence action limit exceeded")]
                if actions
                else [],
            )

        started = monotonic()
        results: list[SandboxActionResult] = []
        for action in actions:
            if monotonic() - started > self.sequence_timeout:
                results.append(self._denied(action, run_id, "Sequence timeout exceeded", timed_out=True))
                break
            reason = self._validate(action, runtime_services)
            if reason:
                results.append(self._denied(action, run_id, reason))
                break
            execution = self._execute_one(
                action,
                run_id,
                runtime_services.session_id if runtime_services else None,
            )
            result = SandboxActionResult(
                session_id=runtime_services.session_id if runtime_services else None,
                run_id=execution.run_id,
                action_id=action.id,
                tool=action.tool,
                service=action.service,
                status=execution.status,
                stdout=_bounded_output(execution.stdout),
                stderr=_bounded_output(execution.stderr),
                exit_code=execution.exit_code,
                timed_out=execution.timed_out,
                duration_ms=execution.duration_ms,
                evidence_ref=execution.evidence_ref,
                execution=execution,
            )
            results.append(result)
            if execution.status != "completed":
                break
        status = "completed" if results and all(item.status == "completed" for item in results) else "failed"
        if results and results[0].status == "denied":
            status = "denied"
        return SandboxRunResult(run_id=run_id, status=status, results=results)

    def _validate(self, action: ActionProposal, runtime_services: RuntimeServiceMap | None) -> str:
        approved, reason = self._approvals.check(action)
        if not approved:
            return reason
        try:
            self._registry.get(action.tool)
        except KeyError:
            return f"Unknown executor tool: {action.tool}"
        if action.tool == "observe_http_surface" and (
            runtime_services is None
            or action.service is None
            or action.endpoint is None
        ):
            return (
                "HTTP runtime observation requires a ready RuntimeServiceMap "
                "and an approved service endpoint"
            )
        contract_endpoint = self._registry.endpoint(action.tool)
        if contract_endpoint is not None and action.endpoint is not None and action.endpoint != contract_endpoint:
            return "Action endpoint does not match the capability contract"
        if runtime_services is not None and contract_endpoint is not None and (
            action.service is None or action.endpoint != contract_endpoint
        ):
            return "Runtime capability requires its contracted service and endpoint"
        if action.endpoint is not None or action.service is not None:
            if runtime_services is None or action.service not in runtime_services.services:
                return "Service is not ready in RuntimeServiceMap"
            service = runtime_services.services[action.service]
            endpoint = contract_endpoint or action.endpoint
            if endpoint not in service.allowed_endpoints:
                return "Endpoint is not allowed by RuntimeServiceMap"
            if not service.address.startswith("http://") or "localhost" in service.address:
                return "Runtime service address is not a sandbox address"
        return ""

    def _denied(
        self,
        action: ActionProposal,
        run_id: str,
        reason: str,
        *,
        timed_out: bool = False,
    ) -> SandboxActionResult:
        if self._deny_one is not None:
            execution = self._deny_one(action, run_id, reason, timed_out)
            return SandboxActionResult(
                run_id=run_id,
                action_id=action.id,
                tool=action.tool,
                service=action.service,
                status=execution.status,
                stdout=_bounded_output(execution.stdout),
                stderr=_bounded_output(execution.stderr),
                exit_code=execution.exit_code,
                timed_out=execution.timed_out,
                duration_ms=execution.duration_ms,
                evidence_ref=execution.evidence_ref,
                execution=execution,
            )
        execution = ExecutionResult(
            run_id=run_id,
            action_id=action.id,
            status="denied",
            exit_code=124 if timed_out else 126,
            stderr=redact_error_message(reason),
            timed_out=timed_out,
            evidence_ref=f"evidence-unavailable-{run_id}",
            audit_ref=f"audit-unavailable:{run_id}",
        )
        return SandboxActionResult(
            run_id=run_id,
            action_id=action.id,
            tool=action.tool,
            service=action.service,
            status=execution.status,
            stdout="",
            stderr=execution.stderr,
            exit_code=execution.exit_code,
            timed_out=timed_out,
            duration_ms=0,
            evidence_ref=execution.evidence_ref,
            execution=execution,
        )
