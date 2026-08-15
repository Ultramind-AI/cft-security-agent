from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    env: str = "local"
    policy_file: Path = Path("policies/default.yaml")
    target_file: Path = Path("targets/sberlab.yaml")
    max_iterations: int = 5
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CFT_", extra="ignore")

settings = Settings()
