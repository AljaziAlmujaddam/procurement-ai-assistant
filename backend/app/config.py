from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # Any OpenAI-compatible chat API: OpenAI (leave base URL empty), xAI, Groq, OpenRouter, ...
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1"
    llm_base_url: str | None = None

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "procurement"
    mongodb_collection: str = "purchase_orders"

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
