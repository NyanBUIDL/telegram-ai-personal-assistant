from __future__ import annotations

import json
import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote_plus, urlparse

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

from .paths import (
    ensure_runtime_dirs,
    project_root,
    resolve_data_path,
    resource_path,
    user_data_root,
)

CONFIG_VERSION = 1


def config_path(root: Path | None = None) -> Path:
    return (
        (resolve_data_path(root) if root is not None else user_data_root())
        / "config"
        / "settings.json"
    )


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "settings"}
            or type(value["version"]) is not int
            or value["version"] != CONFIG_VERSION
            or not isinstance(value["settings"], dict)
            or not set(value["settings"]) <= set(Settings.model_fields)
        ):
            raise ValueError
        return value["settings"]
    except (OSError, ValueError):
        raise ValueError("Unsupported or invalid per-user config") from None


def _legacy_config(path: Path | str | None) -> dict[str, str]:
    """Read known non-secret legacy fields only, without dotenv interpolation."""
    if path is None or not Path(path).is_file():
        return {}
    values = {}
    keys = {f"TG_ASSISTANT_{name.upper()}": name for name in Settings.model_fields}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.removeprefix("export ").partition("=")
        field = keys.get(key.strip())
        if separator and field:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[field] = value
    return values


def _infer_mysql(values: dict[str, Any]) -> dict[str, Any]:
    # An explicit legacy connection is a backend choice; untouched defaults are not.
    connection_fields = {"database_host", "database_port", "database_name", "database_user"}
    if "storage_backend" not in values and set(values) & connection_fields:
        values["storage_backend"] = "mysql"
    return values


def validate_settings(settings: Settings) -> Settings:
    """Revalidate internal copies before they may affect files or connections."""
    try:
        return Settings(_env_file=None, **settings.model_dump())
    except (ValidationError, TypeError):
        raise ValueError("Invalid non-secret settings; configuration was not saved") from None


