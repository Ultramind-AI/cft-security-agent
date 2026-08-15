from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    env: str = "local"
    policy_file: Path = Path("policies/default.yaml")
    target_file: Path = Path("targets/sberlab.yaml")
    max_iterations: int = 5

    agent_mode: Literal["stub", "llm"] = "stub"
    agent_model_provider: str = ""
    agent_model_name: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CFT_",
        extra="ignore",
    )


settings = Settings()
