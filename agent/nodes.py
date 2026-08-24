import json
from datetime import UTC, datetime

from agent.loop import (
    apply_budget_to_reevaluation,
    budget_stop_reason,
    decision_record,
    terminal_evidence_status,
    wall_clock_exhausted,
)
from agent.model import get_agent_model
from agent.planning import DynamicPlanValidator
from app.config import settings
from evidence.interpreter import build_evidence
from evidence.runtime import build_http_surface_evidence
from evidence.store import JsonExecutionEvidenceStore
from executor.approvals import InMemoryApprovalStore
from executor.executor import SafeExecutor
from pipeline.cancellation import check_cancelled
from reporting.builder import build_final_report
from schemas.agent_loop import AgentActionRecord, AgentDecisionRecord
from schemas.agent_outputs import ReevaluationResult
from schemas.architecture import ArchitectureContext
from schemas.state import AgentState
from schemas.target import TargetProfile
from schemas.validation import ValidationResult
from scoring.service import ScoringService
from validator.validator import PolicyValidator


def load_context(state: AgentState) -> dict:
    check_cancelled()
    finding = state["finding"]
    target_profile = state.get("target_profile")
    if target_profile is None:
        target_profile = TargetProfile.from_yaml(
            settings.target_file,
            repository_path_override=settings.target_repository_path,
            base_url_override=settings.target_base_url,
        )

    code_context = state.get("code_context") or (
        f"Synthetic code context for {finding.file}. "
        "Used only to validate orchestration."
    )

    architecture_context = state.get("architecture_context")
    if architecture_context is None:
        service = finding.service or target_profile.resolve_service(finding.file) or "unknown"
        architecture_context = ArchitectureContext(service=service)

    max_iterations = int(state.get("max_iterations", settings.max_iterations))
    max_steps = min(8, max(1, int(state.get("max_steps", max_iterations))))
    runtime_services = state.get("runtime_services")
    return {
        "target_profile": target_profile,
        "code_context": code_context,
        "architecture_context": architecture_context,
        "evidence": list(state.get("evidence", [])),
        "action_history": list(state.get("action_history", [])),
        "decision_history": list(state.get("decision_history", [])),
        "plan_history": list(state.get("plan_history", [])),
        "iteration_count": int(state.get("iteration_count", 0)),
        "max_iterations": max_iterations,
        "max_steps": max_steps,
        "started_at": state.get("started_at") or datetime.now(UTC),
        "wall_clock_budget_seconds": float(
            state.get("wall_clock_budget_seconds", settings.agent_wall_clock_seconds)
        ),
        "sandbox_session_id": (
            runtime_services.session_id
            if runtime_services is not None
            else state.get("sandbox_session_id")
        ),
        "stop_reason": state.get("stop_reason"),
        "status": "context_loaded",
    }


def score_finding(state: AgentState) -> dict:
    check_cancelled()
    cvss, context_priority = ScoringService().score(
        state["finding"],
        state["architecture_context"],
    )
    return {
        "cvss": cvss,
        "context_priority": context_priority,
        "status": "scored",
    }


def guard_agent_budget(state: AgentState) -> dict:
    check_cancelled()
    reason = budget_stop_reason(state)
    if reason is None:
        return {"status": "budget_ok"}

    message = (
        "Agent step budget was exhausted before the next reasoning iteration."
        if reason == "step_budget_exhausted"
        else "Agent wall-clock budget was exhausted before the next reasoning iteration."
    )
    return {
        "status": "inconclusive",
        "stop_reason": reason,
        "decision_history": [
            *state.get("decision_history", []),
            AgentDecisionRecord(
                step=int(state.get("iteration_count", 0)),
                outcome="stop",
                reason=message,
                evidence_ids=[item.id for item in state.get("evidence", [])],
                plan_id=(
                    state["dynamic_plan"].id
                    if state.get("dynamic_plan") is not None
                    else None
                ),
                stop_reason=reason,
            ),
        ],
    }


def analyse(state: AgentState) -> dict:
    check_cancelled()
    model = get_agent_model()
    analysis = model.analyse(state)

    return {
        "analysis": analysis,
        "status": "analysed",
    }


def form_hypothesis(state: AgentState) -> dict:
    check_cancelled()
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
    check_cancelled()
    model = get_agent_model()

    plan = model.build_plan(
        state,
        state["analysis"],
        state["hypothesis"],
    )
    plan_validation = DynamicPlanValidator().validate(plan, state)
    proposal = plan.steps[0].action

    return {
        "dynamic_plan": plan,
        "plan_history": [*state.get("plan_history", []), plan],
        "plan_validation": plan_validation,
        "proposed_action": proposal,
        "iteration_count": proposal.iteration,
        "status": "action_proposed",
    }


