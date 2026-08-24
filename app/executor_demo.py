import logging
from uuid import uuid4

from app.config import settings
from executor.approvals import InMemoryApprovalStore
from executor.executor import SafeExecutor
from schemas.action import ActionProposal
from schemas.target import TargetProfile
from validator.validator import PolicyValidator


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    profile = TargetProfile.from_yaml(
        settings.target_file,
        repository_path_override=settings.target_repository_path,
        base_url_override=settings.target_base_url,
    )

    action = ActionProposal(
        id=f"executor-demo-health-{uuid4().hex[:8]}",
        tool="observe_http_surface",
        target=profile.id,
        environment=profile.environment,
        iteration=1,
        parameters={},
        service="backend",
        endpoint="/health/",
        purpose="Observe the approved backend health endpoint in the managed sandbox.",
        expected_evidence="Structured bounded HTTP response observation.",
    )

    validator = PolicyValidator.from_yaml(
        settings.policy_file,
        target_profile=profile,
    )
    validation = validator.validate(action)
    approvals = InMemoryApprovalStore()
    if validation.approved:
        approvals.record(action, validation)

    executor = SafeExecutor.from_config(
        approvals=approvals,
        policy_file=settings.policy_file,
        target_profile=profile,
        evidence_directory=settings.evidence_dir,
        audit_log_path=settings.executor_audit_log,
        workspace_directory=settings.executor_work_dir,
        target_base_url=settings.target_base_url,
        timeout_seconds=settings.executor_timeout_seconds,
        cpu_time_seconds=settings.executor_cpu_time_seconds,
        memory_mb=settings.executor_memory_mb,
        max_file_bytes=settings.executor_max_file_bytes,
        max_processes=settings.executor_max_processes,
        max_output_bytes=settings.executor_max_output_bytes,
        max_runs_per_action=settings.executor_max_runs_per_action,
        max_concurrent_runs=settings.executor_max_concurrent_runs,
    )
    result = executor.execute(action)

    print(result.model_dump_json(indent=2))
    raise SystemExit(0 if result.status == "completed" else 1)


if __name__ == "__main__":
    main()
