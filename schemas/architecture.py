from pydantic import BaseModel, Field


class ArchitectureContext(BaseModel):
    service: str
    public_exposure: bool = False
    criticality: str = "unknown"
    trust_zone: str | None = None
    connected_services: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    critical_paths: list[str] = Field(default_factory=list)
