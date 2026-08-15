import logging
from uuid import uuid4

from app.config import settings
from executor.approvals import InMemoryApprovalStore
from executor.executor import SafeExecutor
from schemas.action import ActionProposal
from validator.validator import PolicyValidator


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    action = ActionProposal(
        id=f"executor-demo-health-{uuid4().hex[:8]}",
        tool="check_sberlab_health",
        target="sberlab-local",
        parameters={},
        purpose="Check that the approved local SberLab target is ready.",
        expected_evidence="HTTP 200 with status=ok and database=ok.",
    )

    validator = PolicyValidator.from_yaml(settings.policy_file)
    validation = validator.validate(action)
    approvals = InMemoryApprovalStore()
    if validation.approved:
        approvals.record(action, validation)

    executor = SafeExecutor.from_config(
        approvals=approvals,
        policy_file=settings.policy_file,
        target_file=settings.target_file,
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
