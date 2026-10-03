from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin

BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


def uuid4() -> str:
    return str(uuid.uuid4())


class PermissionName(str, enum.Enum):
    READ_MESSAGES = "read_messages"
    SYNC_HISTORY = "sync_history"
    MONITOR_NEW_MESSAGES = "monitor_new_messages"
    SEARCH_MESSAGES = "search_messages"
    SUMMARIZE = "summarize"
    EXTRACT_TASKS = "extract_tasks"
    CREATE_MEMORIES = "create_memories"
    SEND_MESSAGES = "send_messages"
    EDIT_OWN_MESSAGES = "edit_own_messages"
    DELETE_OWN_MESSAGES = "delete_own_messages"
    DELETE_ANY_MESSAGES = "delete_any_messages"
    PIN_MESSAGES = "pin_messages"
    MODERATE_MESSAGES = "moderate_messages"
    DOWNLOAD_MEDIA = "download_media"
    ANALYZE_FILES = "analyze_files"
    AUTO_KNOWLEDGE = "auto_knowledge"
    AUTO_TASK_SUGGESTION = "auto_task_suggestion"
    AUTO_MODERATION = "auto_moderation"
    GROUP_AI_ASK = "group_ai_ask"


class TaskStatus(str, enum.Enum):
    INBOX = "inbox"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    WAITING = "waiting"
    DONE = "done"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class TaskPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TelegramAccount(Base, TimestampMixin):
    __tablename__ = "telegram_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(255))
    phone_masked: Mapped[str | None] = mapped_column(String(32))
    is_owner_paired: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TelegramUser(Base, TimestampMixin):
    __tablename__ = "telegram_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), index=True)
    display_name: Mapped[str | None] = mapped_column(String(512))
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class TelegramChat(Base, TimestampMixin):
    __tablename__ = "telegram_chats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    chat_type: Mapped[str] = mapped_column(String(32), nullable=False)
    account_rights: Mapped[dict | None] = mapped_column(JSON)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TelegramMessage(Base, TimestampMixin):
    __tablename__ = "telegram_messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_telegram_messages_chat_message"),
        Index("ix_messages_chat_date", "chat_id", "sent_at"),
        Index("ix_messages_sender_date", "sender_id", "sent_at"),
        Index("ft_messages_text", "text", mysql_prefix="FULLTEXT"),
    )
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("telegram_chats.chat_id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    text: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    is_outgoing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    embedding_version: Mapped[str | None] = mapped_column(String(64))
    embedding_provider: Mapped[str | None] = mapped_column(String(32))
    embedding_model: Mapped[str | None] = mapped_column(String(160))
    vector_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, index=True
    )
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding_error: Mapped[str | None] = mapped_column(Text)
    embedding_skip_reason: Mapped[str | None] = mapped_column(String(32), index=True)


class TelegramMessageVersion(Base):
    __tablename__ = "telegram_message_versions"
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    telegram_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("telegram_messages.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str | None] = mapped_column(Text)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("telegram_message_id", "version_number"),)


class TelegramAttachment(Base, TimestampMixin):
    __tablename__ = "telegram_attachments"
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    telegram_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("telegram_messages.id", ondelete="CASCADE"), nullable=False
    )
    telegram_file_id: Mapped[str | None] = mapped_column(String(512))
    file_name: Mapped[str | None] = mapped_column(String(512))
    mime_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    local_path: Mapped[str | None] = mapped_column(String(1024))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class TelegramReaction(Base, TimestampMixin):
    __tablename__ = "telegram_reactions"
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    telegram_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("telegram_messages.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    reaction: Mapped[str] = mapped_column(String(64), nullable=False)


class SyncState(Base, TimestampMixin):
    __tablename__ = "sync_states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    last_message_id: Mapped[int | None] = mapped_column(BigInteger)
    last_message_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(32), default="idle", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class TelegramChatPolicy(Base, TimestampMixin):
    __tablename__ = "telegram_chat_policies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    authorization_epoch: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    template: Mapped[str | None] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_memory_action: Mapped[str | None] = mapped_column(String(16))
    ai_mode: Mapped[str] = mapped_column(String(32), default="inherit", nullable=False, index=True)
    preferred_cloud_provider: Mapped[str | None] = mapped_column(String(32))
    cloud_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retention_days: Mapped[int | None] = mapped_column(Integer)
    max_messages: Mapped[int | None] = mapped_column(Integer)
    max_storage_mb: Mapped[int | None] = mapped_column(Integer)
    max_vectors: Mapped[int | None] = mapped_column(Integer)
    ai_efficiency_preset: Mapped[str | None] = mapped_column(String(32))
    filtering_level: Mapped[str] = mapped_column(
        String(32), default="standard", nullable=False
    )
    rag_top_k: Mapped[int | None] = mapped_column(Integer)
    rag_max_context_tokens: Mapped[int | None] = mapped_column(Integer)


class TelegramChatPermission(Base, TimestampMixin):
    __tablename__ = "telegram_chat_permissions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    permission: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (UniqueConstraint("chat_id", "permission"),)


class KnowledgeSource(Base, TimestampMixin):
    """Persistent learning inventory for one Telegram group or channel."""

    __tablename__ = "knowledge_sources"
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), default="not_learned", nullable=False, index=True
    )
    requested_for_learning: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    mysql_message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    text_message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_indexed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    vector_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message_storage_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    media_storage_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    owner_note: Mapped[str | None] = mapped_column(Text)
    last_learned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class VectorStore(Base, TimestampMixin):
    """Registry entry for a derived vector collection; never owns source data."""

    __tablename__ = "vector_stores"
    store_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 512 characters keeps the UTF-8 MySQL unique index below InnoDB's 3072-byte limit.
    path: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    collection: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(160))
    embedding_version: Mapped[str | None] = mapped_column(String(64))
    dimension: Mapped[int | None] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VectorSourceCoverage(Base, TimestampMixin):
    """Observed coverage for a source in one store, retained for audit and UI."""

    __tablename__ = "vector_source_coverage"
    store_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("vector_stores.store_id", ondelete="CASCADE"), primary_key=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mysql_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    eligible_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_vector_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orphan_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    coverage_state: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PendingAction(Base, TimestampMixin):
    __tablename__ = "pending_actions"
    action_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    requested_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    preview: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class ModerationRule(Base, TimestampMixin):
    __tablename__ = "moderation_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), default="detect_only", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": revision}
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), default=TaskStatus.INBOX.value, nullable=False, index=True
    )
    priority: Mapped[str] = mapped_column(
        String(16), default=TaskPriority.MEDIUM.value, nullable=False
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL")
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assignee: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    ai_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TaskSource(Base):
    __tablename__ = "task_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    source_text: Mapped[str | None] = mapped_column(Text)


class TaskHistory(Base):
    __tablename__ = "task_history"
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    old_values: Mapped[dict | None] = mapped_column(JSON)
    new_values: Mapped[dict | None] = mapped_column(JSON)


class Reminder(Base, TimestampMixin):
    __tablename__ = "reminders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE")
    )
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="scheduled", nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)


