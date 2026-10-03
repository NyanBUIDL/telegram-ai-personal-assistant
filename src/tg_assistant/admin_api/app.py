from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import APIKeyCookie
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import __version__
from ..ai.vector import LocalVectorStore
from ..config import Settings
from ..db.base import Database
from ..db.models import (
    AiUsage,
    AppSetting,
    AuditLog,
    BackgroundJob,
    BotQuery,
    HealthCheck,
    KnowledgeSource,
    ModerationRule,
    PendingAction,
    PermissionName,
    RuntimeMetric,
    TelegramAttachment,
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
    TelegramMessage,
)
from ..policy import PolicyEngine
from ..security import contains_secret, redact
from ..services.actions import PendingActionService
from ..services.history_export import (
    encode_csv_row,
    history_csv_header,
    history_csv_row,
    matched_terms,
    matches_history_delete_message,
    parse_search_terms,
    promotion_reasons,
    telegram_post_url,
)
from ..services.jobs import StorageBusy
from ..services.knowledge_inventory import (
    LEARNING_CHAT_TYPES,
    LearningInventoryRow,
    build_learning_inventory_csv,
    knowledge_status_reason,
)
from ..services.maintenance import MaintenanceBusy
from ..services.ollama import OllamaError, OllamaService, format_model_size
from ..services.operations import cleanup_storage
from ..services.vector_reliability import inspect_source_coverage
from .auth import (
    SESSION_COOKIE,
    AdminAuth,
    AdminSession,
    is_loopback_origin,
    login_code_expires_at,
)
from .schemas import (
    AiEfficiencyUpdate,
    AiRouteUpdate,
    DashboardPreferencesUpdate,
    GroupActionRequest,
    HistoryAiFilterRequest,
    HistoryDeletePreviewRequest,
    KnowledgeDeleteRequest,
    KnowledgeNoteUpdate,
    KnowledgeSelectionRequest,
    LeaveChatRequest,
    LoginRequest,
    OllamaActivateRequest,
    OllamaDeleteRequest,
    OllamaPullRequest,
    PermissionUpdate,
    ProviderUpdate,
    SourceLimitsUpdate,
)

PREFERENCES_DEFAULTS = {
    "density": "comfortable",
    "group_page_size": 10,
    "knowledge_page_size": 50,
    "visible_group_columns": [],
    "saved_views": [],
    "always_keep_chat_ids": [],
    "ignored_recommendation_chat_ids": [],
}
DOCUMENTS = {
    "user-guide": ("USER_GUIDE.md", "Hướng dẫn sử dụng theo tác vụ"),
    "readme": ("README.md", "Cài đặt, vận hành và tính năng"),
    "architecture": ("ARCHITECTURE.md", "Kiến trúc backend và trust boundaries"),
    "activity": ("ACTIVITY_DIAGRAM.md", "Luồng Telegram, AI và worker"),
    "security": ("SECURITY.md", "Quy tắc bảo mật và xác nhận"),
    "troubleshooting": ("TROUBLESHOOTING.md", "Chẩn đoán lỗi thường gặp"),
}

PauseAllHandler = Callable[[AsyncSession], Awaitable[int]]
ResumeAllHandler = Callable[[AsyncSession], Awaitable[int]]
ProviderSwitchHandler = Callable[[str], Awaitable[str]]
OllamaActivateHandler = Callable[[str, str, int], Awaitable[str]]
HistoryAiFilterHandler = Callable[
    [AsyncSession, int, str, list[dict[str, object]]], Awaitable[list[dict[str, object]]]
]
HistorySenderLookupHandler = Callable[[str], Awaitable[dict[str, object]]]


@dataclass(slots=True)
class AdminContext:
    database: Database
    policy: PolicyEngine
    owner_id: int
    settings_getter: Callable[[], Settings]
    vectors_getter: Callable[[], LocalVectorStore | None]
    ollama: OllamaService
    ai_switch_handler: ProviderSwitchHandler
    ollama_activate_handler: OllamaActivateHandler
    pause_all_handler: PauseAllHandler
    resume_all_handler: ResumeAllHandler
    paths: dict[str, Path]
    admin_secret: str
    scheduler_getter: Callable[[], Any] | None = None
    history_ai_filter_handler: HistoryAiFilterHandler | None = None
    history_sender_lookup_handler: HistorySenderLookupHandler | None = None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _audit(
    *,
    owner_id: int,
    action: str,
    outcome: str,
    target_type: str | None = None,
    target_id: object | None = None,
    reason: str | None = None,
    details: dict | None = None,
) -> AuditLog:
    return AuditLog(
        occurred_at=datetime.now(UTC),
        actor_id=owner_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        outcome=outcome,
        reason=str(redact(reason)) if reason else None,
        details_redacted=redact(details) if details else None,
    )


def _action_json(action: PendingAction) -> dict:
    return {
        "action_id": action.action_id,
        "action_type": action.action_type,
        "chat_id": str(action.chat_id) if action.chat_id is not None else None,
        "message_id": str(action.message_id) if action.message_id is not None else None,
        "preview": action.preview,
        "reason": action.reason,
        "payload": redact(action.payload or {}),
        "status": action.status,
        "expires_at": _utc(action.expires_at),
        "confirmed_at": _utc(action.confirmed_at),
        "executed_at": _utc(action.executed_at),
        "error": action.error,
        "created_at": _utc(action.created_at),
    }


def _job_json(job: BackgroundJob) -> dict:
    payload = redact(job.payload or {})
    if not isinstance(payload, dict):
        payload = {}
    phase = payload.get("phase")
    if not phase:
        phase = {
            "queued": "queued",
            "running": "syncing",
            "completed": "completed",
            "failed": "failed",
            "paused": "paused",
            "pause_requested": "pausing",
        }.get(job.status, job.status)

    def as_number(value: object) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    processed = (
        as_number(payload.get("processed"))
        or as_number(payload.get("evaluated"))
        or as_number(payload.get("vectors_created"))
        or as_number(payload.get("indexed_count"))
        or as_number(payload.get("synced_messages"))
        or as_number(payload.get("synced_count"))
    )
    total = (
        as_number(payload.get("total"))
        or as_number(payload.get("total_vectors"))
        or as_number(payload.get("total_messages"))
    )
    progress = as_number(payload.get("progress"))
    if job.status == "completed":
        phase = "completed"
    elif job.status == "queued":
        progress = 0.0
        phase = "queued"
    elif job.status == "running" and phase == "syncing":
        if progress is None and processed is not None and total:
            progress = max(1.0, min(50.0, (processed / total) * 50.0))
        elif progress is not None:
            progress = max(1.0, min(50.0, progress))
    elif job.status == "running" and phase == "embedding":
        if progress is None and processed is not None and total:
            progress = max(51.0, min(99.0, 50.0 + (processed / total) * 49.0))
        elif progress is not None:
            progress = max(51.0, min(99.0, progress))
    elif progress is not None:
        progress = max(0.0, min(100.0, progress))

    normalized_progress = round(progress, 1) if progress is not None else None
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "run_after": _utc(job.run_after),
        "locked_by": job.locked_by,
        "locked_at": _utc(job.locked_at),
        "paused_at": _utc(job.paused_at),
        "last_error": str(redact(job.last_error)) if job.last_error else None,
        "payload": payload,
        "phase": phase,
        "progress": normalized_progress,
        "processed": int(processed) if processed is not None else None,
        "total": int(total) if total is not None else None,
        "synced_messages": int(
            as_number(payload.get("synced_messages"))
            or as_number(payload.get("synced_count"))
            or 0
        ),
        "vectors_created": int(
            as_number(payload.get("vectors_created"))
            or as_number(payload.get("indexed_count"))
            or 0
        ),
        "candidates_total": as_number(payload.get("candidates_total")),
        "evaluated": as_number(payload.get("evaluated")),
        "indexed_new": as_number(payload.get("indexed_new")),
        "reused_existing": as_number(payload.get("reused_existing")),
        "filtered": as_number(payload.get("filtered")),
        "duplicate": as_number(payload.get("duplicate")),
        "skipped": as_number(payload.get("skipped")),
        "failed_items": as_number(payload.get("failed")),
        "vectors_before": as_number(payload.get("vectors_before")),
        "vectors_after": as_number(payload.get("vectors_after")),
        "invariant_ok": payload.get("invariant_ok"),
        "duration_ms": as_number(
            payload.get("duration_ms")
            or payload.get("total_duration_ms")
            or payload.get("embedding_duration_ms")
            or payload.get("sync_duration_ms")
        ),
        "created_at": _utc(job.created_at),
        "updated_at": _utc(job.updated_at),
    }


def _ollama_capabilities(model: object) -> list[str]:
    normalized = f"{getattr(model, 'name', '')} {getattr(model, 'family', '') or ''}".casefold()
    embedding = any(
        marker in normalized for marker in ("embed", "nomic", "bge-", "e5-", "gte-")
    )
    if embedding:
        return ["embedding"]
    vision = any(marker in normalized for marker in ("vision", "llava", "vl"))
    return ["chat", "vision"] if vision else ["chat"]


def _preferences_key(owner_id: int) -> str:
    return f"dashboard_preferences:{owner_id}"


async def _preferences(session: AsyncSession, owner_id: int) -> dict:
    row = await session.get(AppSetting, _preferences_key(owner_id))
    stored = row.value if row and isinstance(row.value, dict) else {}
    return {**PREFERENCES_DEFAULTS, **stored}


async def _knowledge_inventory_snapshot(
    session: AsyncSession,
) -> list[LearningInventoryRow]:
    """Read the current inventory without running an expensive full-message reconciliation."""
    rows = (
        await session.execute(
            select(TelegramChat, KnowledgeSource)
            .outerjoin(
                KnowledgeSource,
                KnowledgeSource.chat_id == TelegramChat.chat_id,
            )
            .where(TelegramChat.chat_type.in_(LEARNING_CHAT_TYPES))
            .order_by(TelegramChat.title, TelegramChat.chat_id)
        )
    ).all()
    inventory: list[LearningInventoryRow] = []
    for chat, source in rows:
        if source is None:
            source = KnowledgeSource(
                chat_id=chat.chat_id,
                status="not_learned",
                requested_for_learning=False,
                mysql_message_count=0,
                text_message_count=0,
                last_indexed_count=0,
                vector_count=0,
                message_storage_bytes=0,
                media_storage_bytes=0,
            )
        inventory.append(
            LearningInventoryRow(
                chat=chat,
                source=source,
                reason=knowledge_status_reason(source.status, source.last_error),
            )
        )
    return inventory


