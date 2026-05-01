from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


HOME_DIR = Path.home() / ".sparqlgen"
HOME_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str | None = None

    # Local Llama via Ollama's OpenAI-compatible endpoint.
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_api_key: str = "ollama"  # ignored by Ollama, but the SDK requires a value

    sparqlgen_default_model: str = Field(default="gpt-5.4-mini")

    wikidata_user_agent: str = "sparqlgen/0.1 (https://github.com/your/repo)"
    wikidata_sparql_endpoint: str = "https://query.wikidata.org/sparql"

    history_file: Path = HOME_DIR / "history"
    cache_db: Path = HOME_DIR / "cache.sqlite"


settings = Settings()
