"""Runtime-only lab для одного graph run, без live объектов в AgentState."""

from __future__ import annotations

from contextlib import AbstractContextManager
from contextvars import ContextVar

from app.config import settings
from executor.approvals import InMemoryApprovalStore
from executor.executor import SafeExecutor
from schemas.runtime import RuntimeServiceMap
from schemas.target import TargetProfile

_active_executor: ContextVar[SafeExecutor | None] = ContextVar(
    "active_agent_lab_executor",
    default=None,
)


class ManagedAgentLab(AbstractContextManager[SafeExecutor]):
    """Один Docker container на всю цепочку action внутри investigation."""

    def __init__(self, target: TargetProfile, runtime_services: RuntimeServiceMap) -> None:
        self.executor_target = target
        self.runtime_services = runtime_services
        self.executor = SafeExecutor.from_config(
            approvals=InMemoryApprovalStore(),
            policy_file=settings.policy_file,
            target_profile=target,
            evidence_directory=settings.evidence_dir,
            audit_log_path=settings.executor_audit_log,
            workspace_directory=settings.executor_work_dir,
            target_base_url=settings.target_base_url,
            target_repository_path=settings.target_repository_path,
        )
        self._token = None

    def __enter__(self) -> SafeExecutor:
        self._token = _active_executor.set(self.executor)
        return self.executor

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            self.executor.close_managed_lab()
        finally:
            if self._token is not None:
                _active_executor.reset(self._token)
        return False


def active_executor() -> SafeExecutor | None:
    return _active_executor.get()
