from uuid import uuid4

from agent.model import get_agent_model
from executor.executor import SafeExecutor
from schemas.architecture import ArchitectureContext
from schemas.evidence import Evidence
from schemas.report import FinalReport
from schemas.scoring import CVSSResult, ContextPriority
from schemas.state import AgentState
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
    context = state["architecture_context"]

    reasons: list[str] = []
    if context.public_exposure:
        reasons.append("public_exposure")
    if context.databases:
        reasons.append("database_connectivity")
    if context.criticality.lower() in {"high", "critical"}:
        reasons.append(f"criticality:{context.criticality.lower()}")

    return {
        "cvss": CVSSResult(
            vector="CVSS:4.0/PLACEHOLDER",
            score=0.0,
            severity="UNASSESSED",
            reasoning=(
                "Test stub. Replace with deterministic CVSS implementation."
            ),
        ),
        "context_priority": ContextPriority(
            level="HIGH" if reasons else "LOW",
            score=None,
            reasons=reasons,
        ),
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
    validator = PolicyValidator.from_yaml("policies/default.yaml")
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
    executor = SafeExecutor()

    execution = executor.execute(
        state["proposed_action"],
        state["validation"],
    )

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

    outcome = str(action.parameters.get("test_outcome", "confirmed"))

    evidence.append(
        Evidence(
            id=f"evidence-{uuid4().hex[:10]}",
            action_id=action.id,
            type="test_result",
            summary=(
                "Controlled verification completed: "
                f"outcome={outcome}, executor_status={execution.status}."
            ),
            artifact_refs=list(execution.artifacts),
            reliability=(
                "high"
                if execution.status == "completed"
                else "low"
            ),
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
    status = state.get("status", "inconclusive")

    allowed_statuses = {
        "confirmed",
        "rejected",
        "inconclusive",
        "policy_blocked",
    }

    if status not in allowed_statuses:
        status = "inconclusive"

    explanations = {
        "confirmed": "Controlled evidence confirmed the test hypothesis.",
        "rejected": "Controlled evidence rejected the test hypothesis.",
        "inconclusive": (
            "Evidence was insufficient before the iteration limit."
        ),
        "policy_blocked": (
            "Validator denied the proposed action according to policy."
        ),
    }

    report = FinalReport(
        finding_id=state["finding"].id,
        status=status,
        cvss=state.get("cvss"),
        context_priority=state.get("context_priority"),
        evidence=list(state.get("evidence", [])),
        explanation=explanations[status],
        iterations=int(state.get("iteration_count", 0)),
    )

    return {
        "final_report": report,
        "status": status,
    }