class TaskTag(Base):
    __tablename__ = "task_tags"
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class AiMemory(Base, TimestampMixin):
    __tablename__ = "ai_memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("ai_memories.id"))
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)


class AiMemorySource(Base):
    __tablename__ = "ai_memory_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ai_memories.id", ondelete="CASCADE"), nullable=False
    )
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    citation: Mapped[str | None] = mapped_column(Text)


class AiMemoryCandidate(Base, TimestampMixin):
    __tablename__ = "ai_memory_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)


class AiMemoryConflict(Base, TimestampMixin):
    __tablename__ = "ai_memory_conflicts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    memory_a_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ai_memories.id"), nullable=False
    )
    memory_b_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ai_memories.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="unresolved", nullable=False)
    resolution: Mapped[str | None] = mapped_column(Text)


class AiConversation(Base, TimestampMixin):
    __tablename__ = "ai_conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiConversationMessage(Base):
    __tablename__ = "ai_conversation_messages"
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeCard(Base, TimestampMixin):
    __tablename__ = "knowledge_cards"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    topic: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    facts: Mapped[list | dict | None] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(32), default="unverified", nullable=False
    )


class KnowledgeCardSource(Base):
    __tablename__ = "knowledge_card_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    knowledge_card_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_cards.id", ondelete="CASCADE"), nullable=False
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Summary(Base, TimestampMixin):
    __tablename__ = "summaries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list | dict | None] = mapped_column(JSON)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    model: Mapped[str | None] = mapped_column(String(160))
    provider: Mapped[str | None] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)


class AiUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32), index=True)
    feature: Mapped[str | None] = mapped_column(String(64), index=True)
    route: Mapped[str | None] = mapped_column(String(64), index=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    embedding_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_local: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))


class AiQueryCache(Base, TimestampMixin):
    """Versioned response cache; prompts and secrets are never stored."""

    __tablename__ = "ai_query_cache"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    normalized_query_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope_chat_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    knowledge_version: Mapped[str] = mapped_column(String(128), nullable=False)
    route: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    feature: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(160))
    response: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list | dict | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppSetting(Base, TimestampMixin):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict | str | int | float | bool | None] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(String(512))


class BotQuery(Base, TimestampMixin):
    __tablename__ = "bot_queries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    query_redacted: Mapped[str] = mapped_column(Text, nullable=False)
    response_redacted: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    details_redacted: Mapped[dict | None] = mapped_column(JSON)
    correlation_id: Mapped[str | None] = mapped_column(String(36), index=True)


class BackgroundJob(Base, TimestampMixin):
    __tablename__ = "background_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class HealthCheck(Base):
    __tablename__ = "health_checks"
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict | None] = mapped_column(JSON)


class RuntimeMetric(Base):
    """Bounded operational telemetry for the scheduler and worker dashboard."""

    __tablename__ = "runtime_metrics"
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    process_id: Mapped[int] = mapped_column(Integer, nullable=False)
    process_name: Mapped[str] = mapped_column(String(128), nullable=False)
    rss_bytes: Mapped[int | None] = mapped_column(BigInteger)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    vram_bytes: Mapped[int | None] = mapped_column(BigInteger)
    data_bytes: Mapped[int | None] = mapped_column(BigInteger)
    vector_bytes: Mapped[int | None] = mapped_column(BigInteger)
    media_bytes: Mapped[int | None] = mapped_column(BigInteger)
    queued_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    running_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    paused_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON)