def create_admin_app(context: AdminContext) -> FastAPI:
    settings = context.settings_getter()
    auth = AdminAuth(
        context.admin_secret,
        session_minutes=settings.admin_session_minutes,
    )
    pending = PendingActionService(ttl_seconds=settings.confirmation_ttl_seconds)
    cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)
    app = FastAPI(
        title="Telegram AI Personal Assistant Admin API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.admin_context = context
    app.state.admin_auth = auth

    @app.exception_handler(MaintenanceBusy)
    async def maintenance_busy(_request, _error):
        return JSONResponse(
            status_code=503,
            content={
                "code": "maintenance_in_progress",
                "detail": "Ứng dụng đang bảo trì. Hãy thử lại sau.",
            },
        )

    @app.exception_handler(StorageBusy)
    async def storage_busy(_request, _error):
        return JSONResponse(
            status_code=503,
            content={"code": "storage_busy", "detail": "Dữ liệu đang bận. Hãy thử lại sau."},
        )

    @app.exception_handler(StaleDataError)
    async def stale_edit(_request, _error):
        return JSONResponse(
            status_code=409,
            content={
                "code": "stale_edit",
                "detail": "Nội dung đã được cập nhật. Hãy tải lại trước khi lưu.",
            },
        )

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )

    @app.middleware("http")
    async def secure_api_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    async def require_session(
        token: str | None = Depends(cookie),
    ) -> AdminSession:
        session = auth.get_session(token)
        if not session:
            raise HTTPException(status_code=401, detail="Cần đăng nhập owner.")
        return session

    async def require_write_session(
        request: Request,
        session: AdminSession = Depends(require_session),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> AdminSession:
        current_settings = context.settings_getter()
        origin = request.headers.get("origin") or request.headers.get("referer")
        if not is_loopback_origin(origin, port=current_settings.admin_api_port):
            raise HTTPException(status_code=403, detail="Origin không được phép.")
        if not csrf_token or not secrets_compare(csrf_token, session.csrf_token):
            raise HTTPException(status_code=403, detail="CSRF token không hợp lệ.")
        return session

    async def resolve_history_sender_filter(
        db: AsyncSession,
        sender_query: str,
    ) -> dict[int, dict[str, object]]:
        """Resolve names from joined sources and @handles from Telegram on demand."""
        query = sender_query.strip()
        if not query:
            return {}
        needle = query.lstrip("@").casefold()
        identities: dict[int, dict[str, object]] = {}
        if query.lstrip("-").isdigit():
            identities[int(query.lstrip("-"))] = {
                "sender_id": int(query.lstrip("-")),
                "display_name": f"Sender {query.lstrip('-')}",
                "username": None,
                "kind": "account",
            }
        sources = list((await db.scalars(select(TelegramChat))).all())
        for source in sources:
            username = (source.username or "").lstrip("@").casefold()
            title = (source.title or "").casefold()
            if needle not in {username, title} and needle not in title:
                continue
            raw_chat_id = str(source.chat_id)
            sender_id = int(raw_chat_id[4:]) if raw_chat_id.startswith("-100") else abs(source.chat_id)
            label = source.title or (f"@{source.username}" if source.username else f"Sender {sender_id}")
            identities[sender_id] = {
                "sender_id": sender_id,
                "display_name": label,
                "username": source.username,
                "kind": source.chat_type,
            }
        if query.startswith("@") and context.history_sender_lookup_handler:
            try:
                identity = await context.history_sender_lookup_handler(query)
            except ValueError as exc:
                if not identities:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
            else:
                sender_id = int(identity["sender_id"])
                identities[sender_id] = identity
        return identities

    @app.get("/healthz")
    async def public_health() -> dict:
        return {
            "status": "ok",
            "service": "telegram-ai-admin-api",
            "version": __version__,
            "loopback_only": True,
        }

    @app.post("/api/v1/auth/login")
    async def login(payload: LoginRequest, request: Request, response: Response) -> dict:
        client = request.client.host if request.client else "loopback"
        if client not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(status_code=403, detail="Chỉ cho phép đăng nhập từ máy cục bộ.")
        if not auth.allow_login_attempt(client):
            raise HTTPException(status_code=429, detail="Thử đăng nhập quá nhiều; chờ một phút.")
        if not auth.validate_login_code(payload.code):
            raise HTTPException(status_code=401, detail="Mã đăng nhập sai hoặc đã hết hạn.")
        admin_session = auth.create_session(context.owner_id)
        response.set_cookie(
            SESSION_COOKIE,
            admin_session.token,
            httponly=True,
            secure=False,
            samesite="strict",
            max_age=int(auth.session_ttl.total_seconds()),
            path="/",
        )
        async with context.database.session() as db:
            db.add(_audit(owner_id=context.owner_id, action="dashboard_login", outcome="success"))
        return {
            "authenticated": True,
            "owner_id": str(context.owner_id),
            "csrf_token": admin_session.csrf_token,
            "expires_at": admin_session.expires_at,
        }

    @app.get("/api/v1/auth/session")
    async def session_status(session: AdminSession = Depends(require_session)) -> dict:
        return {
            "authenticated": True,
            "owner_id": str(session.owner_id),
            "csrf_token": session.csrf_token,
            "expires_at": session.expires_at,
        }

    @app.post("/api/v1/auth/logout")
    async def logout(
        response: Response,
        token: str | None = Depends(cookie),
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        auth.revoke_session(token)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"authenticated": False}

    @app.get("/api/v1/auth/code-status")
    async def code_status(_session: AdminSession = Depends(require_session)) -> dict:
        return {"next_code_expires_at": login_code_expires_at()}

    @app.get("/api/v1/overview")
    async def overview(_session: AdminSession = Depends(require_session)) -> dict:
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        async with context.database.session() as db:
            messages = int(
                (
                    await db.scalar(
                        select(func.count(TelegramMessage.id)).where(
                            TelegramMessage.sent_at >= cutoff,
                            TelegramMessage.is_deleted.is_(False),
                        )
                    )
                )
                or 0
            )
            asks = int(
                (
                    await db.scalar(
                        select(func.count(BotQuery.id)).where(BotQuery.created_at >= cutoff)
                    )
                )
                or 0
            )
            pending_count = int(
                (
                    await db.scalar(
                        select(func.count(PendingAction.action_id)).where(
                            PendingAction.status == "pending",
                            PendingAction.expires_at > datetime.now(UTC),
                        )
                    )
                )
                or 0
            )
            blocked = int(
                (
                    await db.scalar(
                        select(func.count(AuditLog.id)).where(
                            AuditLog.occurred_at >= cutoff,
                            AuditLog.outcome.in_(["blocked", "denied", "failed"]),
                        )
                    )
                )
                or 0
            )
            group_count = int(
                (
                    await db.scalar(
                        select(func.count(TelegramChat.chat_id)).where(
                            TelegramChat.chat_type.in_(("group", "supergroup", "channel"))
                        )
                    )
                )
                or 0
            )
            metric = await db.scalar(
                select(RuntimeMetric).order_by(RuntimeMetric.collected_at.desc()).limit(1)
            )
            latest_health = list(
                (
                    await db.scalars(
                        select(HealthCheck).order_by(HealthCheck.checked_at.desc()).limit(50)
                    )
                ).all()
            )
        health = []
        seen_components: set[str] = set()
        for row in latest_health:
            if row.component in seen_components:
                continue
            seen_components.add(row.component)
            health.append(
                {
                    "component": row.component,
                    "status": row.status,
                    "latency_ms": row.latency_ms,
                    "checked_at": _utc(row.checked_at),
                }
            )
        current_settings = context.settings_getter()
        return {
            "window": "24h",
            "messages": messages,
            "ask_requests": asks,
            "pending_actions": pending_count,
            "blocked_or_failed": blocked,
            "joined_sources": group_count,
            "ai_provider": current_settings.ai_provider,
            "ai_model": current_settings.active_ai_model,
            "runtime": (
                {
                    "collected_at": _utc(metric.collected_at),
                    "pid": metric.process_id,
                    "rss_bytes": metric.rss_bytes,
                    "cpu_percent": metric.cpu_percent,
                    "vram_bytes": metric.vram_bytes,
                    "data_bytes": metric.data_bytes,
                    "vector_bytes": metric.vector_bytes,
                    "media_bytes": metric.media_bytes,
                    "queued_jobs": metric.queued_jobs,
                    "running_jobs": metric.running_jobs,
                    "paused_jobs": metric.paused_jobs,
                    "children": (metric.details or {}).get("children", []),
                }
                if metric
                else None
            ),
            "health": health,
            "mock": False,
        }

    @app.get("/api/v1/groups")
    async def groups(
        category: str = Query(default="all", pattern="^(ai|permissions|all)$"),
        query: str = Query(default="", max_length=200),
        chat_type: str | None = Query(default=None, pattern="^(group|supergroup|channel)$"),
        policy_status: str | None = Query(default=None, pattern="^(allow|block)$"),
        learning_status: str | None = Query(default=None, max_length=32),
        activity_state: str | None = Query(
            default=None, pattern="^(active|inactive|unknown)$"
        ),
        inactive_days: int = Query(default=60, ge=1, le=3650),
        sort_by: str = Query(
            default="last_seen",
            pattern="^(last_seen|title|type|policy|learning|messages|vectors|activity)$",
        ),
        sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=10, ge=1, le=100),
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        ai_ids = select(TelegramChatPermission.chat_id).where(
            TelegramChatPermission.permission == PermissionName.GROUP_AI_ASK.value,
            TelegramChatPermission.enabled.is_(True),
        )
        permission_ids = select(TelegramChatPermission.chat_id).where(
            TelegramChatPermission.enabled.is_(True)
        )
        conditions = [TelegramChat.chat_type.in_(("group", "supergroup", "channel"))]
        if category == "ai":
            conditions.append(TelegramChat.chat_id.in_(ai_ids))
        elif category == "permissions":
            conditions.extend(
                [
                    TelegramChat.chat_id.in_(permission_ids),
                    TelegramChat.chat_id.not_in(ai_ids),
                ]
            )
        if query.strip():
            normalized_query = query.strip()
            username_query = normalized_query.lstrip("@")
            pattern = f"%{normalized_query}%"
            username_pattern = f"%{username_query}%"
            conditions.append(
                or_(
                    TelegramChat.title.ilike(pattern),
                    TelegramChat.username.ilike(username_pattern),
                    cast(TelegramChat.chat_id, String).like(pattern),
                )
            )
        if chat_type:
            conditions.append(TelegramChat.chat_type == chat_type)
        async with context.database.session() as db:
            chats = list(
                (
                    await db.scalars(
                        select(TelegramChat)
                        .where(*conditions)
                        .order_by(TelegramChat.chat_id)
                    )
                ).all()
            )
            all_items = await _group_rows(db, chats)
        if policy_status:
            allowed = policy_status == "allow"
            all_items = [
                item for item in all_items if bool(item["policy"]["allowed"]) is allowed
            ]
        if learning_status:
            all_items = [
                item
                for item in all_items
                if item["knowledge"]["status"] == learning_status
            ]
        if activity_state:
            all_items = [
                item
                for item in all_items
                if _activity_state(item, inactive_days) == activity_state
            ]
        sorters = {
            "last_seen": lambda item: item.get("last_seen_at"),
            "title": lambda item: (item.get("title") or "").casefold(),
            "type": lambda item: item.get("chat_type") or "",
            "policy": lambda item: bool(item["policy"]["allowed"]),
            "learning": lambda item: item["knowledge"]["status"],
            "messages": lambda item: item["knowledge"]["mysql_message_count"],
            "vectors": lambda item: item["knowledge"]["vector_count"],
            "activity": lambda item: item["activity"].get("last_message_at"),
        }
        reverse = sort_dir == "desc"
        sorter = sorters[sort_by]
        all_items.sort(
            key=lambda item: (
                sorter(item) is not None,
                sorter(item),
                int(item["chat_id"]),
            ),
            reverse=reverse,
        )
        total = len(all_items)
        items = all_items[(page - 1) * page_size : page * page_size]
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size,
            "category": category,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        }

    @app.get("/api/v1/preferences")
    async def get_preferences(
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        async with context.database.session() as db:
            return await _preferences(db, context.owner_id)

    @app.put("/api/v1/preferences")
    async def update_preferences(
        payload: DashboardPreferencesUpdate,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        value = payload.model_dump()
        if contains_secret(json.dumps(value, ensure_ascii=False)):
            raise HTTPException(status_code=422, detail="Tùy chỉnh không được chứa secret.")
        async with context.database.session() as db:
            row = await db.get(AppSetting, _preferences_key(context.owner_id))
            if not row:
                row = AppSetting(
                    key=_preferences_key(context.owner_id),
                    value=value,
                    description="Owner dashboard preferences; never stores secrets.",
                )
                db.add(row)
            else:
                row.value = value
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="dashboard_preferences_updated",
                    outcome="success",
                    target_type="dashboard",
                    details={"saved_views": len(value["saved_views"])},
                )
            )
        return value

    @app.get("/api/v1/groups/recommendations")
    async def inactive_group_recommendations(
        inactive_days: int = Query(default=60, ge=7, le=3650),
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        async with context.database.session() as db:
            chats = list(
                (
                    await db.scalars(
                        select(TelegramChat).where(
                            TelegramChat.chat_type.in_(("group", "supergroup", "channel"))
                        )
                    )
                ).all()
            )
            items = await _group_rows(db, chats)
            preferences = await _preferences(db, context.owner_id)
        excluded = set(preferences["always_keep_chat_ids"]) | set(
            preferences["ignored_recommendation_chat_ids"]
        )
        recommendations = []
        for item in items:
            rights = item.get("account_rights") or {}
            if (
                int(item["chat_id"]) in excluded
                or rights.get("is_creator")
                or rights.get("is_admin")
                or _activity_state(item, inactive_days) != "inactive"
            ):
                continue
            last_message = item["activity"]["last_message_at"]
            inactive_for = max(
                0,
                (datetime.now(UTC) - _utc(last_message)).days,
            )
            confidence = (
                "high"
                if inactive_for >= max(90, inactive_days * 2)
                and item["activity"]["observed_messages"] >= 20
                else "medium"
            )
            recommendations.append(
                {
                    **item,
                    "recommendation": {
                        "inactive_days": inactive_for,
                        "threshold_days": inactive_days,
                        "confidence": confidence,
                        "reason": (
                            f"Tin gần nhất trong dữ liệu đã cấp quyền cách đây "
                            f"{inactive_for} ngày; đã quan sát "
                            f"{item['activity']['observed_messages']} tin."
                        ),
                    },
                }
            )
        recommendations.sort(
            key=lambda item: item["recommendation"]["inactive_days"], reverse=True
        )
        return {
            "items": recommendations,
            "threshold_days": inactive_days,
            "total": len(recommendations),
            "unknown_activity": sum(
                1 for item in items if _activity_state(item, inactive_days) == "unknown"
            ),
        }

    @app.post("/api/v1/groups/{chat_id}/leave-preview", status_code=201)
    async def leave_chat_preview(
        chat_id: int,
        payload: LeaveChatRequest,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        async with context.database.session() as db:
            chat = await db.scalar(
                select(TelegramChat).where(TelegramChat.chat_id == chat_id)
            )
            if not chat:
                raise HTTPException(status_code=404, detail="Không tìm thấy group/channel.")
            rights = chat.account_rights or {}
            if rights.get("is_creator"):
                raise HTTPException(
                    status_code=409,
                    detail="Không thể rời nguồn mà tài khoản đang là chủ sở hữu.",
                )
            if rights.get("is_admin") and not payload.acknowledge_admin:
                raise HTTPException(
                    status_code=409,
                    detail="Tài khoản đang là admin; cần xác nhận đã hiểu ảnh hưởng.",
                )
            source = await db.get(KnowledgeSource, chat_id)
            action = await pending.create(
                db,
                action_type="leave_telegram_chat",
                requested_by=context.owner_id,
                chat_id=chat_id,
                payload=payload.model_dump(),
                preview=(
                    f"Rời {chat.chat_type} “{chat.title or chat_id}”. "
                    f"Dữ liệu giữ lại: {source.mysql_message_count if source else 0} tin, "
                    f"{source.vector_count if source else 0} vector. "
                    + (
                        "Sau khi rời sẽ tạo yêu cầu xóa dữ liệu riêng."
                        if payload.data_action == "request_delete"
                        else "Kho tri thức cũ được giữ lại ở chế độ lưu trữ."
                    )
                ),
                reason=(
                    "Worker sẽ kiểm tra lại quyền Telegram ngay trước khi rời; "
                    "nguồn thiếu lịch sử không bao giờ được tự đề xuất."
                ),
            )
        return _action_json(action)

    @app.get("/api/v1/groups/{chat_id}")
    async def group_detail(
        chat_id: int,
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        async with context.database.session() as db:
            chat = await db.scalar(select(TelegramChat).where(TelegramChat.chat_id == chat_id))
            if not chat:
                raise HTTPException(status_code=404, detail="Không tìm thấy group/channel.")
            rows = await _group_rows(db, [chat], include_permissions=True)
        return rows[0]

    @app.post("/api/v1/groups/{chat_id}/coverage-check")
    async def coverage_check(
        chat_id: int,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        vectors = context.vectors_getter()
        if vectors is None:
            raise HTTPException(status_code=503, detail="Local-first vector store is unavailable.")
        settings = context.settings_getter()
        async with context.database.session() as db:
            if not await db.scalar(
                select(TelegramChat.chat_id).where(TelegramChat.chat_id == chat_id)
            ):
                raise HTTPException(status_code=404, detail="Group/channel not found.")
            report = await inspect_source_coverage(
                db, chat_id=chat_id, settings=settings, vectors=vectors
            )
            legacy_count = None
            if settings.resolved_qdrant_path.exists() and (
                settings.resolved_qdrant_path != settings.resolved_semantic_vector_path
            ):
                try:
                    legacy = LocalVectorStore(
                        settings.resolved_qdrant_path, vector_size=settings.qdrant_vector_size
                    )
                    try:
                        legacy_count = legacy.count(chat_id=chat_id)
                    finally:
                        legacy.close()
                except Exception as exc:
                    report["legacy_read_error"] = str(redact(str(exc)))[:200]
            report["legacy_vectors"] = legacy_count
            if report["coverage_state"] != "healthy":
                source = await db.get(KnowledgeSource, chat_id)
                if source:
                    source.status = "reconciliation_required"
                db.add(_audit(
                    owner_id=context.owner_id,
                    action="vector_coverage_warning",
                    outcome="warning",
                    target_type="knowledge_source",
                    target_id=chat_id,
                    details={k: report[k] for k in ("expected_eligible_messages", "active_vectors", "missing_count", "orphan_count", "legacy_vectors")},
                ))
        return report

    @app.post("/api/v1/groups/{chat_id}/recovery-preview", status_code=201)
    async def recovery_preview(
        chat_id: int,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        vectors = context.vectors_getter()
        if vectors is None:
            raise HTTPException(status_code=503, detail="Local-first vector store is unavailable.")
        async with context.database.session() as db:
            if not await db.scalar(
                select(TelegramChat.chat_id).where(TelegramChat.chat_id == chat_id)
            ):
                raise HTTPException(status_code=404, detail="Group/channel not found.")
            report = await inspect_source_coverage(
                db, chat_id=chat_id, settings=context.settings_getter(), vectors=vectors
            )
            action = await pending.create(
                db,
                action_type="recover_source_index",
                requested_by=context.owner_id,
                chat_id=chat_id,
                payload={
                    "coverage": report,
                    "execution_guard": "explicit_owner_recovery_required",
                    "estimated_batches": (report["missing_count"] + 31) // 32,
                    "estimated_local_tokens": report["missing_count"] * 160,
                    "rollback_strategy": "No source data is modified. Recovery must be a separately approved, append-only reindex.",
                },
                preview=(
                    f"Preview only: recover {report['missing_count']} missing Local-first references "
                    f"out of {report['expected_eligible_messages']} eligible messages for source {chat_id}."
                ),
                reason="Owner review is required before any source recovery; this pending action cannot execute a reindex.",
            )
            db.add(_audit(
                owner_id=context.owner_id,
                action="vector_recovery_preview_created",
                outcome="pending",
                target_type="knowledge_source",
                target_id=chat_id,
                details={"action_id": action.action_id, "missing_count": report["missing_count"]},
            ))
        return {"pending_action": _action_json(action), "coverage": report}

    @app.post("/api/v1/groups/{chat_id}/actions", status_code=status.HTTP_201_CREATED)
    async def create_group_action(
        chat_id: int,
        payload: GroupActionRequest,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        action_payload = dict(payload.payload)
        if payload.action_type == "set_chat_allowed":
            action_payload.setdefault("memory_action", "keep")
        async with context.database.session() as db:
            exists = await db.scalar(
                select(TelegramChat.chat_id).where(TelegramChat.chat_id == chat_id)
            )
            if exists is None:
                raise HTTPException(status_code=404, detail="Không tìm thấy group/channel.")
            try:
                action = await pending.create(
                    db,
                    action_type=payload.action_type,
                    requested_by=context.owner_id,
                    payload=action_payload,
                    chat_id=chat_id,
                    preview=payload.preview,
                    reason=payload.reason,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="dashboard_pending_action_created",
                    outcome="pending",
                    target_type="telegram_chat",
                    target_id=chat_id,
                    details={"action_id": action.action_id, "action_type": action.action_type},
                )
            )
        return _action_json(action)

    @app.get("/api/v1/groups/{chat_id}/history-export")
    async def export_group_history(
        chat_id: int,
        search_terms: str = Query(default="", max_length=2000),
        promotion_only: bool = False,
        only_matches: bool = False,
        _session: AdminSession = Depends(require_session),
    ) -> StreamingResponse:
        terms = parse_search_terms(search_terms)
        if only_matches and not terms:
            raise HTTPException(
                status_code=422,
                detail="Nhập ít nhất một từ khóa trước khi chỉ xuất nội dung khớp.",
            )
        async with context.database.session() as db:
            chat = await db.scalar(select(TelegramChat).where(TelegramChat.chat_id == chat_id))
            if not chat:
                raise HTTPException(status_code=404, detail="Không tìm thấy group/channel.")
            filename = f"telegram-history-{chat_id}-{datetime.now(UTC):%Y-%m-%d}.csv"

        async def csv_stream():
            yield "\ufeff".encode("utf-8")
            yield encode_csv_row(history_csv_header())
            async with context.database.session() as db:
                source = await db.scalar(
                    select(TelegramChat).where(TelegramChat.chat_id == chat_id)
                )
                if not source:
                    return
                result = await db.stream_scalars(
                    select(TelegramMessage)
                    .where(
                        TelegramMessage.chat_id == chat_id,
                        TelegramMessage.is_deleted.is_(False),
                    )
                    .order_by(TelegramMessage.sent_at.asc(), TelegramMessage.message_id.asc())
                    .execution_options(yield_per=500)
                )
                async for message in result:
                    row, is_promotion, is_match = history_csv_row(source, message, terms)
                    if promotion_only and not is_promotion:
                        continue
                    if only_matches and not is_match:
                        continue
                    yield encode_csv_row(row)

        return StreamingResponse(
            csv_stream(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/v1/groups/{chat_id}/history-delete-preview", status_code=201)
    async def preview_history_link_deletion(
        chat_id: int,
        payload: HistoryDeletePreviewRequest,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        async with context.database.session() as db:
            chat = await db.scalar(select(TelegramChat).where(TelegramChat.chat_id == chat_id))
            if not chat:
                raise HTTPException(status_code=404, detail="Không tìm thấy group/channel.")
            rows = list(
                (
                    await db.scalars(
                        select(TelegramMessage)
                        .where(
                            TelegramMessage.chat_id == chat_id,
                            TelegramMessage.is_deleted.is_(False),
                        )
                        .order_by(TelegramMessage.message_id.asc())
                    )
                ).all()
            )
            keyword_terms = parse_search_terms(payload.keyword_terms)
            if payload.mode == "keywords" and not keyword_terms:
                raise HTTPException(
                    status_code=422,
                    detail="Nhập ít nhất một tên hoặc từ khóa để tạo preview xóa.",
                )
            sender_identities = await resolve_history_sender_filter(db, payload.sender_query)
            if payload.mode == "sender" and not sender_identities:
                raise HTTPException(
                    status_code=422,
                    detail="Không tìm thấy người đăng/channel. Hãy dùng @handle hoặc tên nguồn đã đồng bộ.",
                )
            matching = [
                row
                for row in rows
                if (
                    row.sender_id in sender_identities
                    if payload.mode == "sender"
                    else matches_history_delete_message(
                        row, payload.mode, keyword_terms=keyword_terms
                    )
                )
            ]
            if payload.select_all:
                excluded = set(payload.excluded_message_ids)
                candidates = [row for row in matching if row.message_id not in excluded]
                selection_type = "all_matching"
            else:
                selected = set(payload.selected_message_ids)
                candidates = [row for row in matching if row.message_id in selected]
                selection_type = "specific"
            if not candidates:
                raise HTTPException(
                    status_code=422,
                    detail="Chưa có post hợp lệ nào được chọn để xóa.",
                )
            mode_label = {
                "all_links": "mọi post có link",
                "promotion_links": "post có link kèm dấu hiệu promotion",
                "keywords": f"post khớp tên/từ khóa: {', '.join(keyword_terms)}",
                "sender": "post của người đăng/channel đã chọn",
                "images": "post có hình ảnh",
            }[payload.mode]
            action = await pending.create(
                db,
                action_type="delete_history_link_posts",
                requested_by=context.owner_id,
                chat_id=chat_id,
                payload={
                    "mode": payload.mode,
                    "keyword_terms": keyword_terms,
                    "sender_query": payload.sender_query.strip(),
                    "sender_ids": sorted(sender_identities),
                    "sender_identities": list(sender_identities.values()),
                    "selection_type": selection_type,
                    "candidate_count": len(candidates),
                    "max_message_id": max(row.message_id for row in candidates),
                    "selected_message_ids": (
                        [row.message_id for row in candidates]
                        if selection_type == "specific"
                        else []
                    ),
                    "excluded_message_ids": (
                        sorted(set(payload.excluded_message_ids))
                        if selection_type == "all_matching"
                        else []
                    ),
                    "sample_message_ids": [row.message_id for row in candidates[:20]],
                },
                preview=(
                    f"Xóa {len(candidates):,} {mode_label} khỏi Telegram source "
                    f"“{chat.title or chat_id}”. Preview khóa ở Message ID "
                    f"≤ {max(row.message_id for row in candidates)}; post mới sau preview không bị xóa."
                ),
                reason=(
                    "Không thể hoàn tác trên Telegram. Sau owner confirmation, worker sẽ "
                    "xóa theo lô và ghi audit log. Cần quyền delete_messages thực tế."
                ),
            )
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="history_link_delete_preview_created",
                    outcome="pending",
                    target_type="telegram_chat",
                    target_id=chat_id,
                    details={
                        "action_id": action.action_id,
                        "mode": payload.mode,
                        "candidate_count": len(candidates),
                    },
                )
            )
        return _action_json(action)

    @app.get("/api/v1/groups/{chat_id}/history-delete-candidates")
    async def list_history_link_delete_candidates(
        chat_id: int,
        mode: str = Query(pattern="^(all_links|promotion_links|keywords|sender|images)$"),
        keyword_terms: str = Query(default="", max_length=1_000),
        sender_query: str = Query(default="", max_length=255),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=10, le=100),
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        """Return safe, paginated MySQL candidates for the Telegram-like delete preview."""
        async with context.database.session() as db:
            chat = await db.scalar(select(TelegramChat).where(TelegramChat.chat_id == chat_id))
            if not chat:
                raise HTTPException(status_code=404, detail="Không tìm thấy group/channel.")
            rows = list(
                (
                    await db.scalars(
                        select(TelegramMessage)
                        .where(
                            TelegramMessage.chat_id == chat_id,
                            TelegramMessage.is_deleted.is_(False),
                        )
                        .order_by(TelegramMessage.message_id.desc())
                    )
                ).all()
            )
            terms = parse_search_terms(keyword_terms)
            if mode == "keywords" and not terms:
                return {
                    "mode": mode,
                    "keyword_terms": [],
                    "page": page,
                    "page_size": page_size,
                    "total": 0,
                    "items": [],
                }
            sender_identities = await resolve_history_sender_filter(db, sender_query)
            if mode == "sender" and not sender_identities:
                return {
                    "mode": mode,
                    "sender_query": sender_query,
                    "resolved_senders": [],
                    "page": page,
                    "page_size": page_size,
                    "total": 0,
                    "items": [],
                }
            matching = [
                row
                for row in rows
                if (
                    row.sender_id in sender_identities
                    if mode == "sender"
                    else matches_history_delete_message(row, mode, keyword_terms=terms)
                )
            ]
            start = (page - 1) * page_size
            slice_rows = matching[start : start + page_size]
            return {
                "mode": mode,
                "keyword_terms": terms,
                "sender_query": sender_query.strip(),
                "resolved_senders": list(sender_identities.values()),
                "page": page,
                "page_size": page_size,
                "total": len(matching),
                "items": [
                    {
                        "message_id": row.message_id,
                        "sender_id": row.sender_id,
                        "sender": sender_identities.get(row.sender_id),
                        "text": row.text or "",
                        "sent_at": row.sent_at.isoformat(),
                        "has_media": row.has_media,
                        "has_image": bool(
                            row.has_media
                            and (row.metadata_json or {}).get("media_kind") == "image"
                        ),
                        "reasons": promotion_reasons(row.text),
                        "matched_terms": matched_terms(row.text, terms) if mode == "keywords" else [],
                        "telegram_url": telegram_post_url(chat, row.message_id),
                    }
                    for row in slice_rows
                ],
            }

    @app.post("/api/v1/groups/{chat_id}/history-ai-delete-filter")
    async def classify_history_delete_candidates(
        chat_id: int,
        payload: HistoryAiFilterRequest,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        if not context.history_ai_filter_handler:
            raise HTTPException(
                status_code=503,
                detail="Bộ lọc AI OpenAI chưa sẵn sàng trong runtime.",
            )
        async with context.database.session() as db:
            chat = await db.scalar(select(TelegramChat).where(TelegramChat.chat_id == chat_id))
            if not chat:
                raise HTTPException(status_code=404, detail="Không tìm thấy group/channel.")
            rows = list(
                (
                    await db.scalars(
                        select(TelegramMessage).where(
                            TelegramMessage.chat_id == chat_id,
                            TelegramMessage.message_id.in_(payload.message_ids),
                            TelegramMessage.is_deleted.is_(False),
                        )
                    )
                ).all()
            )
            keyword_terms = parse_search_terms(payload.keyword_terms)
            if payload.mode == "keywords" and not keyword_terms:
                raise HTTPException(
                    status_code=422,
                    detail="Nhập ít nhất một tên hoặc từ khóa trước khi dùng AI.",
                )
            sender_identities = await resolve_history_sender_filter(db, payload.sender_query)
            if payload.mode == "sender" and not sender_identities:
                raise HTTPException(
                    status_code=422,
                    detail="Không tìm thấy người đăng/channel trước khi dùng AI.",
                )
            candidates = [
                row
                for row in rows
                if (
                    row.sender_id in sender_identities
                    if payload.mode == "sender"
                    else matches_history_delete_message(
                        row, payload.mode, keyword_terms=keyword_terms
                    )
                )
            ]
            if not candidates:
                raise HTTPException(
                    status_code=422,
                    detail="Không còn post hợp lệ để AI phân tích trong lựa chọn này.",
                )
            results = await context.history_ai_filter_handler(
                db,
                chat_id,
                payload.instruction.strip(),
                [
                    {"message_id": row.message_id, "text": row.text or ""}
                    for row in candidates
                ],
            )
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="history_delete_ai_filter",
                    outcome="success",
                    target_type="telegram_chat",
                    target_id=chat_id,
                    details={
                        "mode": payload.mode,
                        "analyzed_count": len(candidates),
                        "matched_count": sum(1 for item in results if item.get("match")),
                    },
                )
            )
        return {"items": results, "analyzed_count": len(candidates)}

    @app.post("/api/v1/groups/{chat_id}/permissions", status_code=status.HTTP_201_CREATED)
    async def create_permission_action(
        chat_id: int,
        payload: PermissionUpdate,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        request_payload = GroupActionRequest(
            action_type=(
                "set_group_ai_ask"
                if payload.permission is PermissionName.GROUP_AI_ASK
                else "set_chat_permission"
            ),
            payload=(
                {"enabled": payload.enabled}
                if payload.permission is PermissionName.GROUP_AI_ASK
                else {"permission": payload.permission.value, "enabled": payload.enabled}
            ),
            preview=(
                f"{'Bật' if payload.enabled else 'Tắt'} quyền "
                f"{payload.permission.value} cho chat {chat_id}"
            ),
            reason="Thay đổi quyền từ dashboard local.",
        )
        return await create_group_action(chat_id, request_payload, _session)

    @app.put("/api/v1/groups/{chat_id}/ai-route")
    async def update_ai_route(
        chat_id: int,
        payload: AiRouteUpdate,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        async with context.database.session() as db:
            try:
                policy = await context.policy.set_ai_route(
                    db,
                    chat_id,
                    mode=payload.mode,
                    preferred_cloud_provider=payload.preferred_cloud_provider,
                    cloud_fallback=payload.cloud_fallback,
                )
            except PermissionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="dashboard_ai_route_updated",
                    outcome="success",
                    target_type="telegram_chat",
                    target_id=chat_id,
                    details=payload.model_dump(),
                )
            )
        return _policy_json(policy)

    @app.put("/api/v1/groups/{chat_id}/ai-efficiency")
    async def update_ai_efficiency(
        chat_id: int,
        payload: AiEfficiencyUpdate,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        async with context.database.session() as db:
            try:
                policy = await context.policy.set_ai_efficiency(
                    db,
                    chat_id,
                    preset=payload.preset,
                    filtering_level=payload.filtering_level,
                    rag_top_k=payload.rag_top_k,
                    rag_max_context_tokens=payload.rag_max_context_tokens,
                )
            except PermissionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="dashboard_ai_efficiency_updated",
                    outcome="success",
                    target_type="telegram_chat",
                    target_id=chat_id,
                    details=payload.model_dump(),
                )
            )
        return _policy_json(policy)

    @app.put("/api/v1/groups/{chat_id}/limits")
    async def update_source_limits(
        chat_id: int,
        payload: SourceLimitsUpdate,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        async with context.database.session() as db:
            try:
                policy = await context.policy.set_source_limits(
                    db,
                    chat_id,
                    retention_days=payload.retention_days,
                    max_messages=payload.max_messages,
                    max_storage_mb=payload.max_storage_mb,
                    max_vectors=payload.max_vectors,
                )
            except PermissionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="dashboard_source_limits_updated",
                    outcome="success",
                    target_type="telegram_chat",
                    target_id=chat_id,
                    details=payload.model_dump(),
                )
            )
        return _policy_json(policy)

    @app.get("/api/v1/pending-actions")
    async def pending_actions(
        state: str = Query(default="pending", pattern="^(pending|confirmed|executed|failed|cancelled|all)$"),
        limit: int = Query(default=100, ge=1, le=500),
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        async with context.database.session() as db:
            expired = list(
                (
                    await db.scalars(
                        select(PendingAction).where(
                            PendingAction.requested_by == context.owner_id,
                            PendingAction.status == "pending",
                            PendingAction.expires_at <= datetime.now(UTC),
                        )
                    )
                ).all()
            )
            for action in expired:
                action.status = "expired"
            await db.flush()
            query = select(PendingAction).where(
                PendingAction.requested_by == context.owner_id
            )
            if state != "all":
                query = query.where(PendingAction.status == state)
            rows = list(
                (
                    await db.scalars(
                        query.order_by(PendingAction.created_at.desc()).limit(limit)
                    )
                ).all()
            )
        return {"items": [_action_json(row) for row in rows]}

    @app.post("/api/v1/pending-actions/{action_id}/confirm")
    async def confirm_action(
        action_id: str,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        async with context.database.session() as db:
            try:
                action = await pending.confirm(db, action_id, context.owner_id)
            except PermissionError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except TimeoutError as exc:
                raise HTTPException(status_code=410, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="dashboard_pending_action_confirmed",
                    outcome="confirmed",
                    target_type="pending_action",
                    target_id=action_id,
                )
            )
        return _action_json(action)

    @app.post("/api/v1/pending-actions/{action_id}/cancel")
    async def cancel_action(
        action_id: str,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        async with context.database.session() as db:
            try:
                action = await pending.cancel(db, action_id, context.owner_id)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="dashboard_pending_action_cancelled",
                    outcome="cancelled",
                    target_type="pending_action",
                    target_id=action_id,
                )
            )
        return _action_json(action)

    @app.get("/api/v1/knowledge/sources")
    async def knowledge_sources(
        source_status: str | None = Query(default=None, max_length=32),
        query: str = Query(default="", max_length=200),
        chat_type: str | None = Query(default=None, pattern="^(group|supergroup|channel)$"),
        auto_knowledge: bool | None = Query(default=None),
        sort_by: str = Query(
            default="title",
            pattern="^(title|status|messages|vectors|storage|last_learned)$",
        ),
        sort_dir: str = Query(default="asc", pattern="^(asc|desc)$"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=500),
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        async with context.database.session() as db:
            inventory = await _knowledge_inventory_snapshot(db)
            auto_knowledge_ids = set(
                (
                    await db.scalars(
                        select(TelegramChatPermission.chat_id).where(
                            TelegramChatPermission.permission
                            == PermissionName.AUTO_KNOWLEDGE.value,
                            TelegramChatPermission.enabled.is_(True),
                        )
                    )
                ).all()
            )
            counts = Counter(row.source.status for row in inventory)
            visible = []
            normalized_query = query.strip().casefold().lstrip("@")
            for row in inventory:
                if source_status and row.source.status != source_status:
                    continue
                if chat_type and row.chat.chat_type != chat_type:
                    continue
                is_auto = row.chat.chat_id in auto_knowledge_ids
                if auto_knowledge is not None and is_auto is not auto_knowledge:
                    continue
                if normalized_query and normalized_query not in " ".join(
                    [
                        row.chat.title or "",
                        row.chat.username or "",
                        str(row.chat.chat_id),
                        row.source.owner_note or "",
                    ]
                ).casefold():
                    continue
                visible.append(row)
            sorters = {
                "title": lambda row: (row.chat.title or "").casefold(),
                "status": lambda row: row.source.status,
                "messages": lambda row: row.source.mysql_message_count,
                "vectors": lambda row: row.source.vector_count,
                "storage": lambda row: (
                    row.source.message_storage_bytes + row.source.media_storage_bytes
                ),
                "last_learned": lambda row: row.source.last_learned_at,
            }
            sorter = sorters[sort_by]
            visible.sort(
                key=lambda row: (
                    sorter(row) is not None,
                    sorter(row),
                    row.chat.chat_id,
                ),
                reverse=sort_dir == "desc",
            )
            total = len(visible)
            page_rows = visible[(page - 1) * page_size : page * page_size]
            learned = counts["learned"] + counts["learned_warning"]
            last_learned_values = [
                row.source.last_learned_at
                for row in inventory
                if row.source.last_learned_at is not None
            ]
        items = [
            {
                "chat_id": str(row.source.chat_id),
                "title": row.chat.title,
                "username": row.chat.username,
                "chat_type": row.chat.chat_type,
                "status": row.source.status,
                "status_reason": knowledge_status_reason(
                    row.source.status,
                    row.source.last_error,
                ),
                "requested_for_learning": row.source.requested_for_learning,
                "auto_knowledge": row.chat.chat_id in auto_knowledge_ids,
                "mysql_message_count": row.source.mysql_message_count,
                "text_message_count": row.source.text_message_count,
                "last_indexed_count": row.source.last_indexed_count,
                "vector_count": row.source.vector_count,
                "message_storage_bytes": row.source.message_storage_bytes,
                "media_storage_bytes": row.source.media_storage_bytes,
                "last_job_id": row.source.last_job_id,
                "last_error": (
                    str(redact(row.source.last_error)) if row.source.last_error else None
                ),
                "owner_note": (
                    str(redact(row.source.owner_note)) if row.source.owner_note else None
                ),
                "last_learned_at": _utc(row.source.last_learned_at),
                "updated_at": _utc(row.source.updated_at),
            }
            for row in page_rows
        ]
        all_total = len(inventory)
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "summary": {
                "total_sources": all_total,
                "learned_sources": learned,
                "auto_knowledge_sources": len(auto_knowledge_ids),
                "coverage_percent": (
                    round((learned / all_total) * 100, 1) if all_total else 0.0
                ),
                "mysql_messages": sum(
                    row.source.mysql_message_count for row in inventory
                ),
                "vectors": sum(row.source.vector_count for row in inventory),
                "last_learned_at": (
                    _utc(max(last_learned_values)) if last_learned_values else None
                ),
                "counts": dict(counts),
            },
        }

    @app.get("/api/v1/knowledge/sources/export")
    async def export_knowledge_sources(
        _session: AdminSession = Depends(require_session),
    ) -> Response:
        async with context.database.session() as db:
            rows = await _knowledge_inventory_snapshot(db)
            content = build_learning_inventory_csv(rows)
        filename = f"telegram-learning-sources-{datetime.now(UTC):%Y-%m-%d}.csv"
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post(
        "/api/v1/knowledge/enable-all-action",
        status_code=status.HTTP_201_CREATED,
    )
    async def enable_all_knowledge_sources(
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        async with context.database.session() as db:
            inventory = await _knowledge_inventory_snapshot(db)
            auto_knowledge_ids = set(
                (
                    await db.scalars(
                        select(TelegramChatPermission.chat_id).where(
                            TelegramChatPermission.permission
                            == PermissionName.AUTO_KNOWLEDGE.value,
                            TelegramChatPermission.enabled.is_(True),
                        )
                    )
                ).all()
            )
            chat_ids = [
                row.chat.chat_id
                for row in inventory
                if row.chat.chat_id not in auto_knowledge_ids
                or row.source.status in {"not_learned", "no_content", "failed"}
            ]
            if not chat_ids:
                raise HTTPException(
                    status_code=409,
                    detail="Tất cả nguồn đã được cấp quyền học và đã có trong kho tri thức.",
                )
            action = await pending.create(
                db,
                action_type="enable_group_learning_bulk",
                requested_by=context.owner_id,
                payload={"chat_ids": chat_ids[:500], "limit": 1000},
                preview=(
                    f"Cấp quyền học và tạo hàng đợi cho {min(len(chat_ids), 500)} "
                    "group/channel chưa nằm đầy đủ trong bộ não chung."
                ),
                reason=(
                    "Hành động chỉ có hiệu lực sau khi owner xác nhận; "
                    "worker sẽ đồng bộ MySQL rồi tạo embedding."
                ),
            )
        return _action_json(action)

    @app.post(
        "/api/v1/knowledge/enable-selection-action",
        status_code=status.HTTP_201_CREATED,
    )
    async def enable_selected_knowledge_sources(
        payload: KnowledgeSelectionRequest,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        unique_ids = list(dict.fromkeys(payload.chat_ids))
        async with context.database.session() as db:
            valid_ids = set(
                (
                    await db.scalars(
                        select(TelegramChat.chat_id).where(
                            TelegramChat.chat_id.in_(unique_ids),
                            TelegramChat.chat_type.in_(LEARNING_CHAT_TYPES),
                        )
                    )
                ).all()
            )
            selected = [chat_id for chat_id in unique_ids if chat_id in valid_ids]
            if not selected:
                raise HTTPException(status_code=404, detail="Không có nguồn hợp lệ được chọn.")
            active_jobs = list(
                (
                    await db.scalars(
                        select(BackgroundJob).where(
                            BackgroundJob.job_type == "learn_group",
                            BackgroundJob.status.in_(
                                ("queued", "running", "paused", "pause_requested")
                            ),
                        )
                    )
                ).all()
            )
            active_ids = {
                int(job.payload["chat_id"])
                for job in active_jobs
                if (job.payload or {}).get("chat_id") is not None
            }
            queue_ids = [chat_id for chat_id in selected if chat_id not in active_ids]
            if not queue_ids:
                raise HTTPException(
                    status_code=409,
                    detail="Tất cả nguồn đã chọn đang có learning job hoạt động.",
                )
            action = await pending.create(
                db,
                action_type="enable_group_learning_bulk",
                requested_by=context.owner_id,
                payload={"chat_ids": queue_ids, "limit": payload.limit},
                preview=(
                    f"Đưa {len(queue_ids)} nguồn đã chọn vào hàng đợi học; "
                    f"bỏ qua {len(selected) - len(queue_ids)} nguồn đang chạy/chờ."
                ),
                reason="Mỗi nguồn tạo một job có trạng thái và tiến độ riêng sau khi xác nhận.",
            )
        return _action_json(action)

    @app.put("/api/v1/knowledge/sources/{chat_id}/note")
    async def update_knowledge_source_note(
        chat_id: int,
        payload: KnowledgeNoteUpdate,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        if payload.note and contains_secret(payload.note):
            raise HTTPException(status_code=422, detail="Ghi chú không được chứa secret.")
        async with context.database.session() as db:
            chat = await db.scalar(
                select(TelegramChat).where(TelegramChat.chat_id == chat_id)
            )
            if not chat:
                raise HTTPException(status_code=404, detail="Không tìm thấy nguồn.")
            source = await db.get(KnowledgeSource, chat_id)
            if not source:
                source = KnowledgeSource(chat_id=chat_id)
                db.add(source)
            source.owner_note = payload.note.strip() if payload.note else None
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="knowledge_source_note_updated",
                    outcome="success",
                    target_type="knowledge_source",
                    target_id=chat_id,
                )
            )
        return {"chat_id": str(chat_id), "owner_note": source.owner_note}

    @app.post("/api/v1/knowledge/sources/{chat_id}/delete-preview", status_code=201)
    async def delete_knowledge_source_preview(
        chat_id: int,
        payload: KnowledgeDeleteRequest,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        async with context.database.session() as db:
            chat = await db.scalar(
                select(TelegramChat).where(TelegramChat.chat_id == chat_id)
            )
            if not chat:
                raise HTTPException(status_code=404, detail="Không tìm thấy nguồn.")
            if payload.scope == "all" and payload.confirmation != (chat.title or str(chat_id)):
                raise HTTPException(
                    status_code=422,
                    detail="Để xóa toàn bộ, hãy nhập chính xác tên group/channel.",
                )
            jobs = list(
                (
                    await db.scalars(
                        select(BackgroundJob).where(
                            BackgroundJob.job_type == "learn_group",
                            BackgroundJob.status.in_(
                                ("queued", "running", "paused", "pause_requested")
                            ),
                        )
                    )
                ).all()
            )
            if any(
                str((job.payload or {}).get("chat_id")) == str(chat_id) for job in jobs
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Hãy pause/hủy learning job của nguồn trước khi xóa dữ liệu.",
                )
            message_count = int(
                (
                    await db.scalar(
                        select(func.count(TelegramMessage.id)).where(
                            TelegramMessage.chat_id == chat_id
                        )
                    )
                )
                or 0
            )
            attachment_count = int(
                (
                    await db.scalar(
                        select(func.count(TelegramAttachment.id))
                        .join(
                            TelegramMessage,
                            TelegramAttachment.telegram_message_id == TelegramMessage.id,
                        )
                        .where(TelegramMessage.chat_id == chat_id)
                    )
                )
                or 0
            )
            source = await db.get(KnowledgeSource, chat_id)
            vector_count = source.vector_count if source else 0
            action = await pending.create(
                db,
                action_type="delete_learned_data",
                requested_by=context.owner_id,
                chat_id=chat_id,
                payload={"scope": payload.scope},
                preview=(
                    f"Nguồn “{chat.title or chat_id}”: phạm vi {payload.scope}; "
                    f"{message_count} tin MySQL, {vector_count} vector, "
                    f"{attachment_count} tệp liên quan."
                ),
                reason=(
                    "Không thể hoàn tác dữ liệu đã xóa. Worker sẽ khóa kho tri thức, "
                    "kiểm tra job lần cuối và dọn vector mồ côi."
                ),
            )
        return _action_json(action)

    @app.get("/api/v1/learning-jobs")
    async def learning_jobs(
        job_status: str | None = Query(default=None, max_length=32),
        limit: int = Query(default=200, ge=1, le=1000),
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        query = select(BackgroundJob).where(BackgroundJob.job_type == "learn_group")
        if job_status:
            query = query.where(BackgroundJob.status == job_status)
        async with context.database.session() as db:
            rows = list(
                (
                    await db.scalars(
                        query.order_by(BackgroundJob.created_at.desc()).limit(limit)
                    )
                ).all()
            )
        return {"items": [_job_json(row) for row in rows]}

    @app.get("/api/v1/history-backfill-jobs")
    async def history_backfill_jobs(
        chat_id: int | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        query = select(BackgroundJob).where(BackgroundJob.job_type == "history_backfill")
        if chat_id is not None:
            query = query.where(cast(BackgroundJob.payload["chat_id"], String) == str(chat_id))
        async with context.database.session() as db:
            rows = list(
                (
                    await db.scalars(
                        query.order_by(BackgroundJob.created_at.desc()).limit(limit)
                    )
                ).all()
            )
        return {"items": [_job_json(row) for row in rows]}

    @app.get("/api/v1/history-link-delete-jobs")
    async def history_link_delete_jobs(
        chat_id: int | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        query = select(BackgroundJob).where(BackgroundJob.job_type == "history_link_delete")
        if chat_id is not None:
            query = query.where(cast(BackgroundJob.payload["chat_id"], String) == str(chat_id))
        async with context.database.session() as db:
            rows = list(
                (
                    await db.scalars(
                        query.order_by(BackgroundJob.created_at.desc()).limit(limit)
                    )
                ).all()
            )
        return {"items": [_job_json(row) for row in rows]}

    @app.post("/api/v1/learning-jobs/pause-all")
    async def pause_all_jobs(
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        async with context.database.session() as db:
            count = await context.pause_all_handler(db)
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="dashboard_learning_pause_all",
                    outcome="success",
                    details={"count": count},
                )
            )
        return {"updated": count}

    @app.post("/api/v1/learning-jobs/resume-all")
    async def resume_all_jobs(
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        async with context.database.session() as db:
            count = await context.resume_all_handler(db)
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="dashboard_learning_resume_all",
                    outcome="success",
                    details={"count": count},
                )
            )
        return {"updated": count}

    @app.post("/api/v1/learning-jobs/{job_id}/{operation}")
    async def change_job_state(
        job_id: str,
        operation: str,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        if operation not in {"pause", "resume", "retry"}:
            raise HTTPException(status_code=404, detail="Thao tác job không hợp lệ.")
        async with context.database.session() as db:
            job = await db.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.id == job_id,
                    BackgroundJob.job_type == "learn_group",
                )
                .with_for_update()
            )
            if not job:
                raise HTTPException(status_code=404, detail="Không tìm thấy learning job.")
            _update_job_state(job, operation)
            source = None
            if (job.payload or {}).get("chat_id") is not None:
                source = await db.get(KnowledgeSource, int(job.payload["chat_id"]))
            if source:
                source.status = job.status
                source.last_error = job.last_error
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action=f"dashboard_learning_{operation}",
                    outcome="success",
                    target_type="background_job",
                    target_id=job_id,
                )
            )
        return _job_json(job)

    @app.get("/api/v1/ai/config")
    async def ai_config(_session: AdminSession = Depends(require_session)) -> dict:
        current = context.settings_getter()
        async with context.database.session() as db:
            usage_24h = list(
                (
                    await db.scalars(
                        select(AiUsage).where(
                            AiUsage.occurred_at >= datetime.now(UTC) - timedelta(hours=24)
                        )
                    )
                ).all()
            )
        operation_usage: dict[str, dict[str, int | float]] = {}
        feature_usage: dict[str, dict[str, int | float]] = {}
        provider_model_usage: dict[str, dict[str, int | float]] = {}
        chat_usage: dict[str, dict[str, int | float]] = {}
        local_requests = cloud_requests = cache_hits = 0
        cached_tokens = embedding_tokens = 0
        for row in usage_24h:
            operation = operation_usage.setdefault(
                row.operation,
                {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "failed": 0,
                },
            )
            operation["requests"] += 1
            operation["input_tokens"] += row.input_tokens
            operation["output_tokens"] += row.output_tokens
            operation["estimated_cost_usd"] += row.estimated_cost_usd
            operation["failed"] += int(not row.success)
            feature = row.feature or row.operation or "unknown"
            feature_row = feature_usage.setdefault(
                feature,
                {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "embedding_tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "failed": 0,
                },
            )
            feature_row["requests"] += 1
            feature_row["input_tokens"] += row.input_tokens
            feature_row["output_tokens"] += row.output_tokens
            feature_row["cached_tokens"] += row.cached_tokens
            feature_row["embedding_tokens"] += row.embedding_tokens
            feature_row["estimated_cost_usd"] += row.estimated_cost_usd
            feature_row["failed"] += int(not row.success)
            provider_key = f"{row.provider or 'unknown'} · {row.model}"
            execution = (
                "local"
                if row.is_local
                else "cloud"
                if row.provider in {"openai", "openrouter"}
                else "legacy"
            )
            provider_row = provider_model_usage.setdefault(
                provider_key,
                {
                    "requests": 0,
                    "tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "execution": execution,
                },
            )
            provider_row["requests"] += 1
            provider_row["tokens"] += row.input_tokens + row.output_tokens
            provider_row["estimated_cost_usd"] += row.estimated_cost_usd
            if row.chat_id is not None:
                source_row = chat_usage.setdefault(
                    str(row.chat_id),
                    {"requests": 0, "tokens": 0, "estimated_cost_usd": 0.0},
                )
                source_row["requests"] += 1
                source_row["tokens"] += row.input_tokens + row.output_tokens
                source_row["estimated_cost_usd"] += row.estimated_cost_usd
            local_requests += int(row.is_local)
            cloud_requests += int(not row.is_local and row.provider not in {None, "off"})
            cache_hits += int(row.cache_hit)
            cached_tokens += row.cached_tokens
            embedding_tokens += row.embedding_tokens
        return {
            "provider": current.ai_provider,
            "model": current.active_ai_model,
            "embedding_model": current.active_embedding_model,
            "embeddings_enabled": current.enable_embeddings,
            "daily_budget_usd": current.daily_ai_budget_usd,
            "monthly_budget_usd": current.monthly_ai_budget_usd,
            "usage_24h": {
                "requests": len(usage_24h),
                "input_tokens": sum(row.input_tokens for row in usage_24h),
                "output_tokens": sum(row.output_tokens for row in usage_24h),
                "estimated_cost_usd": sum(row.estimated_cost_usd for row in usage_24h),
                "failed": sum(not row.success for row in usage_24h),
                "operations": operation_usage,
                "features": feature_usage,
                "provider_models": provider_model_usage,
                "groups": chat_usage,
                "local_requests": local_requests,
                "cloud_requests": cloud_requests,
                "cache_hits": cache_hits,
                "cache_hit_rate": (cache_hits / len(usage_24h)) if usage_24h else 0.0,
                "cached_tokens": cached_tokens,
                "embedding_tokens": embedding_tokens,
            },
            "efficiency": {
                "default_preset": current.ai_efficiency_preset,
                "embedding_provider": current.embedding_provider,
                "embedding_version": current.embedding_version,
                "rag_top_k": current.rag_top_k,
                "rag_max_context_tokens": current.rag_max_context_tokens,
                "rag_max_chunks_per_source": current.rag_max_chunks_per_source,
                "rag_max_sources": current.rag_max_sources,
                "daily_token_limit": current.daily_token_limit,
                "monthly_token_limit": current.monthly_token_limit,
                "group_daily_token_limit": current.group_daily_token_limit,
                "feature_daily_token_limit": current.feature_daily_token_limit,
                "provider_daily_token_limit": current.provider_daily_token_limit,
                "max_cloud_fallbacks_per_day": current.max_cloud_fallbacks_per_day,
            },
            "providers": ["openai", "openrouter", "ollama", "off"],
        }

    @app.put("/api/v1/ai/provider")
    async def change_provider(
        payload: ProviderUpdate,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        try:
            message = await context.ai_switch_handler(payload.provider)
        except (OllamaError, RuntimeError, ValueError) as exc:
            async with context.database.session() as db:
                db.add(
                    _audit(
                        owner_id=context.owner_id,
                        action="dashboard_ai_provider_changed",
                        outcome="failed",
                        reason=str(exc),
                        details={"provider": payload.provider},
                    )
                )
            raise HTTPException(status_code=409, detail=str(redact(str(exc)))) from exc
        async with context.database.session() as db:
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="dashboard_ai_provider_changed",
                    outcome="success",
                    details={"provider": payload.provider},
                )
            )
        return {"provider": context.settings_getter().ai_provider, "message": message}

    @app.get("/api/v1/ollama/models")
    async def ollama_models(_session: AdminSession = Depends(require_session)) -> dict:
        try:
            models = await context.ollama.list_models()
        except OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        import psutil

        current = context.settings_getter()
        available_ram = int(psutil.virtual_memory().available)
        async with context.database.session() as db:
            usage_rows = (
                await db.execute(
                    select(AiUsage.model, func.max(AiUsage.occurred_at)).group_by(
                        AiUsage.model
                    )
                )
            ).all()
            enabled_groups = int(
                (
                    await db.scalar(
                        select(func.count(TelegramChatPolicy.id)).where(
                            TelegramChatPolicy.allowed.is_(True),
                            TelegramChatPolicy.ai_mode != "off",
                        )
                    )
                )
                or 0
            )
        last_used = {name: _utc(used_at) for name, used_at in usage_rows}

        def describe_model(model) -> dict:
            capabilities = _ollama_capabilities(model)
            embedding = "embedding" in capabilities
            vision = "vision" in capabilities
            estimated_ram = int(model.size * 1.25)
            fit_ratio = estimated_ram / max(available_ram, 1)
            fit = (
                "recommended"
                if fit_ratio <= 0.7
                else "slow"
                if fit_ratio <= 1.0
                else "not_recommended"
            )
            active = (
                model.name == current.ollama_primary_model
                or model.name == current.ollama_embedding_model
            )
            return {
                "name": model.name,
                "size_bytes": model.size,
                "size": format_model_size(model.size),
                "modified_at": model.modified_at,
                "family": model.family,
                "parameter_size": model.parameter_size,
                "quantization_level": model.quantization_level,
                "model_type": "embedding" if embedding else "multimodal" if vision else "chat",
                "capabilities": capabilities,
                "compatibility": {
                    "chat": "chat" in capabilities,
                    "embedding": "embedding" in capabilities,
                    "reason": (
                        "Model embedding chỉ dùng để lập chỉ mục vector."
                        if embedding
                        else "Model chat không trả về vector embedding chuyên dụng."
                    ),
                },
                "estimated_ram_bytes": estimated_ram,
                "estimated_vram_bytes": model.size,
                "fit": fit,
                "last_used_at": last_used.get(model.name),
                "assigned_groups": enabled_groups if active else 0,
                "is_chat_model": model.name == current.ollama_primary_model,
                "is_embedding_model": model.name == current.ollama_embedding_model,
            }

        return {
            "items": [describe_model(model) for model in models],
            "available_ram_bytes": available_ram,
        }

    @app.post("/api/v1/ollama/models/pull", status_code=status.HTTP_202_ACCEPTED)
    async def pull_ollama_model(
        payload: OllamaPullRequest,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        async with context.database.session() as db:
            existing = await db.scalar(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == "ollama_pull",
                    BackgroundJob.status.in_(["queued", "running", "cancel_requested"]),
                )
            )
            if existing:
                raise HTTPException(status_code=409, detail="Đã có model Ollama đang được tải.")
            job = BackgroundJob(
                job_type="ollama_pull",
                payload={"model": payload.model, "owner_id": context.owner_id},
                status="queued",
                max_attempts=2,
            )
            db.add(job)
            await db.flush()
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="dashboard_ollama_pull_queued",
                    outcome="queued",
                    target_type="ollama_model",
                    target_id=payload.model,
                    details={"job_id": job.id},
                )
            )
        return _job_json(job)

    @app.get("/api/v1/ollama/downloads")
    async def ollama_downloads(
        limit: int = Query(default=20, ge=1, le=100),
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        async with context.database.session() as db:
            rows = list(
                (
                    await db.scalars(
                        select(BackgroundJob)
                        .where(BackgroundJob.job_type == "ollama_pull")
                        .order_by(BackgroundJob.created_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
        return {"items": [_job_json(row) for row in rows]}

    @app.post("/api/v1/ollama/downloads/{job_id}/cancel")
    async def cancel_ollama_download(
        job_id: str,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        async with context.database.session() as db:
            job = await db.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.id == job_id,
                    BackgroundJob.job_type == "ollama_pull",
                )
                .with_for_update()
            )
            if not job:
                raise HTTPException(status_code=404, detail="Không tìm thấy job tải model.")
            if job.status == "queued":
                job.status = "cancelled"
                job.payload = {**(job.payload or {}), "phase": "cancelled"}
            elif job.status == "running":
                job.status = "cancel_requested"
                job.payload = {**(job.payload or {}), "phase": "cancelling"}
            else:
                raise HTTPException(
                    status_code=409,
                    detail="Job tải model đã kết thúc và không thể hủy.",
                )
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="dashboard_ollama_pull_cancelled",
                    outcome="requested",
                    target_type="ollama_model",
                    target_id=str((job.payload or {}).get("model", "")),
                    details={"job_id": job.id},
                )
            )
        return _job_json(job)

    @app.post("/api/v1/ollama/activate-preview")
    async def preview_activate_ollama(
        payload: OllamaActivateRequest,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        try:
            models = {model.name: model for model in await context.ollama.list_models()}
        except OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if payload.chat_model not in models or payload.embedding_model not in models:
            raise HTTPException(status_code=409, detail="Model đã chọn chưa được cài trong Ollama.")
        if "chat" not in _ollama_capabilities(models[payload.chat_model]):
            raise HTTPException(status_code=409, detail="Chat model đã chọn không có capability chat.")
        if "embedding" not in _ollama_capabilities(models[payload.embedding_model]):
            raise HTTPException(
                status_code=409,
                detail="Embedding model đã chọn không có capability embedding.",
            )
        current = context.settings_getter()
        async with context.database.session() as db:
            source_count = int(
                (await db.scalar(select(func.count(KnowledgeSource.chat_id)))) or 0
            )
            vector_count = int(
                (await db.scalar(select(func.sum(KnowledgeSource.vector_count)))) or 0
            )
        return {
            "current": {
                "chat_model": current.ollama_primary_model,
                "embedding_model": current.ollama_embedding_model,
            },
            "requested": {
                "chat_model": payload.chat_model,
                "embedding_model": payload.embedding_model,
            },
            "embedding_changed": payload.embedding_model != current.ollama_embedding_model,
            "requires_reindex": payload.embedding_model != current.ollama_embedding_model,
            "affected_sources": source_count,
            "affected_vectors": vector_count,
            "message": (
                "Đổi embedding model sẽ tạo kho vector tương ứng và cần lập chỉ mục lại."
                if payload.embedding_model != current.ollama_embedding_model
                else "Embedding model không đổi; không yêu cầu re-index vì lựa chọn này."
            ),
        }

    @app.put("/api/v1/ollama/activate")
    async def activate_ollama(
        payload: OllamaActivateRequest,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        try:
            installed = {model.name: model for model in await context.ollama.list_models()}
            if payload.chat_model not in installed or payload.embedding_model not in installed:
                raise ValueError("Model chat hoặc embedding chưa được cài trong Ollama.")
            if "chat" not in _ollama_capabilities(installed[payload.chat_model]):
                raise ValueError("Chat model đã chọn không có capability chat.")
            if "embedding" not in _ollama_capabilities(installed[payload.embedding_model]):
                raise ValueError("Embedding model đã chọn không có capability embedding.")
            dimension = await context.ollama.embedding_dimension(payload.embedding_model)
            message = await context.ollama_activate_handler(
                payload.chat_model,
                payload.embedding_model,
                dimension,
            )
        except (OllamaError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(redact(str(exc)))) from exc
        async with context.database.session() as db:
            db.add(
                _audit(
                    owner_id=context.owner_id,
                    action="dashboard_ollama_activated",
                    outcome="success",
                    details={
                        "chat_model": payload.chat_model,
                        "embedding_model": payload.embedding_model,
                        "dimension": dimension,
                    },
                )
            )
        return {"message": message, "dimension": dimension}

    @app.post("/api/v1/ollama/models/delete-preview", status_code=status.HTTP_201_CREATED)
    async def delete_ollama_model_preview(
        payload: OllamaDeleteRequest,
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        current = context.settings_getter()
        if payload.model in {current.ollama_primary_model, current.ollama_embedding_model}:
            raise HTTPException(
                status_code=409,
                detail="Hãy chọn model chat/embedding khác trước khi xóa model đang dùng.",
            )
        async with context.database.session() as db:
            action = await pending.create(
                db,
                action_type="delete_ollama_model",
                requested_by=context.owner_id,
                payload={"model": payload.model},
                preview=f"Xóa model Ollama {payload.model} khỏi máy cục bộ.",
                reason="Model có thể đang được routing rule tham chiếu.",
            )
        return _action_json(action)

    @app.get("/api/v1/storage")
    async def storage(_session: AdminSession = Depends(require_session)) -> dict:
        current = context.settings_getter()
        async with context.database.session() as db:
            metric = await db.scalar(
                select(RuntimeMetric).order_by(RuntimeMetric.collected_at.desc()).limit(1)
            )
            previous = await db.scalar(
                select(RuntimeMetric).order_by(RuntimeMetric.collected_at.desc()).offset(1).limit(1)
            )
        return {
            "data_root": str(current.data_dir),
            "current": _metric_json(metric),
            "previous": _metric_json(previous),
            "growth": (
                {
                    "data_bytes": (metric.data_bytes or 0) - (previous.data_bytes or 0),
                    "vector_bytes": (metric.vector_bytes or 0) - (previous.vector_bytes or 0),
                    "media_bytes": (metric.media_bytes or 0) - (previous.media_bytes or 0),
                }
                if metric and previous
                else None
            ),
        }

    @app.post("/api/v1/storage/cleanup-preview")
    async def storage_cleanup_preview(
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        current = context.settings_getter()
        async with context.database.session() as db:
            report = await cleanup_storage(
                db,
                context.vectors_getter(),
                media_root=current.data_dir / "downloads",
                dry_run=True,
            )
        return asdict(report)

    @app.post("/api/v1/storage/cleanup-action", status_code=status.HTTP_201_CREATED)
    async def storage_cleanup_action(
        _session: AdminSession = Depends(require_write_session),
    ) -> dict:
        current = context.settings_getter()
        async with context.database.session() as db:
            report = await cleanup_storage(
                db,
                context.vectors_getter(),
                media_root=current.data_dir / "downloads",
                dry_run=True,
            )
            action = await pending.create(
                db,
                action_type="storage_cleanup",
                requested_by=context.owner_id,
                payload={"expected": asdict(report)},
                preview=(
                    f"Xóa {report.expired_messages} message hết retention/vượt quota; "
                    f"{report.orphan_vectors} orphan/vector vượt quota; "
                    f"{report.media_files} media file."
                ),
                reason="Storage cleanup được yêu cầu từ dashboard local.",
            )
        return _action_json(action)

    @app.get("/api/v1/workers")
    async def workers(_session: AdminSession = Depends(require_session)) -> dict:
        async with context.database.session() as db:
            metrics = list(
                (
                    await db.scalars(
                        select(RuntimeMetric)
                        .order_by(RuntimeMetric.collected_at.desc())
                        .limit(60)
                    )
                ).all()
            )
            jobs = list(
                (
                    await db.scalars(
                        select(BackgroundJob)
                        .where(
                            BackgroundJob.status.in_(
                                ["queued", "running", "paused", "pause_requested", "failed"]
                            )
                        )
                        .order_by(BackgroundJob.updated_at.desc())
                        .limit(200)
                    )
                ).all()
            )
        scheduler_rows = []
        scheduler = context.scheduler_getter() if context.scheduler_getter else None
        if scheduler:
            for row in scheduler.get_jobs():
                scheduler_rows.append(
                    {
                        "id": row.id,
                        "next_run_time": _utc(row.next_run_time),
                        "max_instances": row.max_instances,
                        "pending": row.pending,
                    }
                )
        return {
            "scheduler": scheduler_rows,
            "latest_metric": _metric_json(metrics[0] if metrics else None),
            "history": [_metric_json(row) for row in metrics],
            "jobs": [_job_json(row) for row in jobs],
        }

    @app.get("/api/v1/audit")
    async def audit_log(
        query: str = Query(default="", max_length=200),
        outcome: str | None = Query(default=None, max_length=32),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=100, ge=1, le=500),
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        conditions = []
        if query:
            pattern = f"%{query}%"
            conditions.append(
                or_(
                    AuditLog.action.ilike(pattern),
                    AuditLog.target_id.ilike(pattern),
                    AuditLog.reason.ilike(pattern),
                )
            )
        if outcome:
            conditions.append(AuditLog.outcome == outcome)
        async with context.database.session() as db:
            total = int(
                (await db.scalar(select(func.count(AuditLog.id)).where(*conditions))) or 0
            )
            rows = list(
                (
                    await db.scalars(
                        select(AuditLog)
                        .where(*conditions)
                        .order_by(AuditLog.occurred_at.desc())
                        .offset((page - 1) * page_size)
                        .limit(page_size)
                    )
                ).all()
            )
        return {
            "items": [
                {
                    "id": row.id,
                    "occurred_at": _utc(row.occurred_at),
                    "actor_id": str(row.actor_id) if row.actor_id is not None else None,
                    "action": row.action,
                    "target_type": row.target_type,
                    "target_id": row.target_id,
                    "outcome": row.outcome,
                    "reason": row.reason,
                    "details": redact(row.details_redacted),
                    "correlation_id": row.correlation_id,
                }
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    @app.get("/api/v1/events")
    async def event_stream(
        request: Request,
        _session: AdminSession = Depends(require_session),
    ) -> StreamingResponse:
        async def generate():
            event_id = 0
            while not await request.is_disconnected():
                async with context.database.session() as db:
                    pending_count = int(
                        (
                            await db.scalar(
                                select(func.count(PendingAction.action_id)).where(
                                    PendingAction.status == "pending",
                                    PendingAction.expires_at > datetime.now(UTC),
                                )
                            )
                        )
                        or 0
                    )
                    status_rows = (
                        await db.execute(
                            select(BackgroundJob.status, func.count(BackgroundJob.id))
                            .group_by(BackgroundJob.status)
                        )
                    ).all()
                    latest_metric = await db.scalar(
                        select(RuntimeMetric)
                        .order_by(RuntimeMetric.collected_at.desc())
                        .limit(1)
                    )
                    latest_audit_id = int(
                        (await db.scalar(select(func.max(AuditLog.id)))) or 0
                    )
                event_id += 1
                payload = {
                    "pending_actions": pending_count,
                    "jobs": dict(status_rows),
                    "runtime": _metric_json(latest_metric),
                    "latest_audit_id": latest_audit_id,
                    "sent_at": datetime.now(UTC).isoformat(),
                }
                yield (
                    f"id: {event_id}\n"
                    "event: dashboard-snapshot\n"
                    f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                )
                await asyncio.sleep(2)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/docs")
    async def documents_catalog(
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        root = Path(__file__).resolve().parents[3]
        return {
            "items": [
                {
                    "id": document_id,
                    "name": file_name,
                    "description": description,
                    "available": (root / file_name).is_file(),
                }
                for document_id, (file_name, description) in DOCUMENTS.items()
            ]
        }

    @app.get("/api/v1/docs/{document_id}")
    async def document_content(
        document_id: str,
        _session: AdminSession = Depends(require_session),
    ) -> dict:
        document = DOCUMENTS.get(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Tài liệu không tồn tại.")
        file_name, description = document
        path = Path(__file__).resolve().parents[3] / file_name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Tài liệu chưa được tạo.")
        return {
            "id": document_id,
            "name": file_name,
            "description": description,
            "content": path.read_text(encoding="utf-8"),
        }

    static_root = settings.resolved_dashboard_dist_path.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def dashboard_static(path: str, request: Request):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint không tồn tại.")
        if static_root.is_dir():
            candidate = (static_root / path).resolve()
            try:
                candidate.relative_to(static_root)
            except ValueError:
                raise HTTPException(status_code=404, detail="Không tìm thấy tài nguyên.") from None
            if candidate.is_file():
                return FileResponse(candidate)
            index = static_root / "index.html"
            if index.is_file() and "text/html" in request.headers.get("accept", "text/html"):
                return FileResponse(index)
        return JSONResponse(
            {
                "service": "Telegram AI Personal Assistant Admin API",
                "status": "ready",
                "dashboard_built": static_root.is_dir(),
                "login_hint": (
                    "Chạy `.\\.venv\\Scripts\\tg-assistant.exe dashboard-code` "
                    "trong PowerShell tại thư mục dự án."
                ),
            }
        )

    return app


def secrets_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


def _policy_json(policy: TelegramChatPolicy | None) -> dict:
    return {
        "allowed": bool(policy and policy.allowed),
        "template": policy.template if policy else None,
        "ai_mode": policy.ai_mode if policy else "inherit",
        "preferred_cloud_provider": policy.preferred_cloud_provider if policy else None,
        "cloud_fallback": bool(policy and policy.cloud_fallback),
        "retention_days": policy.retention_days if policy else None,
        "max_messages": policy.max_messages if policy else None,
        "max_storage_mb": policy.max_storage_mb if policy else None,
        "max_vectors": policy.max_vectors if policy else None,
        "ai_efficiency_preset": policy.ai_efficiency_preset if policy else None,
        "filtering_level": policy.filtering_level if policy else "standard",
        "rag_top_k": policy.rag_top_k if policy else None,
        "rag_max_context_tokens": policy.rag_max_context_tokens if policy else None,
        "revoked_at": _utc(policy.revoked_at) if policy else None,
    }


def _activity_state(item: dict, inactive_days: int) -> str:
    last_message = item.get("activity", {}).get("last_message_at")
    if not last_message:
        return "unknown"
    value = _utc(last_message)
    assert value is not None
    return (
        "inactive"
        if value <= datetime.now(UTC) - timedelta(days=inactive_days)
        else "active"
    )


async def _group_rows(
    session: AsyncSession,
    chats: list[TelegramChat],
    *,
    include_permissions: bool = False,
) -> list[dict]:
    if not chats:
        return []
    chat_ids = [chat.chat_id for chat in chats]
    policies = {
        row.chat_id: row
        for row in (
            await session.scalars(
                select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id.in_(chat_ids))
            )
        ).all()
    }
    permissions: defaultdict[int, dict[str, bool]] = defaultdict(dict)
    for row in (
        await session.scalars(
            select(TelegramChatPermission).where(TelegramChatPermission.chat_id.in_(chat_ids))
        )
    ).all():
        permissions[int(row.chat_id)][row.permission] = row.enabled
    sources = {
        row.chat_id: row
        for row in (
            await session.scalars(
                select(KnowledgeSource).where(KnowledgeSource.chat_id.in_(chat_ids))
            )
        ).all()
    }
    activity_rows = (
        await session.execute(
            select(
                TelegramMessage.chat_id,
                func.max(TelegramMessage.sent_at),
                func.count(TelegramMessage.id),
            )
            .where(TelegramMessage.chat_id.in_(chat_ids))
            .group_by(TelegramMessage.chat_id)
        )
    ).all()
    activity = {
        int(chat_id): {
            "last_message_at": _utc(last_message_at),
            "observed_messages": int(message_count or 0),
        }
        for chat_id, last_message_at, message_count in activity_rows
    }
    auto_ids = set(
        (
            await session.scalars(
                select(ModerationRule.chat_id).where(
                    ModerationRule.chat_id.in_(chat_ids),
                    ModerationRule.name == "non_admin_external_link_auto_delete",
                    ModerationRule.enabled.is_(True),
                )
            )
        ).all()
    )
    rows = []
    for chat in chats:
        policy = policies.get(chat.chat_id)
        source = sources.get(chat.chat_id)
        enabled = permissions.get(chat.chat_id, {})
        row = {
            "chat_id": str(chat.chat_id),
            "title": chat.title,
            "username": chat.username,
            "chat_type": chat.chat_type,
            "account_rights": chat.account_rights or {},
            "last_seen_at": _utc(chat.last_seen_at),
            "activity": {
                **activity.get(
                    chat.chat_id,
                    {"last_message_at": None, "observed_messages": 0},
                ),
                "state_60d": "unknown",
            },
            "policy": _policy_json(policy),
            "group_ai_ask": bool(enabled.get(PermissionName.GROUP_AI_ASK.value)),
            "auto_link_moderation": chat.chat_id in auto_ids,
            "knowledge": (
                {
                    "status": source.status,
                    "mysql_message_count": source.mysql_message_count,
                    "vector_count": source.vector_count,
                    "storage_bytes": source.message_storage_bytes + source.media_storage_bytes,
                    "last_job_id": source.last_job_id,
                    "last_error": str(redact(source.last_error)) if source.last_error else None,
                    "last_learned_at": _utc(source.last_learned_at),
                }
                if source
                else {
                    "status": "not_learned",
                    "mysql_message_count": 0,
                    "vector_count": 0,
                    "storage_bytes": 0,
                    "last_job_id": None,
                    "last_error": None,
                    "last_learned_at": None,
                }
            ),
        }
        row["activity"]["state_60d"] = _activity_state(row, 60)
        if include_permissions:
            row["permissions"] = {
                permission.value: bool(enabled.get(permission.value)) for permission in PermissionName
            }
        rows.append(row)
    return rows


def _update_job_state(job: BackgroundJob, operation: str) -> None:
    now = datetime.now(UTC)
    if operation == "pause":
        if job.status not in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="Job hiện không thể tạm dừng.")
        job.status = "pause_requested" if job.status == "running" else "paused"
        job.paused_at = now
        if job.status == "paused":
            job.locked_at = None
            job.locked_by = None
        return
    if operation == "resume":
        if job.status != "paused":
            raise HTTPException(status_code=409, detail="Chỉ job đang tạm dừng mới tiếp tục được.")
        job.status = "queued"
        job.paused_at = None
        job.run_after = now
        job.locked_at = None
        job.locked_by = None
        return
    if operation == "retry":
        if job.status != "failed":
            raise HTTPException(status_code=409, detail="Chỉ job lỗi mới được retry.")
        job.status = "queued"
        job.attempts = 0
        job.run_after = now
        job.locked_at = None
        job.locked_by = None
        job.paused_at = None
        job.last_error = None


def _metric_json(metric: RuntimeMetric | None) -> dict | None:
    if not metric:
        return None
    return {
        "id": metric.id,
        "collected_at": _utc(metric.collected_at),
        "process_id": metric.process_id,
        "process_name": metric.process_name,
        "rss_bytes": metric.rss_bytes,
        "cpu_percent": metric.cpu_percent,
        "vram_bytes": metric.vram_bytes,
        "data_bytes": metric.data_bytes,
        "vector_bytes": metric.vector_bytes,
        "media_bytes": metric.media_bytes,
        "queued_jobs": metric.queued_jobs,
        "running_jobs": metric.running_jobs,
        "paused_jobs": metric.paused_jobs,
        "details": redact(metric.details or {}),
    }
