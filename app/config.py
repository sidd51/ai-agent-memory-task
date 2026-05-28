from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os


def _load_dotenv() -> dict[str, str]:
    env_path = Path(".env")
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _get_value(dotenv_values: dict[str, str], *keys: str, default: str) -> str:
    for key in keys:
        value = os.getenv(key)
        if value is not None and value != "":
            return value
        if key in dotenv_values and dotenv_values[key] != "":
            return dotenv_values[key]
    return default


def _get_int(dotenv_values: dict[str, str], *keys: str, default: int) -> int:
    return int(_get_value(dotenv_values, *keys, default=str(default)))


def _get_float(dotenv_values: dict[str, str], *keys: str, default: float) -> float:
    return float(_get_value(dotenv_values, *keys, default=str(default)))


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    api_host: str
    api_port: int
    database_url: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_temperature: float
    worker_poll_interval_seconds: float
    worker_max_concurrency: int
    memory_relevance_limit: int
    memory_summary_char_threshold: int
    retry_delay_seconds: int


@lru_cache
def get_settings() -> Settings:
    dotenv_values = _load_dotenv()
    return Settings(
        app_name=_get_value(dotenv_values, "APP_NAME", default="AI Agent Memory Task"),
        app_env=_get_value(dotenv_values, "APP_ENV", default="development"),
        api_host=_get_value(dotenv_values, "API_HOST", default="0.0.0.0"),
        api_port=_get_int(dotenv_values, "API_PORT", default=8000),
        database_url=_get_value(dotenv_values, "DATABASE_URL", default="sqlite+aiosqlite:///./agent.db"),
        llm_api_key=_get_value(dotenv_values, "LLM_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", default=""),
        llm_base_url=_get_value(
            dotenv_values,
            "LLM_BASE_URL",
            "GEMINI_BASE_URL",
            "OPENAI_BASE_URL",
            default="https://generativelanguage.googleapis.com/v1beta/openai/",
        ),
        llm_model=_get_value(
            dotenv_values,
            "LLM_MODEL",
            "GEMINI_MODEL",
            "OPENAI_MODEL",
            default="gemini-2.5-flash-lite",
        ),
        llm_temperature=_get_float(dotenv_values, "LLM_TEMPERATURE", default=0.2),
        worker_poll_interval_seconds=_get_float(dotenv_values, "WORKER_POLL_INTERVAL_SECONDS", default=1.5),
        worker_max_concurrency=_get_int(dotenv_values, "WORKER_MAX_CONCURRENCY", default=2),
        memory_relevance_limit=_get_int(dotenv_values, "MEMORY_RELEVANCE_LIMIT", default=8),
        memory_summary_char_threshold=_get_int(dotenv_values, "MEMORY_SUMMARY_CHAR_THRESHOLD", default=4000),
        retry_delay_seconds=_get_int(dotenv_values, "RETRY_DELAY_SECONDS", default=5),
    )
