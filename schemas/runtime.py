from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.target import validate_request_host


class RuntimeService(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str = "unknown"
    address: str
    ready: bool
    readiness_source: Literal["compose_health", "http_probe"]
    allowed_endpoints: list[str] = Field(default_factory=list)
    request_host: str | None = None
    diagnostic: str = ""

    @field_validator("address")
    @classmethod
    def validate_sandbox_address(cls, value: str) -> str:
        parsed = urlsplit(value)
        host = parsed.hostname
        if parsed.scheme != "http" or not host or parsed.username or parsed.password:
            raise ValueError("Runtime service address must be an internal sandbox URL")
        if host in {"localhost", "127.0.0.1"}:
            raise ValueError("Runtime service address must be an internal sandbox URL")
        try:
            if not ipaddress.ip_address(host).is_private:
                raise ValueError("Runtime service address must be an internal sandbox URL")
        except ValueError:
            if "." in host:
                raise ValueError("Runtime service address must be an internal sandbox URL")
        return value

    @field_validator("request_host")
    @classmethod
    def validate_request_host_value(cls, value: str | None) -> str | None:
        return validate_request_host(value) if value is not None else None


class RuntimeServiceDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    diagnostic: str


class RuntimeServiceMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    network_name: str | None = None
    services: dict[str, RuntimeService] = Field(default_factory=dict)
    diagnostics: list[RuntimeServiceDiagnostic] = Field(default_factory=list)
