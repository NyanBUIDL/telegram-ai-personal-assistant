from __future__ import annotations

from tg_assistant.db import models as _models  # noqa: F401
from tg_assistant.db.base import Base


def test_required_schema_tables_exist() -> None:
    required = {
        "telegram_accounts",
        "telegram_users",
        "telegram_chats",
        "telegram_messages",
        "telegram_message_versions",
        "telegram_attachments",
        "telegram_reactions",
        "sync_states",
        "telegram_chat_policies",
        "telegram_chat_permissions",
        "knowledge_sources",
        "pending_actions",
        "moderation_rules",
        "projects",
        "tasks",
        "task_sources",
        "task_history",
        "reminders",
        "tags",
        "task_tags",
        "ai_memories",
        "ai_memory_sources",
        "ai_memory_candidates",
        "ai_memory_conflicts",
        "ai_conversations",
        "ai_conversation_messages",
        "knowledge_cards",
        "knowledge_card_sources",
        "summaries",
        "ai_usage",
        "app_settings",
        "bot_queries",
        "audit_logs",
        "background_jobs",
        "health_checks",
        "runtime_metrics",
    }
    assert required <= set(Base.metadata.tables)


def test_message_unique_and_fulltext_indexes_declared() -> None:
    table = Base.metadata.tables["telegram_messages"]
    assert any(
        {column.name for column in constraint.columns} == {"chat_id", "message_id"}
        for constraint in table.constraints
        if hasattr(constraint, "columns")
    )
    assert "ft_messages_text" in {index.name for index in table.indexes}
