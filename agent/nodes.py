import json

from agent.model import get_agent_model
from app.config import settings
from evidence.interpreter import build_evidence
from evidence.store import JsonExecutionEvidenceStore
from executor.approvals import InMemoryApprovalStore
from executor.executor import SafeExecutor
from reporting.builder import build_final_report
from schemas.architecture import ArchitectureContext
from schemas.state import AgentState
from scoring.service import ScoringService
from validator.validator import PolicyValidator


def load_context(state: AgentState) -> dict:
    finding = state["finding"]

    code_context = state.get("code_context") or (
        f"Synthetic code context for {finding.file}. "
        "Used only to validate orchestration."
    )

    architecture_context = state.get("architecture_context")
    if architecture_context is None:
        architecture_context = ArchitectureContext(
            service=finding.service or "backend",
            public_exposure=True,
            criticality="high",
            trust_zone="application",
            connected_services=["database"],
            databases=["database"],
            critical_paths=["backend -> database"],
        )

    return {
        "code_context": code_context,
        "architecture_context": architecture_context,
        "evidence": list(state.get("evidence", [])),
        "iteration_count": int(state.get("iteration_count", 0)),
        "max_iterations": int(state.get("max_iterations", 2)),
        "status": "context_loaded",
    }


def score_finding(state: AgentState) -> dict:
    cvss, context_priority = ScoringService().score(
        state["finding"],
        state["architecture_context"],
    )

    return {
        "cvss": cvss,
        "context_priority": context_priority,
        "status": "scored",
    }


def analyse(state: AgentState) -> dict:
    model = get_agent_model()
    analysis = model.analyse(state)

    return {
        "analysis": analysis,
        "status": "analysed",
    }


def form_hypothesis(state: AgentState) -> dict:
    model = get_agent_model()
    hypothesis = model.form_hypothesis(
        state,
        state["analysis"],
    )

    return {
        "hypothesis": hypothesis,
        "status": "hypothesis_ready",
    }


def propose_action(state: AgentState) -> dict:
    model = get_agent_model()

    proposal = model.propose_action(
        state,
        state["analysis"],
        state["hypothesis"],
    )

    return {
        "proposed_action": proposal,
        "iteration_count": int(state.get("iteration_count", 0)) + 1,
        "status": "action_proposed",
    }


def validate_action(state: AgentState) -> dict:
    validator = PolicyValidator.from_yaml(
        settings.policy_file,
        target_file=settings.target_file,
    )
    validation = validator.validate(state["proposed_action"])

    return {
        "validation": validation,
        "status": (
            "approved"
            if validation.approved
            else "policy_blocked"
        ),
    }


def execute_action(state: AgentState) -> dict:
    action = state["proposed_action"]
    validation = state["validation"]

    approvals = InMemoryApprovalStore()
    approvals.record(action, validation)
    executor = SafeExecutor.from_config(
        approvals=approvals,
        policy_file=settings.policy_file,
        target_file=settings.target_file,
        evidence_directory=settings.evidence_dir,
        audit_log_path=settings.executor_audit_log,
        workspace_directory=settings.executor_work_dir,
        target_base_url=settings.target_base_url,
        target_repository_path=settings.target_repository_path,
        timeout_seconds=settings.executor_timeout_seconds,
        cpu_time_seconds=settings.executor_cpu_time_seconds,
        memory_mb=settings.executor_memory_mb,
        max_file_bytes=settings.executor_max_file_bytes,
        max_processes=settings.executor_max_processes,
        max_output_bytes=settings.executor_max_output_bytes,
        max_runs_per_action=settings.executor_max_runs_per_action,
        max_concurrent_runs=settings.executor_max_concurrent_runs,
    )

    execution = executor.execute(action)

    return {
        "execution": execution,
        "status": (
            "executed"
            if execution.status == "completed"
            else execution.status
        ),
    }


def collect_evidence(state: AgentState) -> dict:
    execution = state["execution"]
    action = state["proposed_action"]
    evidence = list(state.get("evidence", []))

    artifact_refs = list(execution.artifacts)
    if execution.evidence_ref not in artifact_refs:
        artifact_refs.append(execution.evidence_ref)
    if execution.audit_ref not in artifact_refs:
        artifact_refs.append(execution.audit_ref)

    evidence_loaded = False
    record: dict = {}
    try:
        record = JsonExecutionEvidenceStore(settings.evidence_dir).get_execution(
            execution.evidence_ref
        )
        evidence_loaded = (
            record.get("run_id") == execution.run_id
            and record.get("action_id") == action.id
        )
    except (OSError, ValueError, json.JSONDecodeError):
        evidence_loaded = False

    evidence.append(
        build_evidence(
            action=action,
            execution=execution,
            record=record,
            evidence_loaded=evidence_loaded,
            artifact_refs=artifact_refs,
        )
    )

    return {
        "evidence": evidence,
        "status": "evidence_collected",
    }


def reevaluate(state: AgentState) -> dict:
    model = get_agent_model()
    result = model.reevaluate(state)

    return {
        "status": result.status,
    }


def build_report(state: AgentState) -> dict:
    report = build_final_report(state)

    return {
        "final_report": report,
        "status": report.status,
    }