def save_settings(settings: Settings) -> None:
    """Atomically persist validated non-secret settings under the profile root."""
    settings = validate_settings(settings)
    path = config_path(settings.data_dir)
    ensure_runtime_dirs(settings.data_dir, profile_id=settings.profile_id)
    payload = {"version": CONFIG_VERSION, "settings": settings.model_dump(mode="json")}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            temporary = Path(file.name)
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    get_settings.cache_clear()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TG_ASSISTANT_",
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    env: str = "production"
    timezone: str = "Asia/Ho_Chi_Minh"
    log_level: str = "INFO"
    data_dir: Path = Field(default_factory=user_data_root)
    storage_backend: Literal["sqlite", "mysql"] = "sqlite"
    profile_id: str = Field(default="default", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
    admin_api_enabled: bool = True
    admin_api_host: str = "127.0.0.1"
    admin_api_port: int = Field(default=8765, ge=1024, le=65535)
    admin_session_minutes: int = Field(default=480, ge=5, le=1440)
    dashboard_dist_path: Path | None = None

    database_host: str = "127.0.0.1"
    database_port: int = 3306
    database_name: str = "telegram_ai_assistant"
    database_user: str = "tg_assistant"
    database_pool_size: int = 5

    qdrant_path: Path | None = None
    qdrant_vector_size: int = 1536
    media_download_enabled: bool = False
    media_max_size_mb: int = 20
    allowed_media_mime_types: str = "application/pdf,text/plain,text/csv"
    media_retention_hours: int = 24

    daily_ai_budget_usd: float = 1.0
    monthly_ai_budget_usd: float = 20.0
    max_input_tokens_per_request: int = 12_000
    max_output_tokens_per_request: int = 3_000
    max_ai_requests_per_minute: int = 60
    ai_efficiency_preset: Literal["saving", "balanced", "quality"] = "balanced"
    rag_top_k: int = Field(default=8, ge=1, le=50)
    rag_max_chunks_per_source: int = Field(default=3, ge=1, le=20)
    rag_max_sources: int = Field(default=8, ge=1, le=50)
    rag_max_context_tokens: int = Field(default=6_000, ge=500, le=50_000)
    rag_max_chunk_tokens: int = Field(default=500, ge=100, le=4_000)
    daily_token_limit: int = Field(default=1_000_000, ge=1_000)
    monthly_token_limit: int = Field(default=20_000_000, ge=10_000)
    group_daily_token_limit: int = Field(default=150_000, ge=1_000)
    feature_daily_token_limit: int = Field(default=500_000, ge=1_000)
    provider_daily_token_limit: int = Field(default=1_000_000, ge=1_000)
    max_cloud_fallbacks_per_day: int = Field(default=100, ge=0, le=100_000)
    learning_job_interval_seconds: int = 2
    enable_deep_model: bool = False
    enable_embeddings: bool = True
    ai_provider: Literal["openai", "openrouter", "ollama", "off"] = "openai"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_primary_model: str = "gpt-5.6-terra"
    openai_fast_model: str = "gpt-5.6-luna"
    openai_deep_model: str = "gpt-5.6-sol"
    openai_embedding_model: str = "text-embedding-3-small"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_primary_model: str = "openai/gpt-5.6-terra"
    openrouter_embedding_model: str = "openai/text-embedding-3-small"
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_primary_model: str = "qwen3:8b"
    ollama_embedding_model: str = "nomic-embed-text:latest"
    ollama_vector_size: int = 768
    ollama_qdrant_path: Path | None = None
    embedding_provider: Literal["ollama"] = "ollama"
    embedding_version: str = "local-v1"

    confirmation_ttl_seconds: int = 300
    pairing_ttl_seconds: int = 300
    max_bulk_delete: int = 50
    short_memory_turns: int = 12
    short_memory_token_limit: int = 8_000

    def __init__(self, **values: Any) -> None:
        # Compatibility imports use the installation location, never a launch cwd.
        values.setdefault("_env_file", project_root() / ".env")
        super().__init__(**values)

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        def profile_settings():
            initial, environment = init_settings(), env_settings()
            root = initial.get("data_dir", environment.get("data_dir"))
            return _read_config(config_path(Path(root) if root is not None else None))

        return (
            lambda: _infer_mysql(init_settings()),
            lambda: _infer_mysql(env_settings()),
            profile_settings,
            lambda: _infer_mysql(_legacy_config(dotenv_settings.env_file)),
        )

    @field_validator("data_dir", mode="before")
    @classmethod
    def absolute_data_dir(cls, value: Any) -> Path:
        return resolve_data_path(Path(value))

    @field_validator("database_host")
    @classmethod
    def local_database_only(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(
                "Mặc định chỉ cho phép MySQL cục bộ; sửa validator có chủ đích nếu cần."
            )
        return value

    @field_validator("admin_api_host")
    @classmethod
    def local_admin_api_only(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Admin API chỉ được phép lắng nghe trên loopback cục bộ.")
        return value

    @field_validator("openai_base_url")
    @classmethod
    def official_openai_only(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if normalized != "https://api.openai.com/v1":
            raise ValueError("Chỉ cho phép kết nối trực tiếp tới API chính thức của OpenAI.")
        return normalized

    @field_validator("openrouter_base_url")
    @classmethod
    def official_openrouter_only(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if normalized != "https://openrouter.ai/api/v1":
            raise ValueError("Chỉ cho phép kết nối tới API chính thức của OpenRouter.")
        return normalized

    @field_validator("ollama_base_url")
    @classmethod
    def local_ollama_only(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlparse(normalized)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.path.rstrip("/") != "/v1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Ollama chỉ được phép dùng API /v1 trên máy cục bộ.")
        return normalized

    @property
    def database_url_without_password(self) -> str:
        if self.storage_backend == "sqlite":
            return self.database_url("")
        return (
            f"mysql+asyncmy://{quote_plus(self.database_user)}@"
            f"{self.database_host}:{self.database_port}/{self.database_name}"
            "?charset=utf8mb4"
        )

    def database_url(self, password: str, *, async_driver: bool = True) -> str:
        if self.storage_backend == "sqlite":
            return URL.create(
                "sqlite+aiosqlite" if async_driver else "sqlite",
                database=str(self.data_dir / "db" / "assistant.sqlite3"),
            ).render_as_string(hide_password=False)
        driver = "mysql+asyncmy" if async_driver else "mysql+pymysql"
        return (
            f"{driver}://{quote_plus(self.database_user)}:{quote_plus(password)}@"
            f"{self.database_host}:{self.database_port}/{self.database_name}"
            "?charset=utf8mb4"
        )

    @property
    def resolved_qdrant_path(self) -> Path:
        return self.qdrant_path or self.data_dir / "qdrant"

    @property
    def resolved_dashboard_dist_path(self) -> Path:
        return self.dashboard_dist_path or resource_path("dashboard-prototype", "dist", "client")

    @property
    def resolved_active_qdrant_path(self) -> Path:
        if self.ai_provider == "ollama":
            base = self.ollama_qdrant_path or self.data_dir / "qdrant_ollama"
            model_slug = re.sub(
                r"[^a-z0-9]+",
                "-",
                self.ollama_embedding_model.casefold(),
            ).strip("-")[:48]
            return base / f"{model_slug or 'embedding'}-{self.ollama_vector_size}"
        return self.resolved_qdrant_path

    @property
    def resolved_local_embedding_qdrant_path(self) -> Path:
        base = self.ollama_qdrant_path or self.data_dir / "qdrant_local_first"
        model_slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            self.ollama_embedding_model.casefold(),
        ).strip("-")[:48]
        version_slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            self.embedding_version.casefold(),
        ).strip("-")[:24]
        return base / (
            f"{model_slug or 'embedding'}-{self.ollama_vector_size}-{version_slug or 'v1'}"
        )

    @property
    def resolved_semantic_vector_path(self) -> Path:
        """The only vector path used for Local-first semantic operations.

        ``resolved_active_qdrant_path`` remains for backward-compatible config
        inspection only; it is not a runtime resolver because chat provider
        selection must never switch the embedding corpus.
        """
        return self.resolved_local_embedding_qdrant_path

    @property
    def active_qdrant_vector_size(self) -> int:
        if self.ai_provider == "ollama":
            return self.ollama_vector_size
        return self.qdrant_vector_size

    @property
    def ai_secret_name(self) -> str | None:
        if self.ai_provider == "openai":
            return "openai_api_key"
        if self.ai_provider == "openrouter":
            return "openrouter_api_key"
        return None

    @property
    def active_ai_base_url(self) -> str:
        if self.ai_provider == "ollama":
            return self.ollama_base_url
        if self.ai_provider == "openrouter":
            return self.openrouter_base_url
        return self.openai_base_url

    @property
    def active_ai_model(self) -> str:
        if self.ai_provider == "ollama":
            return self.ollama_primary_model
        if self.ai_provider == "openrouter":
            return self.openrouter_primary_model
        return self.openai_primary_model

    @property
    def active_embedding_model(self) -> str:
        """The shared corpus is always embedded locally.

        Chat routing may change between OpenAI, OpenRouter and Ollama, but the
        vector corpus must remain independent from that choice.  Keeping this
        property local also prevents an accidental cloud embedding backfill
        when an owner switches the answer provider from the dashboard.
        """
        return self.ollama_embedding_model

    @property
    def provider_embedding_model(self) -> str:
        """Compatibility model used only when constructing a chat provider client.

        The running application never uses this client to backfill the shared
        corpus; that work is performed by the dedicated local Ollama engine.
        """
        if self.ai_provider == "ollama":
            return self.ollama_embedding_model
        if self.ai_provider == "openrouter":
            return self.openrouter_embedding_model
        return self.openai_embedding_model


@lru_cache
def get_settings() -> Settings:
    return Settings()


def save_settings_env(values: dict[str, str]) -> None:
    """Compatibility writer for known non-secret fields; never writes a .env file."""
    keys = {f"TG_ASSISTANT_{name.upper()}": name for name in Settings.model_fields}
    if not set(values) <= set(keys):
        raise ValueError("Only known non-secret settings may be saved")
    current = Settings()
    updates = {keys[key]: value for key, value in values.items()}
    merged = current.model_dump() | updates
    save_settings(Settings(_env_file=None, **merged))
