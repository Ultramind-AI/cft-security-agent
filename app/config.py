from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    env: str = "local"
    policy_file: Path = Path("policies/default.yaml")
    target_file: Path = Path("targets/sberlab.yaml")
    max_iterations: int = 5
    agent_wall_clock_seconds: float = 120.0
    target_base_url: str | None = None
    target_repository_path: Path | None = None
    executor_timeout_seconds: float = 5.0
    executor_cpu_time_seconds: int = 2
    executor_memory_mb: int = 256
    executor_max_file_bytes: int = 1_048_576
    executor_max_processes: int = 8
    executor_max_output_bytes: int = 16_384
    executor_max_runs_per_action: int = 1
    executor_max_concurrent_runs: int = 1
    executor_work_dir: Path = Path("executor_data/workspaces")
    evidence_dir: Path = Path("executor_data/evidence")
    executor_audit_log: Path = Path("executor_data/audit/executor.jsonl")

    api_database_path: Path = Path("api_data/cft-security.sqlite3")
    api_artifact_root: Path = Path("artifacts/api-runs")
    api_target_profiles: str = "targets/sberlab.yaml"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8080, ge=1, le=65535)

    agent_mode: Literal["stub", "llm"] = "stub"
    agent_model_provider: str = ""
    agent_model_name: str = ""
    llm_routes: str = ""
    llm_timeout_seconds: float = 25.0
    llm_max_output_tokens: int = 1200
    llm_trace: bool = False

    groq_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GROQ_API_KEY",
    )
    zai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="ZAI_API_KEY",
    )
    mistral_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="MISTRAL_API_KEY",
    )
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
    )
    openrouter_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENROUTER_API_KEY",
    )
    nvidia_nim_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="NVIDIA_NIM_API_KEY",
    )
    cloudflare_api_token: SecretStr | None = Field(
        default=None,
        validation_alias="CLOUDFLARE_API_TOKEN",
    )
    cloudflare_account_id: str = Field(
        default="",
        validation_alias="CLOUDFLARE_ACCOUNT_ID",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CFT_",
        extra="ignore",
    )

    def llm_provider_credentials(self) -> dict[str, str]:
        """Вернуть только настроенные учетные данные провайдеров для транспорта LLM."""
        secret_values = {
            "GROQ_API_KEY": self.groq_api_key,
            "ZAI_API_KEY": self.zai_api_key,
            "MISTRAL_API_KEY": self.mistral_api_key,
            "GEMINI_API_KEY": self.gemini_api_key,
            "OPENROUTER_API_KEY": self.openrouter_api_key,
            "NVIDIA_NIM_API_KEY": self.nvidia_nim_api_key,
            "CLOUDFLARE_API_TOKEN": self.cloudflare_api_token,
        }
        credentials = {
            name: secret.get_secret_value()
            for name, secret in secret_values.items()
            if secret is not None and secret.get_secret_value().strip()
        }
        if self.cloudflare_account_id.strip():
            credentials["CLOUDFLARE_ACCOUNT_ID"] = self.cloudflare_account_id.strip()
        return credentials


settings = Settings()
