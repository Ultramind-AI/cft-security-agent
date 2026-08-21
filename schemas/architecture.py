from pydantic import BaseModel, Field


class ProjectServiceDescription(BaseModel):
    type: str = "unknown"
    public: bool = False
    criticality: str = "unknown"
    trust_zone: str | None = None
    connects_to: list[str] = Field(default_factory=list)
    authentication: str = "unknown"
    blast_radius: str = "unknown"


class ProjectDescription(BaseModel):
    services: dict[str, ProjectServiceDescription] = Field(default_factory=dict)


class ArchitectureContext(BaseModel):
    service: str
    public_exposure: bool = False
    criticality: str = "unknown"
    trust_zone: str | None = None
    connected_services: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    critical_paths: list[str] = Field(default_factory=list)
    authentication: str = "unknown"
    blast_radius: str = "unknown"


class ArchitectureContextOverride(BaseModel):
    public_exposure: bool | None = None
    criticality: str | None = None
    trust_zone: str | None = None
    connected_services: list[str] | None = None
    databases: list[str] | None = None
    critical_paths: list[str] | None = None
    authentication: str | None = None
    blast_radius: str | None = None


class ArchitectureOverrides(BaseModel):
    services: dict[str, ArchitectureContextOverride] = Field(default_factory=dict)
