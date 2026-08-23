from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HttpCookieObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    secure: bool
    http_only: bool
    same_site: str | None = None


class HttpSurfaceObservationResult(BaseModel):
    """Ограниченные факты GET-запроса без тела ответа."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["cft.http_surface_observation.v1"] = Field(
        validation_alias="schema", serialization_alias="schema"
    )
    endpoint: str
    status_code: int = Field(ge=100, le=599)
    response_category: Literal["success", "redirect", "client_error", "server_error"]
    route_accessible: bool
    health_or_error_response: Literal["health", "error", "other"]
    security_headers: dict[str, str] = Field(default_factory=dict)
    cookies: list[HttpCookieObservation] = Field(default_factory=list)
    cors: dict[str, str] = Field(default_factory=dict)
    redirect_location: str | None = None
    redirect_target_is_local_path: bool | None = None
