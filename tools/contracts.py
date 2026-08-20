from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from schemas.action import ActionProposal
from schemas.architecture import ArchitectureContext
from schemas.evidence import Evidence
from schemas.finding import Finding
from schemas.scoring import ContextPriority, CVSSResult


class ToolAccess(StrEnum):
    READ_ONLY = "read_only"
    SCORING = "scoring"
    EXECUTION_REQUEST = "execution_request"


class ToolPermission(StrEnum):
    FINDING_READ = "finding:read"
    CODE_READ = "code:read"
    ARCHITECTURE_READ = "architecture:read"
    SCORING_CALCULATE = "scoring:calculate"
    VERIFICATION_REQUEST = "verification:request"
    EVIDENCE_READ = "evidence:read"


class ToolErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    CALCULATION_FAILED = "calculation_failed"
    POLICY_DENIED = "policy_denied"
    EVIDENCE_INVALID = "evidence_invalid"


class FindingLookupInput(BaseModel):
    finding_id: str = Field(min_length=1)


class CodeContextInput(BaseModel):
    file: str = Field(min_length=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    context_lines: int = Field(default=8, ge=0, le=50)


class CodeContextResult(BaseModel):
    file: str
    line_start: int | None = None
    line_end: int | None = None
    content: str


class ArchitectureContextInput(BaseModel):
    service: str = Field(min_length=1)


class CVSSCalculationInput(BaseModel):
    finding: Finding
    metrics: dict[str, str] = Field(
        description=(
            "Explicit CVSS 4.0 metric values selected from known facts. "
            "The deterministic scorer must not invent missing metrics."
        ),
    )


class ContextPriorityInput(BaseModel):
    finding: Finding
    context: ArchitectureContext


class VerificationRequestInput(BaseModel):
    action: ActionProposal


# Запрос проверки только передает proposal в Validator; прямого выполнения здесь нет
class VerificationRequestResult(BaseModel):
    action: ActionProposal
    requires_validator: bool = True


class EvidenceLookupInput(BaseModel):
    evidence_id: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class ToolContract:
    # Контракт фиксирует границу capability для runtime-диспетчера и модели
    name: str
    purpose: str
    access: ToolAccess
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    permissions: tuple[ToolPermission, ...]
    errors: tuple[ToolErrorCode, ...]
    validator_required: bool = False

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "purpose": self.purpose,
            "access": self.access.value,
            "permissions": [permission.value for permission in self.permissions],
            "errors": [error.value for error in self.errors],
            "validator_required": self.validator_required,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
        }


TOOL_CONTRACTS: tuple[ToolContract, ...] = (
    ToolContract(
        name="read_finding",
        purpose="Load one normalized SAST finding by id.",
        access=ToolAccess.READ_ONLY,
        input_model=FindingLookupInput,
        output_model=Finding,
        permissions=(ToolPermission.FINDING_READ,),
        errors=(
            ToolErrorCode.INVALID_INPUT,
            ToolErrorCode.NOT_FOUND,
            ToolErrorCode.UNAVAILABLE,
        ),
    ),
    ToolContract(
        name="read_code_context",
        purpose="Read a bounded source-code window around a finding.",
        access=ToolAccess.READ_ONLY,
        input_model=CodeContextInput,
        output_model=CodeContextResult,
        permissions=(ToolPermission.CODE_READ,),
        errors=(
            ToolErrorCode.INVALID_INPUT,
            ToolErrorCode.NOT_FOUND,
            ToolErrorCode.UNAVAILABLE,
        ),
    ),
    ToolContract(
        name="get_architecture_context",
        purpose="Load architecture context for the affected service.",
        access=ToolAccess.READ_ONLY,
        input_model=ArchitectureContextInput,
        output_model=ArchitectureContext,
        permissions=(ToolPermission.ARCHITECTURE_READ,),
        errors=(
            ToolErrorCode.INVALID_INPUT,
            ToolErrorCode.NOT_FOUND,
            ToolErrorCode.UNAVAILABLE,
        ),
    ),
    ToolContract(
        name="calculate_cvss",
        purpose=(
            "Calculate CVSS 4.0 deterministically from explicit, justified metrics."
        ),
        access=ToolAccess.SCORING,
        input_model=CVSSCalculationInput,
        output_model=CVSSResult,
        permissions=(ToolPermission.SCORING_CALCULATE,),
        errors=(
            ToolErrorCode.INVALID_INPUT,
            ToolErrorCode.CALCULATION_FAILED,
        ),
    ),
    ToolContract(
        name="calculate_context_priority",
        purpose="Calculate project-specific priority from finding and architecture context.",
        access=ToolAccess.SCORING,
        input_model=ContextPriorityInput,
        output_model=ContextPriority,
        permissions=(
            ToolPermission.ARCHITECTURE_READ,
            ToolPermission.SCORING_CALCULATE,
        ),
        errors=(
            ToolErrorCode.INVALID_INPUT,
            ToolErrorCode.CALCULATION_FAILED,
        ),
    ),
    ToolContract(
        name="request_verification",
        purpose=(
            "Submit a structured ActionProposal for Validator review; this tool never "
            "executes the action directly."
        ),
        access=ToolAccess.EXECUTION_REQUEST,
        input_model=VerificationRequestInput,
        output_model=VerificationRequestResult,
        permissions=(ToolPermission.VERIFICATION_REQUEST,),
        errors=(
            ToolErrorCode.INVALID_INPUT,
            ToolErrorCode.POLICY_DENIED,
            ToolErrorCode.UNAVAILABLE,
        ),
        validator_required=True,
    ),
    ToolContract(
        name="read_evidence",
        purpose="Load persisted evidence produced by an approved Executor run.",
        access=ToolAccess.READ_ONLY,
        input_model=EvidenceLookupInput,
        output_model=Evidence,
        permissions=(ToolPermission.EVIDENCE_READ,),
        errors=(
            ToolErrorCode.INVALID_INPUT,
            ToolErrorCode.NOT_FOUND,
            ToolErrorCode.EVIDENCE_INVALID,
            ToolErrorCode.UNAVAILABLE,
        ),
    ),
)

_CONTRACTS_BY_NAME = {contract.name: contract for contract in TOOL_CONTRACTS}


def list_tool_contracts() -> tuple[ToolContract, ...]:
    return TOOL_CONTRACTS


def get_tool_contract(name: str) -> ToolContract:
    try:
        return _CONTRACTS_BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"Unknown tool contract: {name}") from exc


def describe_tool_contracts() -> list[dict[str, object]]:
    return [contract.describe() for contract in TOOL_CONTRACTS]


class FindingReader(Protocol):
    def get_finding(self, finding_id: str) -> Finding: ...


class CodeReader(Protocol):
    def read_code(self, file: str, line_start: int | None, line_end: int | None) -> str: ...


class ArchitectureReader(Protocol):
    def get_context(self, service: str) -> ArchitectureContext: ...


class ScoringTool(Protocol):
    def calculate_cvss(self, finding: Finding) -> CVSSResult: ...

    def calculate_context_priority(
        self,
        finding: Finding,
        context: ArchitectureContext,
    ) -> ContextPriority: ...


class EvidenceReader(Protocol):
    def get_evidence(self, evidence_id: str) -> Evidence: ...


class ActionRequester(Protocol):
    def propose(self, action: ActionProposal) -> ActionProposal: ...
