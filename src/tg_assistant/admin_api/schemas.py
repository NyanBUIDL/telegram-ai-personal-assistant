from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..ai.router import AI_MODES
from ..db.models import PermissionName
from ..services.ollama import validate_model_name


class LoginRequest(BaseModel):
    code: str = Field(min_length=8, max_length=16)


class GroupActionRequest(BaseModel):
    action_type: Literal[
        "set_chat_allowed",
        "set_chat_permission",
        "apply_permission_template",
        "setup_moderation",
        "set_link_spam_auto_moderation",
        "set_group_ai_ask",
        "sync_chat_history",
        "backfill_chat_history",
        "delete_history_link_posts",
        "enable_group_learning",
    ]
    payload: dict = Field(default_factory=dict)
    preview: str | None = Field(default=None, max_length=4000)
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value: dict) -> dict:
        if len(str(value)) > 20_000:
            raise ValueError("Payload vượt giới hạn an toàn.")
        return value


class HistoryDeletePreviewRequest(BaseModel):
    mode: Literal["all_links", "promotion_links", "keywords", "sender", "images"]
    keyword_terms: str = Field(default="", max_length=1_000)
    sender_query: str = Field(default="", max_length=255)
    select_all: bool = False
    selected_message_ids: list[int] = Field(default_factory=list, max_length=2_500)
    excluded_message_ids: list[int] = Field(default_factory=list, max_length=2_500)

    @field_validator("selected_message_ids", "excluded_message_ids")
    @classmethod
    def unique_message_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("Message ID phải là số dương.")
        return list(dict.fromkeys(value))


class HistoryAiFilterRequest(BaseModel):
    mode: Literal["all_links", "promotion_links", "keywords", "sender", "images"]
    keyword_terms: str = Field(default="", max_length=1_000)
    sender_query: str = Field(default="", max_length=255)
    instruction: str = Field(min_length=3, max_length=1_000)
    message_ids: list[int] = Field(min_length=1, max_length=25)

    @field_validator("message_ids")
    @classmethod
    def unique_filter_message_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("Message ID phải là số dương.")
        return list(dict.fromkeys(value))


class PermissionUpdate(BaseModel):
    permission: PermissionName
    enabled: bool


class AiRouteUpdate(BaseModel):
    mode: str
    preferred_cloud_provider: Literal["openai", "openrouter"] | None = None
    cloud_fallback: bool = False

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in AI_MODES:
            raise ValueError("AI mode không hợp lệ.")
        return value


class AiEfficiencyUpdate(BaseModel):
    """Per-source limits for the local-first retrieval pipeline."""

    preset: Literal["saving", "balanced", "quality"] | None = None
    filtering_level: Literal["relaxed", "standard", "strict"] = "standard"
    rag_top_k: int | None = Field(default=None, ge=1, le=50)
    rag_max_context_tokens: int | None = Field(default=None, ge=500, le=50_000)


class SourceLimitsUpdate(BaseModel):
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    max_messages: int | None = Field(default=None, ge=1, le=10_000_000)
    max_storage_mb: int | None = Field(default=None, ge=1, le=10_000_000)
    max_vectors: int | None = Field(default=None, ge=1, le=10_000_000)


class ProviderUpdate(BaseModel):
    provider: Literal["openai", "openrouter", "ollama", "off"]


class OllamaPullRequest(BaseModel):
    model: str

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        return validate_model_name(value)


class OllamaActivateRequest(BaseModel):
    chat_model: str
    embedding_model: str

    @field_validator("chat_model", "embedding_model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        return validate_model_name(value)


class OllamaDeleteRequest(BaseModel):
    model: str

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        return validate_model_name(value)


class KnowledgeSelectionRequest(BaseModel):
    chat_ids: list[int] = Field(min_length=1, max_length=500)
    limit: int = Field(default=1000, ge=1, le=1000)


class KnowledgeNoteUpdate(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class KnowledgeDeleteRequest(BaseModel):
    scope: Literal[
        "vectors_only",
        "search_index",
        "mysql_content",
        "media_only",
        "all",
        "reset_checkpoint",
    ]
    confirmation: str | None = Field(default=None, max_length=512)


class LeaveChatRequest(BaseModel):
    data_action: Literal["keep", "request_delete"] = "keep"
    acknowledge_admin: bool = False


class DashboardPreferencesUpdate(BaseModel):
    density: Literal["comfortable", "compact"] = "comfortable"
    group_page_size: int = Field(default=10, ge=10, le=100)
    knowledge_page_size: int = Field(default=50, ge=10, le=100)
    visible_group_columns: list[str] = Field(default_factory=list, max_length=20)
    saved_views: list[dict] = Field(default_factory=list, max_length=20)
    always_keep_chat_ids: list[int] = Field(default_factory=list, max_length=500)
    ignored_recommendation_chat_ids: list[int] = Field(default_factory=list, max_length=500)
