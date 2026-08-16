from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    env: str = "local"
    policy_file: Path = Path("policies/default.yaml")
    target_file: Path = Path("targets/sberlab.yaml")
    max_iterations: int = 5
    target_base_url: str = "http://127.0.0.1:8000"
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

    agent_mode: Literal["stub", "llm"] = "stub"
    agent_model_provider: str = ""
    agent_model_name: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CFT_",
        extra="ignore",
    )


settings = Settings()
