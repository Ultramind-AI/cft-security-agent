from typing import Literal

from pydantic import BaseModel, Field

PRFindingClassification = Literal["new", "existing", "affected-by-change"]


class PRFindingContext(BaseModel):
    fingerprint: str
    classification: PRFindingClassification
    base_ref: str
    head_ref: str
    changed_file: bool = False
    changed_lines: list[int] = Field(default_factory=list)
    architecture_context_changed: bool = False


class PRAnalysisSummary(BaseModel):
    base_ref: str
    head_ref: str
    changed_files: list[str] = Field(default_factory=list)
    changed_lines: dict[str, list[int]] = Field(default_factory=dict)
    findings: dict[str, PRFindingContext] = Field(default_factory=dict)
