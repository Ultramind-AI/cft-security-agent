from typing import TypedDict

from schemas.action import ActionProposal
from schemas.agent_outputs import AnalysisResult
from schemas.architecture import ArchitectureContext
from schemas.evidence import Evidence
from schemas.execution import ExecutionResult
from schemas.finding import Finding
from schemas.hypothesis import Hypothesis
from schemas.plan import DynamicPlan, PlanValidationResult
from schemas.report import FinalReport
from schemas.runtime import RuntimeServiceMap
from schemas.scoring import ContextPriority, CVSSResult
from schemas.target import TargetProfile
from schemas.validation import ValidationResult


class AgentState(TypedDict, total=False):
    target_profile: TargetProfile
    runtime_services: RuntimeServiceMap
    finding: Finding
    code_context: str | None
    architecture_context: ArchitectureContext | None

    cvss: CVSSResult | None
    context_priority: ContextPriority | None

    analysis: AnalysisResult | None
    hypothesis: Hypothesis | None
    dynamic_plan: DynamicPlan | None
    plan_validation: PlanValidationResult | None
    proposed_action: ActionProposal | None

    validation: ValidationResult | None
    execution: ExecutionResult | None
    evidence: list[Evidence]

    status: str
    iteration_count: int
    max_iterations: int

    final_report: FinalReport | None
