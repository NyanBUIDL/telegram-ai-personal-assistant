from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus, urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .paths import project_root, user_data_root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TG_ASSISTANT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "production"
    timezone: str = "Asia/Ho_Chi_Minh"
    log_level: str = "INFO"
    data_dir: Path = Field(default_factory=user_data_root)
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
        ):
            raise ValueError("Ollama chỉ được phép dùng API /v1 trên máy cục bộ.")
        return normalized

    @property
    def database_url_without_password(self) -> str:
        return (
            f"mysql+asyncmy://{quote_plus(self.database_user)}@"
            f"{self.database_host}:{self.database_port}/{self.database_name}"
            "?charset=utf8mb4"
        )

    def database_url(self, password: str, *, async_driver: bool = True) -> str:
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
        return self.dashboard_dist_path or project_root() / "dashboard-prototype" / "dist" / "client"

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
            f"{model_slug or 'embedding'}-{self.ollama_vector_size}-"
            f"{version_slug or 'v1'}"
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
    """Persist non-secret settings while preserving unrelated user configuration."""
    path = project_root() / ".env"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefixes = tuple(f"{key}=" for key in values)
    kept = [line for line in existing if not line.startswith(prefixes)]
    additions = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join([*kept, *additions]).strip() + "\n", encoding="utf-8")
    get_settings.cache_clear()