def validate_action(state: AgentState) -> dict:
    check_cancelled()
    plan_validation = state.get("plan_validation")
    if plan_validation is not None and not plan_validation.approved:
        validation = ValidationResult(
            approved=False,
            action_id=state["proposed_action"].id,
            reason=plan_validation.reason,
            policy_rules=plan_validation.rules,
        )
        record = AgentActionRecord(
            step=state["proposed_action"].iteration,
            plan_id=state.get("dynamic_plan").id if state.get("dynamic_plan") else None,
            action=state["proposed_action"],
            validation=validation,
        )
        return {
            "validation": validation,
            "action_history": [*state.get("action_history", []), record],
            "stop_reason": "plan_rejected",
            "status": "policy_blocked",
        }

    validator = PolicyValidator.from_yaml(
        settings.policy_file,
        target_profile=state["target_profile"],
    )
    validation = validator.validate(state["proposed_action"])

    if not validation.approved:
        record = AgentActionRecord(
            step=state["proposed_action"].iteration,
            plan_id=state.get("dynamic_plan").id if state.get("dynamic_plan") else None,
            action=state["proposed_action"],
            validation=validation,
        )
        return {
            "validation": validation,
            "action_history": [*state.get("action_history", []), record],
            "stop_reason": "policy_blocked",
            "status": "policy_blocked",
        }

    return {
        "validation": validation,
        "status": "approved",
    }


def execute_action(state: AgentState) -> dict:
    check_cancelled()
    action = state["proposed_action"]
    validation = state["validation"]

    approvals = InMemoryApprovalStore()
    approvals.record(action, validation)
    executor = SafeExecutor.from_config(
        approvals=approvals,
        policy_file=settings.policy_file,
        target_profile=state["target_profile"],
        evidence_directory=settings.evidence_dir,
        audit_log_path=settings.executor_audit_log,
        workspace_directory=settings.executor_work_dir,
        target_base_url=settings.target_base_url,
        target_repository_path=settings.target_repository_path,
        backend_override=("docker" if action.tool == "sandbox_command" else None),
    )

    runtime_services = state.get("runtime_services")
    if runtime_services is None:
        execution = executor.execute(action)
    else:
        # В CI target уже запущен менеджером: второй независимый lifecycle здесь не нужен.
        sequence = executor.execute_sequence(
            [action],
            runtime_services=runtime_services,
        )
        if not sequence.results:
            raise RuntimeError("Sandbox runner returned no action result")
        execution = sequence.results[0].execution

    return {
        "execution": execution,
        "status": (
            "executed"
            if execution.status == "completed"
            else execution.status
        ),
    }


def collect_evidence(state: AgentState) -> dict:
    check_cancelled()
    execution = state["execution"]
    action = state["proposed_action"]
    hypothesis = state.get("hypothesis")
    evidence = list(state.get("evidence", []))

    # Источник истины после песочницы - сохраненная запись, а не объект в памяти
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

    sandbox_session_id = record.get("session_id") if evidence_loaded else None
    if not isinstance(sandbox_session_id, str) or not sandbox_session_id:
        sandbox_session_id = None
    if action.tool == "sandbox_command" and sandbox_session_id is None:
        sandbox_session_id = execution.workspace_id or None

    if action.tool == "observe_http_surface" and evidence_loaded:
        runtime_evidence = build_http_surface_evidence(
            action=action,
            execution=execution,
            record=record,
            artifact_refs=artifact_refs,
            hypothesis_id=(
                hypothesis.id
                if hypothesis is not None
                else f"unlinked-action:{action.id}"
            ),
        )
        if runtime_evidence:
            evidence.extend(runtime_evidence)
        else:
            evidence.append(
                build_evidence(
                    action=action,
                    execution=execution,
                    record=record,
                    evidence_loaded=evidence_loaded,
                    artifact_refs=artifact_refs,
                    hypothesis_id=(
                        hypothesis.id
                        if hypothesis is not None
                        else f"unlinked-action:{action.id}"
                    ),
                    sandbox_session_id=sandbox_session_id,
                )
            )
    else:
        evidence.append(
            build_evidence(
                action=action,
                execution=execution,
                record=record,
                evidence_loaded=evidence_loaded,
                artifact_refs=artifact_refs,
                hypothesis_id=(
                    hypothesis.id
                    if hypothesis is not None
                    else f"unlinked-action:{action.id}"
                ),
                sandbox_session_id=sandbox_session_id,
            )
        )

    matching_evidence_ids = [item.id for item in evidence if item.action_id == action.id]
    action_history = list(state.get("action_history", []))
    if not any(item.action.id == action.id for item in action_history):
        action_history.append(
            AgentActionRecord(
                step=action.iteration,
                plan_id=state.get("dynamic_plan").id if state.get("dynamic_plan") else None,
                action=action,
                validation=state["validation"],
                execution=execution,
                evidence_ids=matching_evidence_ids,
            )
        )

    return {
        "evidence": evidence,
        "action_history": action_history,
        "sandbox_session_id": sandbox_session_id or state.get("sandbox_session_id"),
        "status": "evidence_collected",
    }


def reevaluate(state: AgentState) -> dict:
    check_cancelled()
    terminal = terminal_evidence_status(state)
    if terminal is None and wall_clock_exhausted(state):
        result = ReevaluationResult(
            status="inconclusive",
            explanation="Agent wall-clock budget was exhausted without terminal Evidence.",
        )
        stop_reason = "wall_clock_budget_exhausted"
    else:
        model = get_agent_model()
        result = model.reevaluate(state)
        result, stop_reason = apply_budget_to_reevaluation(state, result)

    decision = decision_record(state, result, stop_reason)
    return {
        "decision_history": [*state.get("decision_history", []), decision],
        "stop_reason": stop_reason,
        "status": result.status,
    }


def build_report(state: AgentState) -> dict:
    check_cancelled()
    report = build_final_report(state)

    return {
        "final_report": report,
        "status": report.status,
    }
