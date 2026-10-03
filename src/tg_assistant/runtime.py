from __future__ import annotations

import asyncio
import getpass
import re
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic

import structlog
import typer
import uvicorn
from aiogram import Bot
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .admin_api import AdminContext, create_admin_app, ensure_dashboard_secret
from .ai.budget import BudgetService
from .ai.engine import AiEngine
from .ai.local_first import embedding_decision
from .ai.rag import (
    RagService,
    SourceEvidence,
    citation_appendix,
    rag_time_window,
    source_context,
    telegram_message_url,
    without_source_references,
)
from .ai.router import AiRouter
from .ai.vector import LocalVectorStore
from .config import Settings, get_settings, save_settings_env
from .db.base import Database
from .db.models import (
    AiMemory,
    AppSetting,
    AuditLog,
    BackgroundJob,
    KnowledgeSource,
    PendingAction,
    PermissionName,
    Reminder,
    TaskStatus,
    TelegramAccount,
    TelegramAttachment,
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
    TelegramMessage,
)
from .logging import configure_logging
from .paths import ensure_runtime_dirs
from .policy import PolicyEngine, SlidingWindowLimiter
from .security import SecretStore, contains_secret, redact
from .services.actions import PendingActionService
from .services.coingecko import (
    CoinGeckoClient,
    CoinGeckoError,
    extract_price_asset,
    format_coin_price,
)
from .services.daily_digest import referenced_item_indexes
from .services.history_export import matches_history_delete_message
from .services.memory import MemoryService
from .services.ollama import OllamaPullCancelled, OllamaService
from .services.operations import (
    allowed_index_count,
    cleanup_storage,
    collect_runtime_metric,
    refresh_source_usage,
)
from .services.revocation import (
    AuthorizationRevoked,
    AuthorizedAnswer,
    RevocationService,
    require_authorization,
    source_epoch,
    validate_action_epoch,
    validate_answer,
)
from .services.tasks import TaskService
from .services.vector_reliability import ensure_vector_store_registry
from .telegram.control_bot import ControlBot, telegram_html_chunks
from .telegram.pairing import PairingCode
from .telegram.user_client import UserClientAdapter, mask_phone

log = structlog.get_logger()

NEWS_QUESTION_MARKERS = (
    "tin tức",
    "tin mới",
    "tin nóng",
    "mới nhất",
    "có gì mới",
    "cập nhật",
    "diễn biến",
    "tình hình",
    "thông báo mới",
    "vừa công bố",
    "vừa xảy ra",
    "gần đây",
    "tuần qua",
    "7 ngày",
    "hôm nay",
    "24 giờ",
    "24h",
    "news",
    "latest",
    "breaking",
    "announcement",
    "thị trường",
)
CURRENT_GROUP_QUESTION_MARKERS = (
    "chủ đề đang thảo luận",
    "đang thảo luận gì",
    "đang nói gì",
    "đang bàn gì",
    "nhóm này",
    "group này",
    "trong nhóm này",
    "trong group này",
    "cuộc trò chuyện",
    "tóm tắt hội thoại",
    "tóm tắt đoạn chat",
    "tóm tắt nhóm",
    "tóm tắt group",
    "mọi người đang nói",
    "mọi người đang bàn",
)
SENDER_HISTORY_QUESTION_MARKERS = (
    "đã nói",
    "nói về",
    "đã nhắn",
    "nhắn về",
    "tin nhắn của",
    "lịch sử nhắn",
    "chủ đề gì",
    "bàn về",
    "thảo luận về",
)
TELEGRAM_USERNAME_RE = re.compile(r"(?<!\w)@([A-Za-z0-9_]{5,32})")


def question_requests_news(question: str) -> bool:
    """Detect requests for current news where group replies must show sources."""
    normalized = " ".join(question.casefold().split())
    return any(marker in normalized for marker in NEWS_QUESTION_MARKERS)


def question_targets_current_group(question: str) -> bool:
    """Detect questions that ask about the conversation in the invoking group."""
    normalized = " ".join(question.casefold().split())
    return any(marker in normalized for marker in CURRENT_GROUP_QUESTION_MARKERS)


def sender_history_username(question: str) -> str | None:
    """Return the mentioned account when the question asks what that sender discussed."""
    normalized = " ".join(question.casefold().split())
    if not any(marker in normalized for marker in SENDER_HISTORY_QUESTION_MARKERS):
        return None
    match = TELEGRAM_USERNAME_RE.search(question)
    return match.group(1) if match else None


def embedding_batches(
    rows: list[TelegramMessage],
    *,
    max_input_tokens: int,
    max_items: int = 100,
) -> list[list[TelegramMessage]]:
    """Pack messages into bounded embedding requests while preserving order."""
    batches: list[list[TelegramMessage]] = []
    current: list[TelegramMessage] = []
    current_tokens = 0
    for row in rows:
        text = (row.text or "").strip()
        if not text or contains_secret(text):
            continue
        estimated_tokens = max(1, len(text) // 4)
        if estimated_tokens > max_input_tokens:
            continue
        if current and (
            len(current) >= max_items or current_tokens + estimated_tokens > max_input_tokens
        ):
            batches.append(current)
            current, current_tokens = [], 0
        current.append(row)
        current_tokens += estimated_tokens
    if current:
        batches.append(current)
    return batches


@dataclass(frozen=True, slots=True)
class EmbeddingIndexResult:
    processed: int = 0
    indexed: int = 0
    reused: int = 0
    filtered: int = 0
    duplicate: int = 0
    skipped: int = 0
    failed: int = 0


def knowledge_rows_query(
    chat_id: int,
    *,
    limit: int,
    checkpoint_id: int = 0,
    since: datetime | None = None,
):
    """Select rows that extend the shared corpus without re-embedding old rows."""
    query = select(TelegramMessage).where(
        TelegramMessage.chat_id == chat_id,
        TelegramMessage.is_deleted.is_(False),
        TelegramMessage.text.is_not(None),
    )
    if checkpoint_id > 0:
        query = query.where(TelegramMessage.id > checkpoint_id)
        if since is not None:
            query = query.where(TelegramMessage.sent_at >= since)
        return query.order_by(TelegramMessage.id.asc()).limit(limit)
    if since is not None:
        query = query.where(TelegramMessage.sent_at >= since)
    return query.order_by(TelegramMessage.sent_at.desc()).limit(limit)


def knowledge_checkpoint_key(provider: str, chat_id: int) -> str:
    """Keep Ollama checkpoints independent from the cloud vector store."""
    if provider == "ollama":
        return f"knowledge_checkpoint:ollama:{chat_id}"
    return f"knowledge_checkpoint:{chat_id}"


def requeue_interrupted_learning_job(
    job: BackgroundJob,
    *,
    now: datetime | None = None,
) -> None:
    if job.status in {"cancelled", "uncertain"}:
        return
    job.status = "queued"
    job.attempts = max(0, job.attempts - 1)
    job.run_after = now or datetime.now(UTC)
    job.locked_by = None
    job.locked_at = None
    job.last_error = None


async def learning_backlog_size(session) -> int:
    return int(
        (
            await session.scalar(
                select(func.count(BackgroundJob.id)).where(
                    BackgroundJob.job_type == "learn_group",
                    BackgroundJob.status.in_(["queued", "running", "pause_requested"]),
                )
            )
        )
        or 0
    )


async def pause_learning_jobs(session) -> int:
    jobs = list(
        (
            await session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == "learn_group",
                    BackgroundJob.status.in_(["queued", "running"]),
                )
            )
        ).all()
    )
    now = datetime.now(UTC)
    for job in jobs:
        job.status = "pause_requested" if job.status == "running" else "paused"
        job.paused_at = now
        if job.status == "paused":
            job.locked_by = None
            job.locked_at = None
        payload = job.payload or {}
        if payload.get("chat_id") is not None:
            source = await session.get(KnowledgeSource, int(payload["chat_id"]))
            if source:
                source.status = job.status
    return len(jobs)


async def resume_learning_jobs(session) -> int:
    jobs = list(
        (
            await session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == "learn_group",
                    BackgroundJob.status == "paused",
                )
            )
        ).all()
    )
    for job in jobs:
        job.status = "queued"
        job.paused_at = None
        job.run_after = datetime.now(UTC)
        payload = job.payload or {}
        if payload.get("chat_id") is not None:
            source = await session.get(KnowledgeSource, int(payload["chat_id"]))
            if source:
                source.status = "queued"
    return len(jobs)


async def honor_learning_pause(session, job: BackgroundJob) -> bool:
    if job.status != "pause_requested":
        return False
    job.status = "paused"
    job.paused_at = job.paused_at or datetime.now(UTC)
    job.locked_by = None
    job.locked_at = None
    payload = job.payload or {}
    if payload.get("chat_id") is not None:
        source = await session.get(KnowledgeSource, int(payload["chat_id"]))
        if source:
            source.status = "paused"
    return True


async def recover_interrupted_learning_jobs(session) -> int:
    jobs = list(
        (
            await session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == "learn_group",
                    BackgroundJob.status == "running",
                )
            )
        ).all()
    )
    for job in jobs:
        requeue_interrupted_learning_job(job)
    return len(jobs)


AI_PROVIDERS = {"openai", "openrouter", "ollama", "off"}


def _save_env_values(values: dict[str, str]) -> None:
    save_settings_env(values)


def _save_ai_provider(provider: str) -> None:
    _save_env_values({"TG_ASSISTANT_AI_PROVIDER": provider})


def configure_ai_provider(
    store: SecretStore,
    *,
    provider: str | None = None,
    prompt_key: bool = True,
) -> Settings:
    settings = get_settings()
    chosen = (provider or "").strip().lower()
    if not chosen:
        chosen = (
            typer.prompt(
                "Nhà cung cấp AI (openai/openrouter/ollama/off)",
                default=settings.ai_provider,
            )
            .strip()
            .lower()
        )
    if chosen not in AI_PROVIDERS:
        raise typer.BadParameter("Nhà cung cấp AI phải là openai, openrouter, ollama hoặc off.")
    _save_ai_provider(chosen)
    settings = get_settings()
    if chosen == "ollama":
        if prompt_key:
            chat_model = typer.prompt(
                "Model chat Ollama",
                default=settings.ollama_primary_model,
            ).strip()
            embedding_model = typer.prompt(
                "Model embedding Ollama",
                default=settings.ollama_embedding_model,
            ).strip()
            if not chat_model or not embedding_model:
                raise typer.BadParameter("Tên model Ollama không được để trống.")
            _save_env_values(
                {
                    "TG_ASSISTANT_OLLAMA_PRIMARY_MODEL": chat_model,
                    "TG_ASSISTANT_OLLAMA_EMBEDDING_MODEL": embedding_model,
                }
            )
            settings = get_settings()
        typer.echo(
            "Đã chọn Ollama local; không cần API key. "
            "Model phải được tải sẵn bằng `ollama pull <tên-model>`."
        )
        return settings
    secret_name = settings.ai_secret_name
    if not secret_name:
        typer.echo("AI đã tắt; Telegram, MySQL, tìm kiếm local và chống spam vẫn hoạt động.")
        return settings
    existing = bool(store.get(secret_name))
    if prompt_key or not existing:
        configure_ai_key(store, chosen)
    return settings


def configure_ai_key(store: SecretStore, provider: str) -> bool:
    if provider not in {"openai", "openrouter"}:
        raise typer.BadParameter("Chỉ OpenAI và OpenRouter sử dụng API key.")
    secret_name = f"{provider}_api_key"
    label = "OPENAI_API_KEY" if provider == "openai" else "OPENROUTER_API_KEY"
    existing = bool(store.get(secret_name))
    hint = "Enter để giữ key hiện tại" if existing else "nhập ẩn; Enter để bỏ qua"
    value = getpass.getpass(f"{label} ({hint}): ").strip()
    if value:
        if provider == "openrouter" and not value.startswith("sk-or-v1-"):
            raise typer.BadParameter("OPENROUTER_API_KEY phải bắt đầu bằng sk-or-v1-.")
        if provider == "openai" and not value.startswith("sk-"):
            raise typer.BadParameter("OPENAI_API_KEY phải bắt đầu bằng sk-.")
        store.set(secret_name, value)
        _save_env_values(
            {f"TG_ASSISTANT_{provider.upper()}_API_KEY_SOURCE": ("windows_credential_manager")}
        )
        return True
    return existing


def prompt_secrets(store: SecretStore, *, include_openai: bool = True) -> None:
    prompts = {
        "telegram_api_id": "TELEGRAM_API_ID",
        "telegram_api_hash": "TELEGRAM_API_HASH",
        "telegram_phone": "Số điện thoại Telegram",
        "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    }
    values: dict[str, str] = {}
    for name, label in prompts.items():
        existing = bool(store.get(name))
        hint = "Enter để giữ giá trị hiện tại" if existing else "nhập ẩn"
        value = getpass.getpass(f"{label} ({hint}): ").strip()
        if value:
            values[name] = value
        elif not existing:
            raise typer.BadParameter(f"Thiếu {label}.")
    api_id = values.get("telegram_api_id") or store.get("telegram_api_id") or ""
    try:
        int(api_id)
    except ValueError as exc:
        raise ValueError("TELEGRAM_API_ID phải là số") from exc
    for name, value in values.items():
        store.set(name, value)
    if include_openai:
        settings = configure_ai_provider(store, prompt_key=True)
        selected_cloud = (
            settings.ai_provider if settings.ai_provider in {"openai", "openrouter"} else None
        )
        for provider in ("openai", "openrouter"):
            if provider != selected_cloud:
                configure_ai_key(store, provider)
    configure_coingecko_key(store, prompt_key=True)


def configure_coingecko_key(
    store: SecretStore,
    *,
    prompt_key: bool = True,
) -> bool:
    existing = bool(store.get("coingecko_api_key"))
    if prompt_key:
        hint = (
            "Enter để giữ key hiện tại" if existing else "nhập ẩn; Enter để bỏ qua và cấu hình sau"
        )
        value = getpass.getpass(f"COINGECKO_API_KEY ({hint}): ").strip()
        if value:
            if not value.startswith("CG-"):
                raise typer.BadParameter("COINGECKO_API_KEY phải bắt đầu bằng CG-.")
            store.set("coingecko_api_key", value)
            _save_env_values(
                {"TG_ASSISTANT_COINGECKO_API_KEY_SOURCE": ("windows_credential_manager")}
            )
            existing = True
    return existing


async def verify_bot_token(token: str) -> str:
    bot = Bot(token)
    try:
        me = await bot.get_me()
        return me.username
    finally:
        await bot.session.close()


def make_database(settings: Settings, store: SecretStore) -> Database:
    password = store.get("database_password")
    if not password:
        raise RuntimeError("Chưa cấu hình MySQL. Chạy tg-assistant reconfigure hoặc start.bat.")
    return Database(settings.database_url(password), pool_size=settings.database_pool_size)


def make_user_client(
    settings: Settings, store: SecretStore, policy: PolicyEngine, paths: dict
) -> UserClientAdapter:
    api_id, api_hash = store.get("telegram_api_id"), store.get("telegram_api_hash")
    if not api_id or not api_hash:
        raise RuntimeError("Chưa cấu hình Telegram API")
    return UserClientAdapter(
        api_id=int(api_id),
        api_hash=api_hash,
        session_path=paths["sessions"] / "account.session",
        encrypted_path=paths["sessions"] / "account.session.enc",
        store=store,
        policy=policy,
    )


def make_ai_engine(
    settings: Settings,
    store: SecretStore,
    budget: BudgetService,
) -> AiEngine:
    secret_name = settings.ai_secret_name
    api_key = store.get(secret_name) if secret_name else None
    return AiEngine(
        api_key=api_key,
        budget=budget,
        provider=settings.ai_provider,
        base_url=settings.active_ai_base_url,
        model=settings.active_ai_model,
        embedding_model=settings.provider_embedding_model,
        max_output_tokens=settings.max_output_tokens_per_request,
        max_input_tokens=settings.max_input_tokens_per_request,
        max_requests_per_minute=settings.max_ai_requests_per_minute,
    )


def make_local_embedding_engine(
    settings: Settings,
    store: SecretStore,
    budget: BudgetService,
) -> AiEngine:
    candidate = settings.model_copy(update={"ai_provider": "ollama"})
    return make_ai_engine(candidate, store, budget)


def make_ai_router(
    settings: Settings,
    store: SecretStore,
    budget: BudgetService,
    primary: AiEngine,
) -> AiRouter:
    engines: dict[str, AiEngine] = {}
    if settings.ai_provider != "off":
        engines[settings.ai_provider] = primary
    for provider in ("ollama", "openai", "openrouter"):
        if provider in engines:
            continue
        candidate = settings.model_copy(update={"ai_provider": provider})
        secret_name = candidate.ai_secret_name
        if secret_name and not store.get(secret_name):
            continue
        engines[provider] = make_ai_engine(candidate, store, budget)
    return AiRouter(engines, default_provider=settings.ai_provider)


async def bootstrap() -> None:
    settings, store, paths = get_settings(), SecretStore(), ensure_runtime_dirs()
    for key in ("telegram_api_id", "telegram_api_hash", "telegram_phone", "telegram_bot_token"):
        if not store.get(key):
            prompt_secrets(store)
            break
    username = await verify_bot_token(store.get("telegram_bot_token") or "")
    print(f"Đã xác minh bot @{username}")
    database, policy = make_database(settings, store), PolicyEngine()
    user = make_user_client(settings, store, policy, paths)
    try:
        # Telethon tự hỏi OTP/2FA trong terminal và không lưu chúng.
        me = await user.authenticate(store.get("telegram_phone") or "")
        async with database.session() as session:
            account = await session.scalar(select(TelegramAccount).limit(1))
            if not account:
                account = TelegramAccount()
                session.add(account)
            account.telegram_user_id, account.username = int(me.id), me.username
            account.phone_masked, account.last_authenticated_at = (
                mask_phone(me.phone),
                datetime.now(UTC),
            )
            await user.discover_dialogs(session)
            paired = account.is_owner_paired
        if not paired:
            pairing = PairingCode.create(int(me.id), settings.pairing_ttl_seconds)
            print(f"Mở @{username} và gửi:\n/pair {pairing.value}\nMã hết hạn sau 5 phút.")
            control = ControlBot(
                store.get("telegram_bot_token") or "",
                owner_id=int(me.id),
                database=database,
                policy=policy,
                pairing=pairing,
            )
            bot_task = asyncio.create_task(control.run())
            try:
                for _ in range(settings.pairing_ttl_seconds):
                    if pairing.used:
                        async with database.session() as session:
                            account = await session.scalar(select(TelegramAccount).limit(1))
                            if account:
                                account.is_owner_paired = True
                        print("[✓] Ghép nối chủ sở hữu thành công.")
                        break
                    await asyncio.sleep(1)
                else:
                    raise TimeoutError("Mã pairing đã hết hạn")
            finally:
                await control.dp.stop_polling()
                bot_task.cancel()
                await asyncio.gather(bot_task, return_exceptions=True)
                await control.close()
    finally:
        await user.close()
        await database.close()


class Application:
    async def _source_fence(self, chat_id: int, permission: PermissionName, epoch: int | None = None) -> int:
        # A separate transaction observes committed revocations after network I/O.
        async with self.database.session() as authorization_session:
            return await require_authorization(authorization_session, chat_id, permission, epoch)

    async def _job_fence(self, session, job: BackgroundJob, permission: PermissionName) -> int:
        payload = job.payload or {}
        if job.status not in {"queued", "running"}:
            raise AuthorizationRevoked("job_cancelled")
        if int(payload.get("owner_id", -1)) != int(self.user.owner_id):
            raise AuthorizationRevoked("not_owner")
        return await self._source_fence(int(payload["chat_id"]), permission, int(payload.get("authorization_epoch", 0)))

    async def _action_fence(self, action: PendingAction) -> None:
        async with self.database.session() as current:
            await validate_action_epoch(current, action)

    async def _cancel_revoked_job(self, job_id: str) -> None:
        async with self.database.session() as session:
            job = await session.get(BackgroundJob, job_id)
            if job:
                job.status = "uncertain" if (job.payload or {}).get("external_effect_started") else "cancelled"
                job.last_error = "source_authorization_revoked"
                job.locked_by = None
                job.locked_at = None
                chat_id = (job.payload or {}).get("chat_id")
                source = await session.get(KnowledgeSource, int(chat_id)) if chat_id is not None else None
                if source:
                    source.status = "revoked"
                    source.requested_for_learning = False

    def __init__(self) -> None:
        self.settings, self.store, self.paths = get_settings(), SecretStore(), ensure_runtime_dirs()
        configure_logging(self.settings.log_level, self.paths["logs"])
        self.database = make_database(self.settings, self.store)
        self.policy = PolicyEngine()
        self.user = make_user_client(self.settings, self.store, self.policy, self.paths)
        self.budget = BudgetService(
            self.settings.daily_ai_budget_usd,
            self.settings.monthly_ai_budget_usd,
            daily_token_limit=self.settings.daily_token_limit,
            monthly_token_limit=self.settings.monthly_token_limit,
            group_daily_token_limit=self.settings.group_daily_token_limit,
            feature_daily_token_limit=self.settings.feature_daily_token_limit,
            provider_daily_token_limit=self.settings.provider_daily_token_limit,
            max_cloud_fallbacks_per_day=self.settings.max_cloud_fallbacks_per_day,
        )
        self.ai = make_ai_engine(self.settings, self.store, self.budget)
        self.embedding_ai = make_local_embedding_engine(
            self.settings, self.store, self.budget
        )
        self.ai_router = make_ai_router(self.settings, self.store, self.budget, self.ai)
        self.coingecko = CoinGeckoClient(self.store.get("coingecko_api_key"))
        self.ollama = OllamaService(self.settings.ollama_base_url)
        vectors = None
        if self.embedding_ai.available and self.settings.enable_embeddings:
            try:
                vectors = LocalVectorStore(
                    self.settings.resolved_semantic_vector_path,
                    vector_size=self.settings.ollama_vector_size,
                )
            except Exception as exc:
                log.warning("qdrant_local_unavailable", error=str(exc))
        self.rag = RagService(
            self.policy,
            self.ai,
            vectors,
            self.ai_router,
            embedding_ai=self.embedding_ai,
            default_preset=self.settings.ai_efficiency_preset,
        )
        self.rag.database = self.database
        self.bot: ControlBot | None = None
        self.admin_server: uvicorn.Server | None = None
        self.scheduler = AsyncIOScheduler(timezone=self.settings.timezone)
        self.scheduler.add_listener(
            self._scheduler_event,
            EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
        )
        self.stopping = asyncio.Event()
        self._knowledge_lock = asyncio.Lock()
        self._group_ai_limiter = SlidingWindowLimiter(limit=3, seconds=60)

    @staticmethod
    def _scheduler_event(event) -> None:
        scheduled = getattr(event, "scheduled_run_time", None)
        lag_ms = (
            max(0.0, (datetime.now(UTC) - scheduled).total_seconds() * 1000) if scheduled else None
        )
        logger = log.warning if event.code in {EVENT_JOB_ERROR, EVENT_JOB_MISSED} else log.debug
        logger(
            "scheduler_job_event",
            job_id=getattr(event, "job_id", None),
            event_code=event.code,
            lag_ms=lag_ms,
            exception=str(getattr(event, "exception", "") or "") or None,
        )

    async def _activate_ollama(
        self,
        chat_model: str,
        embedding_model: str,
        vector_size: int,
    ) -> str:
        """Hot-switch the running application to validated local Ollama models."""
        candidate = self.settings.model_copy(
            update={
                "ai_provider": "ollama",
                "ollama_primary_model": chat_model,
                "ollama_embedding_model": embedding_model,
                "ollama_vector_size": vector_size,
            }
        )
        async with self._knowledge_lock:
            current_vectors = self.rag.vectors
            current_path = self.settings.resolved_semantic_vector_path
            target_path = candidate.resolved_semantic_vector_path
            reuse_vectors = current_vectors is not None and current_path == target_path
            new_vectors = current_vectors if reuse_vectors else None
            if not reuse_vectors and candidate.enable_embeddings:
                new_vectors = LocalVectorStore(
                    target_path,
                    vector_size=vector_size,
                )
            new_ai = make_ai_engine(candidate, self.store, self.budget)
            new_embedding_ai = make_local_embedding_engine(
                candidate, self.store, self.budget
            )
            new_router = make_ai_router(candidate, self.store, self.budget, new_ai)
            new_rag = RagService(
                self.policy,
                new_ai,
                new_vectors,
                new_router,
                embedding_ai=new_embedding_ai,
                default_preset=candidate.ai_efficiency_preset,
            )
            old_router, old_vectors = self.ai_router, current_vectors
            old_embedding_ai = self.embedding_ai
            save_settings_env(
                {
                    "TG_ASSISTANT_AI_PROVIDER": "ollama",
                    "TG_ASSISTANT_OLLAMA_PRIMARY_MODEL": chat_model,
                    "TG_ASSISTANT_OLLAMA_EMBEDDING_MODEL": embedding_model,
                    "TG_ASSISTANT_OLLAMA_VECTOR_SIZE": str(vector_size),
                }
            )
            self.settings = get_settings()
            self.ai = new_ai
            self.embedding_ai = new_embedding_ai
            self.ai_router, self.rag = new_router, new_rag
            self.rag.database = self.database
            if self.bot:
                self.bot.ai, self.bot.rag = new_ai, new_rag
            if old_vectors and not reuse_vectors:
                old_vectors.close()
            await old_router.close()
            await old_embedding_ai.close()
        return (
            f"Đã kích hoạt Ollama local.\n"
            f"Model chat: {chat_model}\n"
            f"Embedding: {embedding_model} ({vector_size} chiều)\n"
            f"Kho vector Local-first: {self.settings.resolved_semantic_vector_path}\n\n"
            "AI có thể trả lời ngay bằng dữ liệu keyword trong MySQL. "
            "Kho semantic Ollama sẽ được bổ sung tự động từ các nguồn đã cấp quyền."
        )

    async def _switch_ai_provider(self, provider: str) -> str:
        if provider == "ollama":
            settings = get_settings()
            dimension = await self.ollama.embedding_dimension(settings.ollama_embedding_model)
            return await self._activate_ollama(
                settings.ollama_primary_model,
                settings.ollama_embedding_model,
                dimension,
            )
        if provider not in {"openai", "openrouter", "off"}:
            raise ValueError("Nhà cung cấp AI không hợp lệ.")
        candidate = self.settings.model_copy(update={"ai_provider": provider})
        secret_name = candidate.ai_secret_name
        if secret_name and not self.store.get(secret_name):
            label = "OpenAI" if provider == "openai" else "OpenRouter"
            raise RuntimeError(
                f"Chưa có API key {label} trong Windows Credential Manager. "
                "Không nhận API key qua Telegram để tránh lộ secret."
            )
        async with self._knowledge_lock:
            current_vectors = self.rag.vectors
            target_path = candidate.resolved_semantic_vector_path
            reuse_vectors = bool(current_vectors)
            new_vectors = current_vectors
            if not new_vectors and candidate.enable_embeddings:
                new_vectors = LocalVectorStore(
                    target_path,
                    vector_size=candidate.ollama_vector_size,
                )
            new_ai = make_ai_engine(candidate, self.store, self.budget)
            new_router = make_ai_router(candidate, self.store, self.budget, new_ai)
            new_rag = RagService(
                self.policy,
                new_ai,
                new_vectors,
                new_router,
                embedding_ai=self.embedding_ai,
                default_preset=candidate.ai_efficiency_preset,
            )
            old_router, old_vectors = self.ai_router, current_vectors
            save_settings_env({"TG_ASSISTANT_AI_PROVIDER": provider})
            self.settings = get_settings()
            self.ai, self.ai_router, self.rag = new_ai, new_router, new_rag
            self.rag.database = self.database
            if self.bot:
                self.bot.ai, self.bot.rag = new_ai, new_rag
            if old_vectors and not reuse_vectors:
                old_vectors.close()
            await old_router.close()
        if provider == "off":
            return (
                "Đã tắt toàn bộ AI: OpenAI, OpenRouter, Ollama, suy luận và embedding. "
                "MySQL, tìm kiếm local, CoinGecko, đồng bộ và chống spam vẫn hoạt động."
            )
        return (
            f"Đã chuyển sang {provider}.\n"
            f"Model: {self.ai.model}\n"
            f"Embedding: {self.ai.embedding_model}\n"
            "Thay đổi đã áp dụng ngay, không cần khởi động lại bot."
        )

    async def _answer_from_message_rows(
        self,
        session,
        *,
        chat: TelegramChat,
        question: str,
        rows: list[TelegramMessage],
        empty_message: str,
        sender_label: str | None = None,
    ) -> str:
        evidence_rows: list[SourceEvidence] = []
        epoch = await self._source_fence(chat.chat_id, PermissionName.SEARCH_MESSAGES)
        contexts: list[str] = []
        context_chars = 0
        context_budget = min(36_000, max(6_000, self.ai.max_input_tokens * 3))
        title = str(redact(chat.title or f"Chat {chat.chat_id}"))
        for row in rows:
            text = str(redact((row.text or "").strip()))[:1200]
            if not text:
                continue
            evidence = SourceEvidence(
                chat_id=chat.chat_id,
                message_id=row.message_id,
                score=1.0,
                text=text,
                sent_at=row.sent_at,
                title=title,
                url=telegram_message_url(chat, row.message_id),
            )
            context = source_context(len(evidence_rows) + 1, evidence)
            if sender_label:
                context = f"Tài khoản đang truy cứu: {sender_label}\n{context}"
            if contexts and context_chars + len(context) > context_budget:
                break
            evidence_rows.append(evidence)
            contexts.append(context)
            context_chars += len(context)
        if not contexts:
            return empty_message
        include_citations = question_requests_news(question)
        await self._source_fence(chat.chat_id, PermissionName.SEARCH_MESSAGES, epoch)
        if hasattr(self, "policy") and hasattr(self, "ai_router"):
            route = await self.policy.ai_route(session, [chat.chat_id])
            answer = await self.ai_router.answer(
                session,
                question,
                contexts,
                route=route,
                include_source_refs=include_citations,
            )
        else:
            answer = await self.ai.answer(
                session,
                question,
                contexts,
                include_source_refs=include_citations,
            )
        if not include_citations:
            await self._source_fence(chat.chat_id, PermissionName.SEARCH_MESSAGES, epoch)
            return AuthorizedAnswer(without_source_references(answer), {chat.chat_id: epoch})
        referenced = referenced_item_indexes(answer, item_count=len(evidence_rows))
        cited = [evidence_rows[index] for index in referenced]
        await self._source_fence(chat.chat_id, PermissionName.SEARCH_MESSAGES, epoch)
        return AuthorizedAnswer((
            f"{answer.rstrip()}\n\n"
            f"{citation_appendix(cited, source_numbers=[index + 1 for index in referenced])}"
        ), {chat.chat_id: epoch})

    async def _answer_from_current_group(
        self,
        session,
        *,
        chat: TelegramChat,
        question: str,
        asked_message_id: int,
    ) -> str:
        """Summarize only the newest stored conversation from the invoking group."""
        since, until = rag_time_window()
        rows = list(
            (
                await session.scalars(
                    select(TelegramMessage)
                    .where(
                        TelegramMessage.chat_id == chat.chat_id,
                        TelegramMessage.message_id != asked_message_id,
                        TelegramMessage.sent_at >= since,
                        TelegramMessage.sent_at <= until,
                        TelegramMessage.is_deleted.is_(False),
                        TelegramMessage.text.is_not(None),
                    )
                    .order_by(TelegramMessage.sent_at.desc())
                    .limit(100)
                )
            ).all()
        )
        return await self._answer_from_message_rows(
            session,
            chat=chat,
            question=question,
            rows=rows,
            empty_message=(
                "Chưa có đủ hội thoại đã đồng bộ trong 7 ngày gần nhất của group này "
                "để xác định chủ đề đang thảo luận."
            ),
        )

    async def _answer_from_group_sender(
        self,
        session,
        *,
        chat: TelegramChat,
        question: str,
        asked_message_id: int,
        username: str,
    ) -> str:
        """Fetch and summarize one mentioned account's history in the invoking group."""
        sender_id, _synced = await self.user.sync_sender_history(
            session,
            chat_id=chat.chat_id,
            username=username,
            limit=100,
        )
        await session.flush()
        rows = list(
            (
                await session.scalars(
                    select(TelegramMessage)
                    .where(
                        TelegramMessage.chat_id == chat.chat_id,
                        TelegramMessage.sender_id == sender_id,
                        TelegramMessage.message_id != asked_message_id,
                        TelegramMessage.is_deleted.is_(False),
                        TelegramMessage.text.is_not(None),
                    )
                    .order_by(TelegramMessage.sent_at.desc())
                    .limit(100)
                )
            ).all()
        )
        return await self._answer_from_message_rows(
            session,
            chat=chat,
            question=question,
            rows=rows,
            empty_message=(
                f"Đã truy cứu lịch sử Telegram nhưng không tìm thấy tin nhắn nào của "
                f"@{username} trong group này."
            ),
            sender_label=f"@{username} (sender_id={sender_id})",
        )

    async def _handle_group_ai_ask(
        self,
        chat_id: int,
        requester_id: int,
        message_id: int,
        question: str,
    ) -> None:
        """Answer an authorized group invocation from the learned project corpus."""
        started_at = monotonic()
        username = self.user.username or "your_assistant_username"
        try:
            async with self.database.session() as session:
                chat = await session.scalar(
                    select(TelegramChat)
                    .join(
                        TelegramChatPolicy,
                        TelegramChatPolicy.chat_id == TelegramChat.chat_id,
                    )
                    .join(
                        TelegramChatPermission,
                        TelegramChatPermission.chat_id == TelegramChat.chat_id,
                    )
                    .where(
                        TelegramChat.chat_id == chat_id,
                        TelegramChat.chat_type.in_(("group", "supergroup")),
                        TelegramChatPolicy.allowed.is_(True),
                        TelegramChatPermission.permission == PermissionName.GROUP_AI_ASK.value,
                        TelegramChatPermission.enabled.is_(True),
                    )
                )
                if not chat:
                    return
                authorization_epoch = await require_authorization(session, chat_id, PermissionName.GROUP_AI_ASK)
                if not question:
                    answer = f"Dùng: @{username} /ask <câu hỏi>"
                elif len(question) > 1500:
                    answer = "Câu hỏi quá dài; vui lòng rút gọn còn tối đa 1.500 ký tự."
                elif contains_secret(question):
                    answer = "Câu hỏi có vẻ chứa thông tin bí mật nên không được gửi tới AI."
                elif not self._group_ai_limiter.allow(
                    requester_id,
                    f"{PermissionName.GROUP_AI_ASK.value}:{chat_id}",
                ):
                    answer = "Bạn đã hỏi quá nhanh; mỗi thành viên được tối đa 3 câu mỗi phút."
                else:
                    price_asset = extract_price_asset(question)
                    if price_asset:
                        coingecko = getattr(self, "coingecko", None)
                        if not coingecko or not coingecko.available:
                            answer = (
                                "CoinGecko chưa được cấu hình. Chủ bot cần chạy "
                                "`tg-assistant coingecko-key` trong terminal."
                            )
                        else:
                            try:
                                answer = format_coin_price(await coingecko.get_price(price_asset))
                            except CoinGeckoError as exc:
                                answer = str(exc)
                            except Exception as exc:
                                log.warning(
                                    "group_coingecko_price_failed",
                                    chat_id=chat_id,
                                    requester_id=requester_id,
                                    error=str(redact(str(exc))),
                                )
                                answer = "CoinGecko tạm thời không trả được giá; vui lòng thử lại."
                    else:
                        ai_setting = await session.get(AppSetting, "ai_enabled")
                        if ai_setting and ai_setting.value is False:
                            answer = "AI hiện đang tắt."
                        elif not self.ai.available:
                            answer = "AI hiện chưa sẵn sàng."
                        else:
                            await self._source_fence(chat_id, PermissionName.GROUP_AI_ASK, authorization_epoch)
                            await self.user.client.send_message(
                                chat_id,
                                "Đã nhận câu hỏi; đang rà dữ liệu Telegram phù hợp…",
                                parse_mode="html",
                                reply_to=message_id,
                            )
                            if target_username := sender_history_username(question):
                                try:
                                    answer = await self._answer_from_group_sender(
                                        session,
                                        chat=chat,
                                        question=question,
                                        asked_message_id=message_id,
                                        username=target_username,
                                    )
                                except ValueError:
                                    answer = (
                                        f"Không phân giải được @{target_username} thành "
                                        "tài khoản Telegram hợp lệ."
                                    )
                                except RuntimeError as exc:
                                    answer = str(exc)
                                except Exception as exc:
                                    log.warning(
                                        "group_sender_history_failed",
                                        chat_id=chat_id,
                                        requester_id=requester_id,
                                        target_username=target_username,
                                        error=str(redact(str(exc))),
                                    )
                                    answer = (
                                        f"Không thể truy xuất lịch sử của @{target_username} "
                                        "trong group này; vui lòng thử lại sau."
                                    )
                            elif question_targets_current_group(question):
                                try:
                                    answer = await self._answer_from_current_group(
                                        session,
                                        chat=chat,
                                        question=question,
                                        asked_message_id=message_id,
                                    )
                                except RuntimeError as exc:
                                    answer = str(exc)
                                except Exception as exc:
                                    log.warning(
                                        "group_context_answer_failed",
                                        chat_id=chat_id,
                                        requester_id=requester_id,
                                        error=str(redact(str(exc))),
                                    )
                                    answer = "AI tạm thời không đọc được hội thoại của group này."
                            else:
                                # Group /ask is scoped to the invoking chat. Shared
                                # corpus queries remain an owner-only action, which
                                # prevents unrelated group history reaching members.
                                try:
                                    include_citations = question_requests_news(question)
                                    answer = await self.rag.answer(
                                        session,
                                        question,
                                        actor_id=int(self.user.owner_id),
                                        owner_id=int(self.user.owner_id),
                                        chat_ids=[chat_id],
                                        include_citations=include_citations,
                                    )
                                except RuntimeError as exc:
                                    answer = str(exc)
                                except Exception as exc:
                                    log.warning(
                                        "group_ai_answer_failed",
                                        chat_id=chat_id,
                                        requester_id=requester_id,
                                        error=str(redact(str(exc))),
                                    )
                                    answer = (
                                        "AI tạm thời không trả lời được; "
                                        "vui lòng thử lại sau."
                                    )
            chunks = telegram_html_chunks(answer)
            for chunk in chunks:
                await self._source_fence(chat_id, PermissionName.GROUP_AI_ASK, authorization_epoch)
                async with self.database.session() as current:
                    await validate_answer(current, answer)
                await self.user.client.send_message(
                    chat_id,
                    chunk,
                    parse_mode="html",
                    reply_to=message_id,
                )
            log.info(
                "group_ai_answer_sent",
                chat_id=chat_id,
                requester_id=requester_id,
                source_mode="invoking_group_corpus",
                latency_ms=round((monotonic() - started_at) * 1000),
            )
        except Exception as exc:
            log.warning(
                "group_ai_delivery_failed",
                chat_id=chat_id,
                requester_id=requester_id,
                error=str(redact(str(exc))),
            )

    async def _dispatch_reminders(self) -> None:
        if not self.bot:
            return
        async with self.database.session() as session:
            reminders = (
                await session.scalars(
                    select(Reminder)
                    .where(
                        Reminder.status == "scheduled",
                        Reminder.remind_at <= datetime.now(UTC),
                    )
                    .order_by(Reminder.remind_at)
                    .limit(20)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for reminder in reminders:
                try:
                    await self.bot.bot.send_message(
                        int(self.user.owner_id),
                        f"⏰ Nhắc việc\n{reminder.message}",
                    )
                    reminder.status = "sent"
                    reminder.sent_at = datetime.now(UTC)
                except Exception as exc:
                    reminder.status = "failed"
                    log.warning("reminder_failed", reminder_id=reminder.id, error=str(exc))

    async def _index_knowledge_rows(
        self,
        session,
        rows: list[TelegramMessage],
        *,
        filtering_level: str = "standard",
        authorization_epochs: dict[int, int] | None = None,
    ) -> EmbeddingIndexResult:
        if not self.rag.vectors or not self.embedding_ai.available:
            return EmbeddingIndexResult()
        if not rows:
            return EmbeddingIndexResult()
        epochs = dict(authorization_epochs or {})
        for chat_id in dict.fromkeys(row.chat_id for row in rows):
            epochs[chat_id] = await self._source_fence(chat_id, PermissionName.AUTO_KNOWLEDGE, epochs.get(chat_id))

        row_ids = [row.id for row in rows]
        candidate_hashes = {
            decision.content_hash
            for row in rows
            if (
                decision := embedding_decision(
                    row.text,
                    metadata=row.metadata_json,
                    filtering_level=filtering_level,
                )
            ).content_hash
        }
        existing_hashes = set()
        if candidate_hashes:
            existing_hashes = set(
                (
                    await session.scalars(
                        select(TelegramMessage.content_hash).where(
                            TelegramMessage.content_hash.in_(candidate_hashes),
                            TelegramMessage.id.not_in(row_ids),
                            TelegramMessage.vector_status == "indexed",
                            TelegramMessage.embedding_provider == "ollama",
                            TelegramMessage.embedding_model
                            == self.settings.ollama_embedding_model,
                            TelegramMessage.embedding_version
                            == self.settings.embedding_version,
                        )
                    )
                ).all()
            )

        eligible: list[TelegramMessage] = []
        seen_hashes = set(existing_hashes)
        reused = filtered = duplicate = skipped = 0
        for row in rows:
            decision = embedding_decision(
                row.text,
                metadata=row.metadata_json,
                duplicate_hashes=seen_hashes,
                filtering_level=filtering_level,
            )
            row.normalized_text = decision.normalized_text or None
            row.content_hash = decision.content_hash
            row.embedding_error = None
            if not decision.eligible:
                row.vector_status = "skipped"
                row.embedding_skip_reason = decision.reason
                if decision.reason == "duplicate":
                    duplicate += 1
                elif decision.reason in {"empty_content", "service_message", "unsupported_content"}:
                    skipped += 1
                else:
                    filtered += 1
                continue
            if (
                row.vector_status == "indexed"
                and row.embedding_provider == "ollama"
                and row.embedding_model == self.settings.ollama_embedding_model
                and row.embedding_version == self.settings.embedding_version
            ):
                row.embedding_skip_reason = None
                reused += 1
                seen_hashes.add(decision.content_hash or "")
                continue
            row.vector_status = "pending"
            row.embedding_skip_reason = None
            eligible.append(row)
            seen_hashes.add(decision.content_hash or "")

        indexed = 0
        for batch in embedding_batches(
            eligible,
            max_input_tokens=self.settings.max_input_tokens_per_request,
        ):
            try:
                for chat_id in dict.fromkeys(row.chat_id for row in batch):
                    await self._source_fence(chat_id, PermissionName.AUTO_KNOWLEDGE, epochs[chat_id])
                vectors = await self.embedding_ai.embed_many(
                    session,
                    [
                        row.normalized_text or (row.text or "").strip()
                        for row in batch
                    ],
                    chat_id=batch[0].chat_id if batch else None,
                )
                for chat_id in dict.fromkeys(row.chat_id for row in batch):
                    await self._source_fence(chat_id, PermissionName.AUTO_KNOWLEDGE, epochs[chat_id])
                self.rag.vectors.upsert_many(
                    [
                        (row.id, vector, row.chat_id, row.message_id)
                        for row, vector in zip(batch, vectors, strict=True)
                    ]
                )
                embedded_at = datetime.now(UTC)
                for row in batch:
                    row.embedding_provider = "ollama"
                    row.embedding_model = self.settings.ollama_embedding_model
                    row.embedding_version = self.settings.embedding_version
                    row.vector_status = "indexed"
                    row.embedded_at = embedded_at
                    row.embedding_error = None
                indexed += len(vectors)
            except Exception as exc:
                error = str(redact(str(exc)))[:1000]
                for row in batch:
                    row.vector_status = "error"
                    row.embedding_error = error
                raise
        return EmbeddingIndexResult(
            processed=len(rows),
            indexed=indexed,
            reused=reused,
            filtered=filtered,
            duplicate=duplicate,
            skipped=skipped,
        )

    async def _refresh_group_knowledge(self) -> None:
        if (
            not self.rag.vectors
            or not self.embedding_ai.available
            or self.settings.ai_provider == "off"
            or self._knowledge_lock.locked()
        ):
            return
        async with self._knowledge_lock:
            try:
                async with self.database.session() as session:
                    ai_setting = await session.get(AppSetting, "ai_enabled")
                    if ai_setting and ai_setting.value is False:
                        return
                    backlog = await learning_backlog_size(session)
                    if backlog:
                        log.info(
                            "knowledge_refresh_deferred_for_learning_backlog",
                            pending_jobs=backlog,
                        )
                        return
                    learned_chats = (
                        await session.scalars(
                            select(TelegramChatPermission.chat_id)
                            .join(
                                TelegramChatPolicy,
                                TelegramChatPolicy.chat_id == TelegramChatPermission.chat_id,
                            )
                            .where(
                                TelegramChatPolicy.allowed.is_(True),
                                TelegramChatPermission.permission
                                == PermissionName.AUTO_KNOWLEDGE.value,
                                TelegramChatPermission.enabled.is_(True),
                            )
                        )
                    ).all()
                    for chat_id in learned_chats:
                        checkpoint_key = knowledge_checkpoint_key(
                            "ollama",
                            chat_id,
                        )
                        checkpoint = await session.get(AppSetting, checkpoint_key)
                        last_id = int(checkpoint.value) if checkpoint else 0
                        policy = await session.scalar(
                            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
                        )
                        source = await refresh_source_usage(session, chat_id, self.rag.vectors)
                        refresh_limit = allowed_index_count(policy, source, 100)
                        retention_since = (
                            datetime.now(UTC) - timedelta(days=policy.retention_days)
                            if policy and policy.retention_days
                            else None
                        )
                        rows = (
                            await session.scalars(
                                select(TelegramMessage)
                                .where(
                                    TelegramMessage.chat_id == chat_id,
                                    TelegramMessage.id > last_id,
                                    TelegramMessage.is_deleted.is_(False),
                                    TelegramMessage.text.is_not(None),
                                    *(
                                        [TelegramMessage.sent_at >= retention_since]
                                        if retention_since
                                        else []
                                    ),
                                )
                                .order_by(TelegramMessage.id.asc())
                                .limit(refresh_limit)
                            )
                        ).all()
                        if not rows:
                            continue
                        epoch = await self._source_fence(chat_id, PermissionName.AUTO_KNOWLEDGE)
                        index_result = await self._index_knowledge_rows(
                            session,
                            list(rows),
                            filtering_level=(policy.filtering_level if policy else "standard"),
                            authorization_epochs={chat_id: epoch},
                        )
                        await self._source_fence(chat_id, PermissionName.AUTO_KNOWLEDGE, epoch)
                        newest_id = max(row.id for row in rows)
                        if checkpoint:
                            checkpoint.value = newest_id
                        else:
                            session.add(
                                AppSetting(
                                    key=checkpoint_key,
                                    value=newest_id,
                                    description="Checkpoint lập chỉ mục kiến thức Telegram.",
                                )
                            )
                        log.info(
                            "knowledge_refreshed",
                            chat_id=chat_id,
                            indexed_count=index_result.indexed,
                            reused_count=index_result.reused,
                            filtered_count=index_result.filtered,
                        )
            except Exception as exc:
                log.warning("knowledge_refresh_failed", error=str(redact(str(exc))))

    async def _collect_operations_metric(self) -> None:
        try:
            async with self.database.session() as session:
                metric = await collect_runtime_metric(
                    session,
                    data_root=self.paths["data"],
                    vector_root=self.settings.resolved_semantic_vector_path,
                    media_root=self.paths["downloads"],
                )
                log.info(
                    "runtime_metric_collected",
                    rss_bytes=metric.rss_bytes,
                    vram_bytes=metric.vram_bytes,
                    queued_jobs=metric.queued_jobs,
                    running_jobs=metric.running_jobs,
                    paused_jobs=metric.paused_jobs,
                )
        except Exception as exc:
            log.warning("runtime_metric_failed", error=str(redact(str(exc))))

    async def _run_storage_cleanup(self) -> None:
        """Collect an hourly cleanup preview; deletion always requires confirmation."""
        if self._knowledge_lock.locked():
            return
        try:
            async with self._knowledge_lock:
                async with self.database.session() as session:
                    report = await cleanup_storage(
                        session,
                        self.rag.vectors,
                        media_root=self.paths["downloads"],
                        dry_run=True,
                    )
                    log.info(
                        "storage_cleanup_preview",
                        expired_messages=report.expired_messages,
                        expired_vectors=report.expired_vectors,
                        orphan_vectors=report.orphan_vectors,
                        media_files=report.media_files,
                        media_bytes=report.media_bytes,
                    )
        except Exception as exc:
            log.warning("storage_cleanup_failed", error=str(redact(str(exc))))

    async def _run_history_backfill_job(self, job_id: str) -> None:
        """Fetch one bounded older-history page and persist a restartable cursor."""
        try:
            async with self.database.session() as session:
                job = await session.get(BackgroundJob, job_id)
                if not job:
                    return
                payload = dict(job.payload or {})
                chat_id = int(payload["chat_id"])
                owner_id = int(payload["owner_id"])
                epoch = await self._job_fence(session, job, PermissionName.SYNC_HISTORY)
                batch_size = min(max(int(payload.get("batch_size", 500)), 1), 1000)
                chat = await session.scalar(
                    select(TelegramChat).where(TelegramChat.chat_id == chat_id)
                )
                if not chat or chat.chat_type not in {"group", "supergroup", "channel"}:
                    raise LookupError(f"Không tìm thấy nguồn Telegram {chat_id}.")
                page = await self.user.backfill_history_page(
                    session,
                    chat_id=chat_id,
                    actor_id=owner_id,
                    owner_id=int(self.user.owner_id),
                    before_message_id=payload.get("before_message_id"),
                    limit=batch_size,
                )
                processed = int(payload.get("processed", 0)) + page.synced_count
                await self._source_fence(chat_id, PermissionName.SYNC_HISTORY, epoch)
                job.payload = {
                    **payload,
                    "phase": "completed" if page.completed else "backfilling",
                    "processed": processed,
                    "last_batch": page.synced_count,
                    "before_message_id": page.next_before_message_id,
                    "progress": 100 if page.completed else None,
                    "progress_note": (
                        "Đã đến bài đăng đầu tiên." if page.completed
                        else "Đang quét ngược lịch sử; Telegram không cung cấp tổng số bài."
                    ),
                }
                job.locked_by = None
                job.locked_at = None
                job.last_error = None
                if page.completed:
                    job.status = "completed"
                    job.attempts = 0
                    session.add(
                        AuditLog(
                            occurred_at=datetime.now(UTC),
                            actor_id=owner_id,
                            action="history_backfill_completed",
                            target_type="telegram_chat",
                            target_id=str(chat_id),
                            outcome="success",
                            details_redacted={"job_id": job.id, "processed": processed},
                            correlation_id=job.id,
                        )
                    )
                else:
                    job.status = "queued"
                    # ``attempts`` tracks failures, not successful 500-post pages.
                    # Otherwise a long channel would always stop after max_attempts pages.
                    job.attempts = 0
                    job.run_after = datetime.now(UTC) + timedelta(seconds=2)
        except AuthorizationRevoked:
            await self._cancel_revoked_job(job_id)
            return
        except Exception as exc:
            error = str(redact(str(exc)))[:1000]
            async with self.database.session() as session:
                job = await session.get(BackgroundJob, job_id)
                if not job:
                    return
                job.locked_by = None
                job.locked_at = None
                job.last_error = error
                if job.attempts >= job.max_attempts:
                    job.status = "failed"
                    phase = "failed"
                else:
                    job.status = "queued"
                    job.run_after = datetime.now(UTC) + timedelta(minutes=job.attempts)
                    phase = "retrying"
                job.payload = {**(job.payload or {}), "phase": phase, "progress": None}
                session.add(
                    AuditLog(
                        occurred_at=datetime.now(UTC),
                        actor_id=int(self.user.owner_id),
                        action="history_backfill",
                        target_type="telegram_chat",
                        target_id=str((job.payload or {}).get("chat_id", "")),
                        outcome="failed",
                        reason=error,
                        details_redacted={"job_id": job.id, "attempt": job.attempts},
                        correlation_id=job.id,
                    )
                )
            log.warning("history_backfill_job_failed", job_id=job_id, error=error)

    async def _run_history_link_delete_job(self, job_id: str) -> None:
        """Delete a confirmed, bounded historical-link selection in safe Telegram batches."""
        try:
            async with self.database.session() as session:
                job = await session.get(BackgroundJob, job_id)
                if not job:
                    return
                payload = dict(job.payload or {})
                chat_id = int(payload["chat_id"])
                owner_id = int(payload["owner_id"])
                mode = str(payload["mode"])
                epoch = await self._job_fence(session, job, PermissionName.DELETE_ANY_MESSAGES)
                keyword_terms = list(payload.get("keyword_terms", []))
                sender_ids = {int(value) for value in payload.get("sender_ids", [])}
                selection_type = str(payload.get("selection_type", "all_matching"))
                selected_ids = {int(value) for value in payload.get("selected_message_ids", [])}
                excluded_ids = {int(value) for value in payload.get("excluded_message_ids", [])}
                max_message_id = int(payload["max_message_id"])
                cursor = int(payload.get("scan_cursor", 0))
                rows = list(
                    (
                        await session.scalars(
                            select(TelegramMessage)
                            .where(
                                TelegramMessage.chat_id == chat_id,
                                TelegramMessage.is_deleted.is_(False),
                                TelegramMessage.message_id > cursor,
                                TelegramMessage.message_id <= max_message_id,
                            )
                            .order_by(TelegramMessage.message_id.asc())
                            .limit(500)
                        )
                    ).all()
                )
                candidates = [
                    row
                    for row in rows
                    if (
                        row.sender_id in sender_ids
                        if mode == "sender"
                        else matches_history_delete_message(
                            row, mode, keyword_terms=keyword_terms
                        )
                    )
                    and (
                        row.message_id in selected_ids
                        if selection_type == "specific"
                        else row.message_id not in excluded_ids
                    )
                ]
                if candidates:
                    actual_rights = await self.user.get_actual_rights(chat_id)
                    rights = frozenset(key for key, enabled in actual_rights.items() if enabled)
                    for start in range(0, len(candidates), 100):
                        batch = candidates[start : start + 100]
                        await self._source_fence(chat_id, PermissionName.DELETE_ANY_MESSAGES, epoch)
                        job.payload = {**payload, "external_effect_started": True}
                        await session.commit()
                        await self._job_fence(session, job, PermissionName.DELETE_ANY_MESSAGES)
                        await self.user.delete_messages_bulk(
                            session,
                            chat_id=chat_id,
                            message_ids=[int(row.message_id) for row in batch],
                            actor_id=owner_id,
                            owner_id=int(self.user.owner_id),
                            telegram_rights=rights,
                        )
                        for row in batch:
                            row.is_deleted = True
                        await self._source_fence(chat_id, PermissionName.DELETE_ANY_MESSAGES, epoch)
                scanned = int(payload.get("scanned", 0)) + len(rows)
                deleted = int(payload.get("deleted", 0)) + len(candidates)
                completed = len(rows) < 500
                candidate_count = max(int(payload.get("candidate_count", 0)), 1)
                job.payload = {
                    **payload,
                    "phase": "completed" if completed else "deleting",
                    "scan_cursor": rows[-1].message_id if rows else cursor,
                    "scanned": scanned,
                    "deleted": deleted,
                    "last_batch": len(candidates),
                    "progress": 100 if completed else min(99, round(deleted * 100 / candidate_count)),
                    "progress_note": (
                        "Đã xử lý hết phạm vi preview." if completed
                        else "Đang xóa theo lô; post mới sau preview không nằm trong phạm vi."
                    ),
                }
                job.locked_by = None
                job.locked_at = None
                job.last_error = None
                if completed:
                    job.status = "completed"
                    job.attempts = 0
                    session.add(
                        AuditLog(
                            occurred_at=datetime.now(UTC),
                            actor_id=owner_id,
                            action="history_link_delete_completed",
                            target_type="telegram_chat",
                            target_id=str(chat_id),
                            outcome="success",
                            details_redacted={
                                "job_id": job.id,
                                "mode": mode,
                                "deleted": deleted,
                            },
                            correlation_id=job.id,
                        )
                    )
                else:
                    job.status = "queued"
                    job.attempts = 0
                    job.run_after = datetime.now(UTC) + timedelta(seconds=2)
        except AuthorizationRevoked:
            await self._cancel_revoked_job(job_id)
            return
        except Exception as exc:
            error = str(redact(str(exc)))[:1000]
            async with self.database.session() as session:
                job = await session.get(BackgroundJob, job_id)
                if not job:
                    return
                job.locked_by = None
                job.locked_at = None
                job.last_error = error
                if (job.payload or {}).get("external_effect_started"):
                    job.status = "uncertain"
                    return
                if job.attempts >= job.max_attempts:
                    job.status = "failed"
                    phase = "failed"
                else:
                    job.status = "queued"
                    job.run_after = datetime.now(UTC) + timedelta(minutes=job.attempts)
                    phase = "retrying"
                job.payload = {**(job.payload or {}), "phase": phase}
                session.add(
                    AuditLog(
                        occurred_at=datetime.now(UTC),
                        actor_id=int(self.user.owner_id),
                        action="history_link_delete",
                        target_type="telegram_chat",
                        target_id=str((job.payload or {}).get("chat_id", "")),
                        outcome="failed",
                        reason=error,
                        details_redacted={"job_id": job.id, "attempt": job.attempts},
                        correlation_id=job.id,
                    )
                )
            log.warning("history_link_delete_job_failed", job_id=job_id, error=error)

    async def _classify_history_delete_with_openai(
        self,
        session: AsyncSession,
        chat_id: int,
        instruction: str,
        posts: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Use direct OpenAI only to suggest, never execute, historical deletions."""
        engine = self.ai_router.engines.get("openai")
        if not engine or not engine.available:
            raise RuntimeError(
                "Chưa có OpenAI trực tiếp. Hãy cấu hình OPENAI_API_KEY trong terminal rồi thử lại."
            )
        return await engine.classify_history_delete_candidates(
            session,
            instruction=instruction,
            posts=posts,
            chat_id=chat_id,
        )

    async def _resolve_history_sender_identity(self, reference: str) -> dict[str, object]:
        """Resolve a Dashboard @handle via the signed-in Telegram account."""
        return await self.user.resolve_sender_identity(reference)

    async def _process_admin_jobs(self) -> None:
        """Run bounded dashboard jobs that must not block an HTTP request."""
        job_id: str | None = None
        async with self.database.session() as session:
            stale_backfills = list(
                (
                    await session.scalars(
                        select(BackgroundJob).where(
                            BackgroundJob.job_type.in_(
                                ("history_backfill", "history_link_delete")
                            ),
                            BackgroundJob.status == "running",
                            BackgroundJob.locked_at < datetime.now(UTC) - timedelta(minutes=15),
                        )
                    )
                ).all()
            )
            for stale_job in stale_backfills:
                if (
                    stale_job.job_type == "history_link_delete"
                    and (stale_job.payload or {}).get("external_effect_started")
                ):
                    stale_job.status = "uncertain"
                    stale_job.locked_by = None
                    stale_job.locked_at = None
                    stale_job.run_after = None
                    stale_job.last_error = (
                        "Thao tác xóa bị gián đoạn; cần đối soát trước khi chạy lại."
                    )
                    stale_job.payload = {
                        **(stale_job.payload or {}),
                        "phase": "uncertain",
                        "requires_reconciliation": True,
                    }
                    continue
                stale_job.status = "queued"
                stale_job.locked_by = None
                stale_job.locked_at = None
                stale_job.run_after = datetime.now(UTC)
            job = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.job_type.in_(
                        ("ollama_pull", "history_backfill", "history_link_delete")
                    ),
                    BackgroundJob.status == "queued",
                    (BackgroundJob.run_after.is_(None))
                    | (BackgroundJob.run_after <= datetime.now(UTC)),
                    BackgroundJob.attempts < BackgroundJob.max_attempts,
                )
                .order_by(BackgroundJob.created_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not job:
                return
            job.status = "running"
            job.attempts += 1
            job.locked_by = "telegram-assistant-runtime"
            job.locked_at = datetime.now(UTC)
            job_id = job.id
            job_type = job.job_type
        if job_type == "history_backfill":
            await self._run_history_backfill_job(job_id)
            return
        if job_type == "history_link_delete":
            await self._run_history_link_delete_job(job_id)
            return
        try:
            async with self.database.session() as session:
                job = await session.get(BackgroundJob, job_id)
                if not job:
                    return
                model = str((job.payload or {}).get("model", "")).strip()
            async def pull_cancelled() -> bool:
                async with self.database.session() as progress_session:
                    current = await progress_session.get(BackgroundJob, job_id)
                    return bool(current and current.status == "cancel_requested")

            async def update_pull_progress(update: dict) -> None:
                async with self.database.session() as progress_session:
                    current = await progress_session.get(BackgroundJob, job_id)
                    if not current or current.status == "cancel_requested":
                        return
                    current.payload = {
                        **(current.payload or {}),
                        **update,
                        "phase": "downloading",
                    }

            async with self.database.session() as session:
                current = await session.get(BackgroundJob, job_id)
                if current:
                    current.payload = {
                        **(current.payload or {}),
                        "phase": "downloading",
                        "progress": 0,
                    }
            await self.ollama.pull_model(
                model,
                on_progress=update_pull_progress,
                should_cancel=pull_cancelled,
            )
            async with self.database.session() as session:
                job = await session.get(BackgroundJob, job_id)
                if job:
                    job.status = "completed"
                    job.payload = {
                        **(job.payload or {}),
                        "phase": "completed",
                        "progress": 100,
                    }
                    job.locked_by = None
                    job.locked_at = None
                    job.last_error = None
                    session.add(
                        AuditLog(
                            occurred_at=datetime.now(UTC),
                            actor_id=int(self.user.owner_id),
                            action="ollama_model_pulled",
                            target_type="ollama_model",
                            target_id=model,
                            outcome="success",
                            details_redacted={"job_id": job.id},
                            correlation_id=job.id,
                        )
                    )
        except OllamaPullCancelled:
            async with self.database.session() as session:
                job = await session.get(BackgroundJob, job_id)
                if job:
                    job.status = "cancelled"
                    job.payload = {
                        **(job.payload or {}),
                        "phase": "cancelled",
                    }
                    job.locked_by = None
                    job.locked_at = None
                    job.last_error = None
            log.info("ollama_pull_job_cancelled", job_id=job_id)
        except Exception as exc:
            error = str(redact(str(exc)))[:1000]
            async with self.database.session() as session:
                job = await session.get(BackgroundJob, job_id)
                if job:
                    job.last_error = error
                    job.locked_by = None
                    job.locked_at = None
                    if job.attempts >= job.max_attempts:
                        job.status = "failed"
                    else:
                        job.status = "queued"
                        job.run_after = datetime.now(UTC) + timedelta(minutes=job.attempts)
                    session.add(
                        AuditLog(
                            occurred_at=datetime.now(UTC),
                            actor_id=int(self.user.owner_id),
                            action="ollama_model_pull",
                            target_type="ollama_model",
                            target_id=str((job.payload or {}).get("model", "")),
                            outcome="failed",
                            reason=error,
                            details_redacted={"job_id": job.id},
                            correlation_id=job.id,
                        )
                    )
            log.warning("ollama_pull_job_failed", job_id=job_id, error=error)

    async def _process_learning_jobs(self) -> None:
        cycle_started = monotonic()
        if (
            self._knowledge_lock.locked()
            or not self.embedding_ai.available
            or self.settings.ai_provider == "off"
            or not self.rag.vectors
        ):
            return
        async with self.database.session() as session:
            ai_setting = await session.get(AppSetting, "ai_enabled")
            if ai_setting and ai_setting.value is False:
                return
        job_id: str | None = None
        async with self.database.session() as session:
            stale_jobs = (
                await session.scalars(
                    select(BackgroundJob).where(
                        BackgroundJob.job_type == "learn_group",
                        BackgroundJob.status == "running",
                        BackgroundJob.locked_at < datetime.now(UTC) - timedelta(minutes=15),
                    )
                )
            ).all()
            for stale in stale_jobs:
                stale.status = "queued"
                stale.locked_by = None
                stale.locked_at = None
                stale.run_after = datetime.now(UTC)
                stale_payload = stale.payload or {}
                if stale_payload.get("chat_id") is not None:
                    stale_source = await session.get(KnowledgeSource, int(stale_payload["chat_id"]))
                    if stale_source:
                        stale_source.status = "queued"
            job = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.job_type == "learn_group",
                    BackgroundJob.status == "queued",
                    (BackgroundJob.run_after.is_(None))
                    | (BackgroundJob.run_after <= datetime.now(UTC)),
                    BackgroundJob.attempts < BackgroundJob.max_attempts,
                )
                .order_by(BackgroundJob.created_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not job:
                return
            job.status = "running"
            job.attempts += 1
            job.locked_by = "telegram-assistant-runtime"
            job.locked_at = datetime.now(UTC)
            job_id = job.id
            payload = dict(job.payload or {})
            payload.update(
                {
                    "phase": "syncing",
                    "progress": 10,
                    "processed": 0,
                    "total": int(payload.get("limit", 1000)),
                    "synced_messages": 0,
                }
            )
            job.payload = payload
            if payload.get("chat_id") is not None:
                chat_id = int(payload["chat_id"])
                source = await session.get(KnowledgeSource, chat_id)
                if not source:
                    source = KnowledgeSource(chat_id=chat_id)
                    session.add(source)
                source.status = "running"
                source.requested_for_learning = True
                source.last_job_id = job.id
                source.last_error = None
        try:
            async with self._knowledge_lock:
                # Phase 1 is committed first so MySQL remains the source of truth
                # even when the external embedding request fails.
                async with self.database.session() as session:
                    job = await session.get(BackgroundJob, job_id)
                    if not job:
                        return
                    if await honor_learning_pause(session, job):
                        log.info("learning_job_paused", job_id=job.id, phase="before_sync")
                        return
                    if job.status != "running":
                        return
                    payload = dict(job.payload or {})
                    chat_id = int(payload["chat_id"])
                    owner_id = int(payload["owner_id"])
                    limit = min(max(int(payload.get("limit", 1000)), 1), 1000)
                    chat = await session.scalar(
                        select(TelegramChat).where(
                            TelegramChat.chat_id == chat_id,
                            TelegramChat.chat_type.in_(
                                (
                                    "group",
                                    "supergroup",
                                    "channel",
                                )
                            ),
                        )
                    )
                    if not chat:
                        raise LookupError(f"Không tìm thấy group/channel {chat_id}.")
                    epoch = await self._job_fence(session, job, PermissionName.SYNC_HISTORY)
                    policy = await session.scalar(
                        select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
                    )
                    current_message_count = int(
                        (
                            await session.scalar(
                                select(func.count(TelegramMessage.id)).where(
                                    TelegramMessage.chat_id == chat_id,
                                    TelegramMessage.is_deleted.is_(False),
                                )
                            )
                        )
                        or 0
                    )
                    if policy and policy.max_messages:
                        limit = min(
                            limit,
                            max(0, policy.max_messages - current_message_count),
                        )
                    sync_started = monotonic()
                    synced = (
                        await self.user.sync_history(
                            session,
                            chat_id=chat_id,
                            actor_id=owner_id,
                            owner_id=int(self.user.owner_id),
                            limit=limit,
                        )
                        if limit > 0
                        else 0
                    )
                    sync_duration_ms = (monotonic() - sync_started) * 1000
                    await self._source_fence(chat_id, PermissionName.SYNC_HISTORY, epoch)
                    await session.flush()
                    mysql_message_count = int(
                        (
                            await session.scalar(
                                select(func.count(TelegramMessage.id)).where(
                                    TelegramMessage.chat_id == chat_id,
                                    TelegramMessage.is_deleted.is_(False),
                                )
                            )
                        )
                        or 0
                    )
                    mysql_text_count = int(
                        (
                            await session.scalar(
                                select(func.count(TelegramMessage.id)).where(
                                    TelegramMessage.chat_id == chat_id,
                                    TelegramMessage.is_deleted.is_(False),
                                    TelegramMessage.text.is_not(None),
                                    func.length(func.trim(TelegramMessage.text)) > 0,
                                )
                            )
                        )
                        or 0
                    )
                    job.payload = {
                        **payload,
                        "phase": "embedding",
                        "progress": 55,
                        "processed": 0,
                        "total": mysql_text_count,
                        "synced_messages": synced,
                        "total_messages": limit,
                        "vectors_created": 0,
                        "title": chat.title,
                        "synced_count": synced,
                        "mysql_message_count": mysql_message_count,
                        "mysql_before": current_message_count,
                        "mysql_after": mysql_message_count,
                        "mysql_text_count": mysql_text_count,
                        "mysql_synced": True,
                        "sync_duration_ms": round(sync_duration_ms, 2),
                    }
                    source = await session.get(KnowledgeSource, chat_id)
                    if source:
                        source.mysql_message_count = mysql_message_count
                        source.text_message_count = mysql_text_count
                    await refresh_source_usage(session, chat_id, self.rag.vectors)

                # Phase 2 only reads committed MySQL rows and creates derived vectors.
                async with self.database.session() as session:
                    job = await session.get(BackgroundJob, job_id)
                    if not job:
                        return
                    if await honor_learning_pause(session, job):
                        log.info("learning_job_paused", job_id=job.id, phase="before_embedding")
                        return
                    if job.status != "running":
                        return
                    payload = dict(job.payload or {})
                    chat_id = int(payload["chat_id"])
                    epoch = await self._job_fence(session, job, PermissionName.AUTO_KNOWLEDGE)
                    limit = min(max(int(payload.get("limit", 1000)), 1), 1000)
                    checkpoint_key = knowledge_checkpoint_key(
                        "ollama",
                        chat_id,
                    )
                    checkpoint = await session.get(AppSetting, checkpoint_key)
                    checkpoint_id = int(checkpoint.value) if checkpoint else 0
                    policy = await session.scalar(
                        select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
                    )
                    source = await refresh_source_usage(session, chat_id, self.rag.vectors)
                    vectors_before = source.vector_count
                    limit = allowed_index_count(policy, source, limit)
                    retention_since = (
                        datetime.now(UTC) - timedelta(days=policy.retention_days)
                        if policy and policy.retention_days
                        else None
                    )
                    rows = list(
                        (
                            await session.scalars(
                                knowledge_rows_query(
                                    chat_id,
                                    limit=limit,
                                    checkpoint_id=checkpoint_id,
                                    since=retention_since,
                                )
                            )
                        ).all()
                    )
                    embedding_started = monotonic()
                    index_result = await self._index_knowledge_rows(
                        session,
                        rows,
                        filtering_level=(policy.filtering_level if policy else "standard"),
                        authorization_epochs={chat_id: epoch},
                    )
                    indexed = index_result.indexed
                    embedding_duration_ms = (monotonic() - embedding_started) * 1000
                    await session.refresh(job)
                    await self._job_fence(session, job, PermissionName.AUTO_KNOWLEDGE)
                    if await honor_learning_pause(session, job):
                        log.info("learning_job_paused", job_id=job.id, phase="after_embedding")
                        return
                    newest_id = max((row.id for row in rows), default=checkpoint_id)
                    if rows:
                        if checkpoint:
                            checkpoint.value = newest_id
                        else:
                            session.add(
                                AppSetting(
                                    key=checkpoint_key,
                                    value=newest_id,
                                    description="Checkpoint lập chỉ mục kiến thức Telegram.",
                                )
                            )
                    source = await refresh_source_usage(session, chat_id, self.rag.vectors)
                    vectors_after = source.vector_count
                    evaluated = index_result.processed
                    accounted = (
                        index_result.indexed
                        + index_result.reused
                        + index_result.filtered
                        + index_result.duplicate
                        + index_result.skipped
                        + index_result.failed
                    )
                    invariant_ok = evaluated == accounted
                    coverage_warning = vectors_after < vectors_before
                    final_status = (
                        "completed_with_warning"
                        if coverage_warning or not invariant_ok
                        else "completed"
                    )
                    job.payload = {
                        **payload,
                        "phase": "reconciliation_required" if coverage_warning else "completed",
                        "progress": 100,
                        "processed": evaluated,
                        "total": evaluated,
                        "candidates_total": len(rows),
                        "evaluated": evaluated,
                        "indexed_new": index_result.indexed,
                        "reused_existing": index_result.reused,
                        "filtered": index_result.filtered,
                        "duplicate": index_result.duplicate,
                        "skipped": index_result.skipped,
                        "failed": index_result.failed,
                        "invariant_ok": invariant_ok,
                        "synced_messages": int(payload.get("synced_count") or 0),
                        "vectors_created": indexed,
                        "vectors_reused": index_result.reused,
                        "messages_filtered": index_result.filtered,
                        "messages_duplicate": index_result.duplicate,
                        "messages_embedded": indexed,
                        "local_tokens": sum(
                            max(1, len(row.normalized_text or row.text or "") // 4)
                            for row in rows
                            if row.vector_status == "indexed"
                        ),
                        "cloud_tokens": 0,
                        "total_vectors": len(rows),
                        "indexed_count": indexed,
                        "checkpoint_before": checkpoint_id,
                        "checkpoint_after": newest_id,
                        "vectors_before": vectors_before,
                        "vectors_after": vectors_after,
                        "active_vector_store_id": self.settings.resolved_semantic_vector_path.name,
                        "embedding_model": self.settings.ollama_embedding_model,
                        "embedding_version": self.settings.embedding_version,
                        "corpus": LocalVectorStore.COLLECTION,
                        "embedding_duration_ms": round(embedding_duration_ms, 2),
                        "total_duration_ms": round((monotonic() - cycle_started) * 1000, 2),
                    }
                    job.status = final_status
                    job.locked_by = None
                    job.locked_at = None
                    job.last_error = None
                    source = await session.get(KnowledgeSource, chat_id)
                    if source:
                        had_corpus = checkpoint_id > 0 or indexed > 0
                        source.status = (
                            "reconciliation_required"
                            if coverage_warning
                            else ("learned" if had_corpus else "no_content")
                        )
                        source.requested_for_learning = False
                        source.last_indexed_count = indexed
                        source.last_job_id = job.id
                        source.last_error = None
                        if had_corpus:
                            source.last_learned_at = datetime.now(UTC)
                        # The derived index is append-only in incremental learning.
                        # A count drop is preserved as a warning; it is never hidden by a green status.
                        await refresh_source_usage(session, chat_id, self.rag.vectors)
                    log.info(
                        "learning_job_completed",
                        job_id=job.id,
                        chat_id=chat_id,
                        synced_count=payload.get("synced_count", 0),
                        mysql_message_count=payload.get("mysql_message_count", 0),
                        indexed_count=indexed,
                        sync_duration_ms=payload.get("sync_duration_ms"),
                        embedding_duration_ms=embedding_duration_ms,
                        total_duration_ms=(monotonic() - cycle_started) * 1000,
                    )
        except AuthorizationRevoked:
            await self._cancel_revoked_job(job_id)
            return
        except asyncio.CancelledError:
            async with self.database.session() as session:
                job = await session.get(BackgroundJob, job_id)
                if job and job.status == "running":
                    requeue_interrupted_learning_job(job)
                    payload = job.payload or {}
                    if payload.get("chat_id") is not None:
                        source = await session.get(KnowledgeSource, int(payload["chat_id"]))
                        if source:
                            source.status = "queued"
            log.info("learning_job_interrupted_requeued", job_id=job_id)
            raise
        except Exception as exc:
            async with self.database.session() as session:
                job = await session.get(BackgroundJob, job_id)
                if job:
                    error = str(redact(str(exc)))[:1000]
                    job.last_error = error
                    job.locked_by = None
                    job.locked_at = None
                    if job.attempts >= job.max_attempts:
                        job.status = "failed"
                    else:
                        job.status = "queued"
                        job.run_after = datetime.now(UTC) + timedelta(minutes=min(job.attempts, 5))
                    payload = job.payload or {}
                    if payload.get("chat_id") is not None:
                        source = await session.get(KnowledgeSource, int(payload["chat_id"]))
                        if source:
                            source.status = job.status
                            source.last_job_id = job.id
                            source.last_error = error
            log.warning(
                "learning_job_failed",
                job_id=job_id,
                error=str(redact(str(exc))),
            )

    @staticmethod
    def _audit_row(action: PendingAction, outcome: str, reason: str | None = None) -> AuditLog:
        if action.action_type in {
            "set_chat_allowed",
            "set_chat_permission",
            "apply_permission_template",
            "setup_moderation",
            "set_admin_only_auto_moderation",
            "set_link_spam_auto_moderation",
            "set_group_ai_ask",
            "enable_group_learning",
            "leave_telegram_chat",
            "delete_learned_data",
            "delete_history_link_posts",
            "recover_source_index",
        }:
            target_type, target_id = "chat_policy", str(action.chat_id)
        elif action.action_type in {"create_memory", "forget_memory"}:
            target_type = "memory"
            target_id = str(action.payload.get("memory_id", "new"))
        elif action.action_type in {"create_task", "delete_task"}:
            target_type = "task"
            target_id = str(action.payload.get("task_id", "new"))
        elif action.action_type == "enable_group_learning_bulk":
            target_type = "knowledge_batch"
            target_id = action.action_id
        elif action.action_type == "storage_cleanup":
            target_type = "storage"
            target_id = "local_runtime"
        elif action.action_type == "delete_ollama_model":
            target_type = "ollama_model"
            target_id = str(action.payload.get("model", ""))
        else:
            target_type, target_id = (
                "telegram_message",
                f"{action.chat_id}/{action.message_id}",
            )
        return AuditLog(
            occurred_at=datetime.now(UTC),
            actor_id=action.requested_by,
            action=action.action_type,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            reason=str(redact(reason)) if reason is not None else None,
            details_redacted=redact(action.payload),
            correlation_id=action.action_id,
        )

    async def _execute_actions(self) -> None:
        while not self.stopping.is_set():
            async with self.database.session() as session:
                actions = list(
                    (
                        await session.scalars(
                            select(PendingAction)
                            .where(PendingAction.status == "confirmed")
                            .order_by(PendingAction.confirmed_at)
                            .limit(10)
                            .with_for_update(skip_locked=True)
                        )
                    ).all()
                )
                for action in actions:
                    try:
                        if action.requested_by != self.user.owner_id:
                            raise PermissionError("Pending action không thuộc owner hiện tại.")
                        await self._action_fence(action)
                        claimed = await session.execute(update(PendingAction).where(PendingAction.action_id == action.action_id, PendingAction.status == "confirmed").values(status="executing"))
                        if claimed.rowcount != 1:
                            continue
                        action.status = "executing"
                        await session.commit()
                        chat = await session.scalar(
                            select(TelegramChat).where(TelegramChat.chat_id == action.chat_id)
                        )
                        if action.action_type == "recover_source_index":
                            # Confirmation records owner review; it never starts reindexing implicitly.
                            action.status = "approved_not_executed"
                            action.error = (
                                "Recovery remains disabled until the owner starts an explicit "
                                "source-recovery workflow."
                            )
                            session.add(self._audit_row(action, "warning", action.error))
                            continue
                        if action.action_type in {
                            "send_message",
                            "edit_message",
                            "delete_message",
                            "delete_history_link_posts",
                            "pin_message",
                            "set_group_ai_ask",
                            "leave_telegram_chat",
                        }:
                            current_rights = await self.user.get_actual_rights(int(action.chat_id))
                            if chat:
                                chat.account_rights = current_rights
                            rights = frozenset(
                                key for key, enabled in current_rights.items() if enabled
                            )
                        else:
                            rights = frozenset()
                        # Rights lookups yield to Telegram; a revoke can win meanwhile.
                        await self._action_fence(action)
                        if action.action_type in {"send_message", "edit_message", "delete_message", "pin_message", "leave_telegram_chat"}:
                            action.payload = {**action.payload, "external_effect_started": True}
                            await session.commit()
                            await self._action_fence(action)
                        elif action.action_type in {"set_chat_allowed", "set_chat_permission", "apply_permission_template", "setup_moderation", "set_admin_only_auto_moderation", "set_link_spam_auto_moderation", "set_group_ai_ask", "enable_group_learning", "enable_group_learning_bulk"}:
                            await validate_action_epoch(session, action)
                        if action.action_type == "delete_message":
                            await self.user.delete_message(
                                session,
                                chat_id=int(action.chat_id),
                                message_id=int(action.message_id),
                                actor_id=action.requested_by,
                                owner_id=int(self.user.owner_id),
                                telegram_rights=rights,
                                confirmed=True,
                                delete_any=bool(action.payload.get("delete_any")),
                            )
                        elif action.action_type == "send_message":
                            result_id = await self.user.send_message(
                                session,
                                chat_id=int(action.chat_id),
                                text=str(action.payload["text"]),
                                actor_id=action.requested_by,
                                owner_id=int(self.user.owner_id),
                                telegram_rights=rights,
                            )
                            action.message_id = result_id
                        elif action.action_type == "edit_message":
                            await self.user.edit_message(
                                session,
                                chat_id=int(action.chat_id),
                                message_id=int(action.message_id),
                                text=str(action.payload["text"]),
                                actor_id=action.requested_by,
                                owner_id=int(self.user.owner_id),
                                telegram_rights=rights,
                            )
                        elif action.action_type == "pin_message":
                            await self.user.pin_message(
                                session,
                                chat_id=int(action.chat_id),
                                message_id=int(action.message_id),
                                actor_id=action.requested_by,
                                owner_id=int(self.user.owner_id),
                                telegram_rights=rights,
                            )
                        elif action.action_type == "set_chat_allowed":
                            allowed = bool(action.payload["allowed"])
                            if allowed:
                                await self.policy.set_allowed(session, int(action.chat_id), True, expected_epoch=action.payload["authorization_epochs"][str(action.chat_id)])
                            else:
                                await RevocationService(self.database, int(self.user.owner_id)).revoke_in_session(session, int(action.chat_id), action.requested_by, str(action.payload["memory_action"]))
                            if not allowed:
                                memory_action = str(action.payload["memory_action"])
                                if memory_action in {"archive", "delete"}:
                                    memories = (
                                        await session.scalars(
                                            select(AiMemory).where(
                                                AiMemory.source_chat_id == action.chat_id,
                                                AiMemory.status == "active",
                                            )
                                        )
                                    ).all()
                                    for memory in memories:
                                        if memory_action == "archive":
                                            memory.status = "archived"
                                        else:
                                            memory.content = "[deleted]"
                                            memory.status = "deleted"
                                other_actions = (
                                    await session.scalars(
                                        select(PendingAction).where(
                                            PendingAction.chat_id == action.chat_id,
                                            PendingAction.action_id != action.action_id,
                                            PendingAction.status.in_(["pending", "confirmed"]),
                                        )
                                    )
                                ).all()
                                for pending in other_actions:
                                    pending.status = "cancelled"
                                    pending.error = "Chat đã bị block."
                        elif action.action_type == "set_chat_permission":
                            await self.policy.set_permission(
                                session,
                                int(action.chat_id),
                                PermissionName(str(action.payload["permission"])),
                                bool(action.payload["enabled"]),
                            )
                        elif action.action_type == "apply_permission_template":
                            await self.policy.apply_template(
                                session,
                                int(action.chat_id),
                                str(action.payload["template"]),
                            )
                        elif action.action_type == "setup_moderation":
                            await self.policy.apply_moderation_bundle(
                                session,
                                int(action.chat_id),
                            )
                        elif action.action_type in {
                            "set_admin_only_auto_moderation",
                            "set_link_spam_auto_moderation",
                        }:
                            await self.policy.set_link_spam_auto_moderation(
                                session,
                                int(action.chat_id),
                                enabled=bool(action.payload["enabled"]),
                            )
                        elif action.action_type == "set_group_ai_ask":
                            enabled = bool(action.payload["enabled"])
                            if enabled and "send_messages" not in rights:
                                raise PermissionError(
                                    "Tài khoản Telegram không có quyền gửi tin trong group."
                                )
                            await self.policy.set_group_ai_ask(
                                session,
                                int(action.chat_id),
                                enabled=enabled,
                            )
                        elif action.action_type == "sync_chat_history":
                            synced = await self.user.sync_history(
                                session,
                                chat_id=int(action.chat_id),
                                actor_id=action.requested_by,
                                owner_id=int(self.user.owner_id),
                                limit=min(max(int(action.payload.get("limit", 1000)), 1), 1000),
                            )
                            action.payload = {**action.payload, "synced_count": synced}
                        elif action.action_type == "backfill_chat_history":
                            chat_id = int(action.chat_id)
                            active_backfills = list(
                                (
                                    await session.scalars(
                                        select(BackgroundJob).where(
                                            BackgroundJob.job_type == "history_backfill",
                                            BackgroundJob.status.in_(("queued", "running")),
                                        )
                                    )
                                ).all()
                            )
                            existing_job = next(
                                (
                                    item
                                    for item in active_backfills
                                    if str((item.payload or {}).get("chat_id")) == str(chat_id)
                                ),
                                None,
                            )
                            if existing_job:
                                job = existing_job
                            else:
                                job = BackgroundJob(
                                    job_type="history_backfill",
                                    payload={
                                        "action_id": action.action_id,
                                        "chat_id": chat_id,
                                        "owner_id": action.requested_by,
                                        "authorization_epoch": action.payload["authorization_epochs"][str(chat_id)],
                                        "batch_size": min(
                                            max(
                                                int(action.payload.get("batch_size", 500)),
                                                1,
                                            ),
                                            1000,
                                        ),
                                        "before_message_id": 0,
                                        "processed": 0,
                                        "phase": "queued",
                                        "progress": None,
                                        "progress_note": "Chờ worker quét lịch sử cũ.",
                                    },
                                    status="queued",
                                    max_attempts=5,
                                )
                                session.add(job)
                                await session.flush()
                            action.payload = {
                                **action.payload,
                                "job_id": job.id,
                                "job_status": job.status,
                            }
                        elif action.action_type == "delete_history_link_posts":
                            chat_id = int(action.chat_id)
                            active_jobs = list(
                                (
                                    await session.scalars(
                                        select(BackgroundJob).where(
                                            BackgroundJob.job_type == "history_link_delete",
                                            BackgroundJob.status.in_(("queued", "running")),
                                        )
                                    )
                                ).all()
                            )
                            existing_job = next(
                                (
                                    item
                                    for item in active_jobs
                                    if str((item.payload or {}).get("chat_id")) == str(chat_id)
                                ),
                                None,
                            )
                            if existing_job:
                                job = existing_job
                            else:
                                job = BackgroundJob(
                                    job_type="history_link_delete",
                                    payload={
                                        "action_id": action.action_id,
                                        "chat_id": chat_id,
                                        "owner_id": action.requested_by,
                                        "authorization_epoch": action.payload["authorization_epochs"][str(chat_id)],
                                        "mode": str(action.payload["mode"]),
                                        "keyword_terms": list(
                                            action.payload.get("keyword_terms", [])
                                        ),
                                        "sender_ids": list(action.payload.get("sender_ids", [])),
                                        "selection_type": str(
                                            action.payload.get("selection_type", "all_matching")
                                        ),
                                        "selected_message_ids": list(
                                            action.payload.get("selected_message_ids", [])
                                        ),
                                        "excluded_message_ids": list(
                                            action.payload.get("excluded_message_ids", [])
                                        ),
                                        "max_message_id": int(action.payload["max_message_id"]),
                                        "candidate_count": int(
                                            action.payload["candidate_count"]
                                        ),
                                        "scan_cursor": 0,
                                        "scanned": 0,
                                        "deleted": 0,
                                        "phase": "queued",
                                        "progress": 0,
                                        "progress_note": "Chờ worker xóa các post trong phạm vi preview.",
                                    },
                                    status="queued",
                                    max_attempts=5,
                                )
                                session.add(job)
                                await session.flush()
                            action.payload = {
                                **action.payload,
                                "job_id": job.id,
                                "job_status": job.status,
                            }
                        elif action.action_type == "enable_group_learning":
                            chat_id = int(action.chat_id)
                            await self.policy.apply_template(session, chat_id, "knowledge")
                            authorization_epoch = await source_epoch(session, chat_id)
                            active_jobs = list(
                                (
                                    await session.scalars(
                                        select(BackgroundJob).where(
                                            BackgroundJob.job_type == "learn_group",
                                            BackgroundJob.status.in_(
                                                (
                                                    "queued",
                                                    "running",
                                                    "paused",
                                                    "pause_requested",
                                                )
                                            ),
                                        )
                                    )
                                ).all()
                            )
                            active_job = next(
                                (
                                    item
                                    for item in active_jobs
                                    if str((item.payload or {}).get("chat_id")) == str(chat_id)
                                ),
                                None,
                            )
                            if active_job:
                                job = active_job
                            else:
                                job = BackgroundJob(
                                    job_type="learn_group",
                                    payload={
                                        "action_id": action.action_id,
                                        "chat_id": chat_id,
                                        "owner_id": action.requested_by,
                                        "authorization_epoch": authorization_epoch,
                                        "limit": min(
                                            max(int(action.payload.get("limit", 1000)), 1),
                                            1000,
                                        ),
                                    },
                                    status="queued",
                                    max_attempts=3,
                                )
                                session.add(job)
                                await session.flush()
                            source = await session.get(KnowledgeSource, chat_id)
                            if not source:
                                source = KnowledgeSource(chat_id=chat_id)
                                session.add(source)
                            source.status = job.status
                            source.requested_for_learning = True
                            source.last_job_id = job.id
                            source.last_error = None
                            action.payload = {
                                **action.payload,
                                "job_id": job.id,
                                "job_status": job.status,
                                "deduplicated": active_job is not None,
                            }
                        elif action.action_type == "enable_group_learning_bulk":
                            chat_ids = list(
                                dict.fromkeys(
                                    int(chat_id) for chat_id in action.payload.get("chat_ids", [])
                                )
                            )
                            valid_ids = set(
                                (
                                    await session.scalars(
                                        select(TelegramChat.chat_id).where(
                                            TelegramChat.chat_id.in_(chat_ids),
                                            TelegramChat.chat_type.in_(
                                                ("group", "supergroup", "channel")
                                            ),
                                        )
                                    )
                                ).all()
                            )
                            existing_jobs = list(
                                (
                                    await session.scalars(
                                        select(BackgroundJob).where(
                                            BackgroundJob.job_type == "learn_group",
                                            BackgroundJob.status.in_(
                                                (
                                                    "queued",
                                                    "running",
                                                    "paused",
                                                    "pause_requested",
                                                )
                                            ),
                                        )
                                    )
                                ).all()
                            )
                            active_ids = {
                                int(item.payload["chat_id"])
                                for item in existing_jobs
                                if (item.payload or {}).get("chat_id") is not None
                            }
                            queued_jobs: list[BackgroundJob] = []
                            for chat_id in chat_ids:
                                if chat_id not in valid_ids or chat_id in active_ids:
                                    continue
                                await validate_action_epoch(session, action)
                                await self.policy.apply_template(session, chat_id, "knowledge")
                                authorization_epoch = await source_epoch(session, chat_id)
                                job = BackgroundJob(
                                    job_type="learn_group",
                                    payload={
                                        "batch_action_id": action.action_id,
                                        "authorization_epoch": authorization_epoch,
                                        "chat_id": chat_id,
                                        "owner_id": action.requested_by,
                                        "limit": min(
                                            max(
                                                int(
                                                    action.payload.get(
                                                        "limit",
                                                        1000,
                                                    )
                                                ),
                                                1,
                                            ),
                                            1000,
                                        ),
                                    },
                                    status="queued",
                                    max_attempts=3,
                                )
                                session.add(job)
                                queued_jobs.append(job)
                            await session.flush()
                            for job in queued_jobs:
                                payload = job.payload or {}
                                chat_id = int(payload["chat_id"])
                                source = await session.get(KnowledgeSource, chat_id)
                                if not source:
                                    source = KnowledgeSource(chat_id=chat_id)
                                    session.add(source)
                                source.status = "queued"
                                source.requested_for_learning = True
                                source.last_job_id = job.id
                                source.last_error = None
                            action.payload = {
                                **action.payload,
                                "queued_count": len(queued_jobs),
                                "deduplicated_count": len(valid_ids) - len(queued_jobs),
                            }
                        elif action.action_type == "leave_telegram_chat":
                            chat_id = int(action.chat_id)
                            if current_rights.get("is_creator"):
                                raise PermissionError(
                                    "Không thể rời nguồn mà tài khoản đang là chủ sở hữu."
                                )
                            if current_rights.get("is_admin") and not bool(
                                action.payload.get("acknowledge_admin")
                            ):
                                raise PermissionError(
                                    "Nguồn đang có quyền admin và chưa xác nhận ảnh hưởng."
                                )
                            await self.user.leave_chat(chat_id)
                            await RevocationService(self.database, int(self.user.owner_id)).revoke_in_session(session, chat_id, action.requested_by, "archive")
                            for permission in (
                                PermissionName.AUTO_KNOWLEDGE,
                                PermissionName.GROUP_AI_ASK,
                            ):
                                await self.policy.set_permission(
                                    session, chat_id, permission, False
                                )
                            if chat:
                                chat.account_rights = {
                                    **(chat.account_rights or {}),
                                    "membership_status": "left",
                                    "left_at": datetime.now(UTC).isoformat(),
                                }
                            if action.payload.get("data_action") == "request_delete":
                                source = await session.get(KnowledgeSource, chat_id)
                                follow_up = await PendingActionService(
                                    ttl_seconds=self.settings.confirmation_ttl_seconds
                                ).create(
                                    session,
                                    action_type="delete_learned_data",
                                    requested_by=action.requested_by,
                                    chat_id=chat_id,
                                    payload={"scope": "all"},
                                    preview=(
                                        f"Xóa toàn bộ dữ liệu đã học của "
                                        f"{chat.title if chat else chat_id}: "
                                        f"{source.mysql_message_count if source else 0} tin, "
                                        f"{source.vector_count if source else 0} vector."
                                    ),
                                    reason=(
                                        "Yêu cầu xóa được tạo riêng sau khi rời; "
                                        "owner phải xác nhận lần thứ hai."
                                    ),
                                )
                                action.payload = {
                                    **action.payload,
                                    "delete_action_id": follow_up.action_id,
                                }
                        elif action.action_type == "delete_learned_data":
                            chat_id = int(action.chat_id)
                            active_jobs = list(
                                (
                                    await session.scalars(
                                        select(BackgroundJob).where(
                                            BackgroundJob.job_type == "learn_group",
                                            BackgroundJob.status.in_(
                                                (
                                                    "queued",
                                                    "running",
                                                    "paused",
                                                    "pause_requested",
                                                )
                                            ),
                                        )
                                    )
                                ).all()
                            )
                            if any(
                                str((job.payload or {}).get("chat_id")) == str(chat_id)
                                for job in active_jobs
                            ):
                                raise RuntimeError(
                                    "Nguồn có learning job hoạt động; thao tác xóa bị chặn."
                                )
                            scope = str(action.payload["scope"])
                            removed_vectors = 0
                            removed_messages = 0
                            removed_media = 0
                            async with self._knowledge_lock:
                                if scope in {
                                    "vectors_only",
                                    "search_index",
                                    "mysql_content",
                                    "all",
                                } and self.rag.vectors:
                                    reference_ids = self.rag.vectors.reference_ids(
                                        chat_id=chat_id
                                    )
                                    removed_vectors = (
                                        self.rag.vectors.delete_reference_ids(reference_ids)
                                    )
                                if scope in {"media_only", "mysql_content", "all"}:
                                    attachments = list(
                                        (
                                            await session.scalars(
                                                select(TelegramAttachment)
                                                .join(
                                                    TelegramMessage,
                                                    TelegramAttachment.telegram_message_id
                                                    == TelegramMessage.id,
                                                )
                                                .where(TelegramMessage.chat_id == chat_id)
                                            )
                                        ).all()
                                    )
                                    downloads_root = self.paths["downloads"].resolve()
                                    for attachment in attachments:
                                        if attachment.local_path:
                                            candidate = Path(attachment.local_path).resolve()
                                            try:
                                                candidate.relative_to(downloads_root)
                                            except ValueError:
                                                candidate = None
                                            if candidate and candidate.is_file():
                                                candidate.unlink(missing_ok=True)
                                                removed_media += 1
                                        if scope == "media_only":
                                            attachment.local_path = None
                                            attachment.size_bytes = None
                                if scope in {"mysql_content", "all"}:
                                    removed_messages = int(
                                        (
                                            await session.scalar(
                                                select(func.count(TelegramMessage.id)).where(
                                                    TelegramMessage.chat_id == chat_id
                                                )
                                            )
                                        )
                                        or 0
                                    )
                                    await session.execute(
                                        delete(TelegramMessage).where(
                                            TelegramMessage.chat_id == chat_id
                                        )
                                    )
                                if scope in {
                                    "search_index",
                                    "mysql_content",
                                    "all",
                                    "reset_checkpoint",
                                }:
                                    await session.execute(
                                        delete(AppSetting).where(
                                            AppSetting.key.in_(
                                                (
                                                    f"knowledge_checkpoint:{chat_id}",
                                                    f"knowledge_checkpoint:ollama:{chat_id}",
                                                )
                                            )
                                        )
                                    )
                                source = await session.get(KnowledgeSource, chat_id)
                                if source:
                                    if scope in {
                                        "vectors_only",
                                        "search_index",
                                        "mysql_content",
                                        "all",
                                    }:
                                        source.vector_count = 0
                                        source.last_indexed_count = 0
                                    if scope in {"mysql_content", "all"}:
                                        source.mysql_message_count = 0
                                        source.text_message_count = 0
                                        source.message_storage_bytes = 0
                                    if scope in {"media_only", "mysql_content", "all"}:
                                        source.media_storage_bytes = 0
                                    if scope == "all":
                                        source.status = "not_learned"
                                        source.requested_for_learning = False
                                        source.last_job_id = None
                                        source.last_learned_at = None
                                        await self.policy.set_permission(
                                            session,
                                            chat_id,
                                            PermissionName.AUTO_KNOWLEDGE,
                                            False,
                                        )
                            action.payload = {
                                **action.payload,
                                "result": {
                                    "removed_messages": removed_messages,
                                    "removed_vectors": removed_vectors,
                                    "removed_media_files": removed_media,
                                },
                            }
                        elif action.action_type == "storage_cleanup":
                            async with self._knowledge_lock:
                                report = await cleanup_storage(
                                    session,
                                    self.rag.vectors,
                                    media_root=self.paths["downloads"],
                                    dry_run=False,
                                )
                            action.payload = {
                                **action.payload,
                                "result": {
                                    "expired_messages": report.expired_messages,
                                    "expired_vectors": report.expired_vectors,
                                    "orphan_vectors": report.orphan_vectors,
                                    "media_files": report.media_files,
                                    "media_bytes": report.media_bytes,
                                },
                            }
                        elif action.action_type == "delete_ollama_model":
                            model = str(action.payload.get("model", "")).strip()
                            active_models = {
                                self.settings.ollama_primary_model,
                                self.settings.ollama_embedding_model,
                            }
                            if model in active_models:
                                raise PermissionError(
                                    "Không thể xóa model Ollama đang được cấu hình làm model hoạt động."
                                )
                            await self.ollama.delete_model(model)
                        elif action.action_type == "create_memory":
                            await MemoryService().create(
                                session,
                                content=str(action.payload["content"]),
                                memory_type=str(action.payload["memory_type"]),
                                scope_type=str(action.payload["scope_type"]),
                                scope_id=action.payload.get("scope_id"),
                                confirmed=True,
                                source_chat_id=(
                                    int(action.payload["scope_id"])
                                    if action.payload["scope_type"] == "chat"
                                    else None
                                ),
                            )
                        elif action.action_type == "forget_memory":
                            memory = await session.get(AiMemory, str(action.payload["memory_id"]))
                            if not memory or memory.status != "active":
                                raise LookupError("Memory không còn hoạt động.")
                            memory.status = "forgotten"
                        elif action.action_type == "delete_task":
                            await TaskService().update_status(
                                session,
                                str(action.payload["task_id"]),
                                TaskStatus.CANCELLED,
                                changed_by="owner_confirmed",
                            )
                        else:
                            raise ValueError("Loại action không hỗ trợ")
                        if action.action_type not in {"set_chat_allowed", "set_chat_permission", "apply_permission_template", "setup_moderation", "set_admin_only_auto_moderation", "set_link_spam_auto_moderation", "set_group_ai_ask", "enable_group_learning", "enable_group_learning_bulk", "leave_telegram_chat"}:
                            await self._action_fence(action)
                        action.status, action.executed_at = "executed", datetime.now(UTC)
                        session.add(self._audit_row(action, "success"))
                        # Release DB grant/epoch locks before Telegram notification.
                        # The durable result must not depend on a slow bot request.
                        await session.commit()
                        if action.action_type == "enable_group_learning" and self.bot and chat:
                            try:
                                await self.bot.bot.send_message(
                                    action.requested_by,
                                    "ĐÃ HỌC XONG GROUP\n\n"
                                    f"Group: {chat.title or chat.chat_id}\n"
                                    f"Đồng bộ MySQL: {action.payload.get('synced_count', 0)} tin\n"
                                    f"Embedding: {action.payload.get('indexed_count', 0)} tin\n\n"
                                    "Các tin mới sẽ tiếp tục được đưa vào kho kiến thức. "
                                    "Bạn có thể dùng “Hỏi AI” ngay.",
                                )
                            except Exception as exc:
                                log.warning(
                                    "knowledge_completion_notice_failed",
                                    action_id=action.action_id,
                                    error=str(redact(str(exc))),
                                )
                    except Exception as exc:
                        action.status = "uncertain" if (action.payload or {}).get("external_effect_started") else ("cancelled" if isinstance(exc, AuthorizationRevoked) else "failed")
                        action.error = str(redact(str(exc)))[:1000]
                        session.add(self._audit_row(action, "failed", str(exc)[:1000]))
                        log.warning("action_failed", action_id=action.action_id, error=str(exc))
            await asyncio.sleep(2)

    async def run(self) -> None:
        me = await self.user.authenticate(self.store.get("telegram_phone") or "")
        async with self.database.session() as session:
            await ensure_vector_store_registry(session, self.settings)
            account = await session.scalar(
                select(TelegramAccount).where(TelegramAccount.telegram_user_id == int(me.id))
            )
            if not account or not account.is_owner_paired:
                raise RuntimeError("Chưa pair owner; chạy tg-assistant start ở terminal")
            await self.user.discover_dialogs(session)
            recovered_jobs = await recover_interrupted_learning_jobs(session)
            if recovered_jobs:
                log.info(
                    "learning_jobs_recovered_on_startup",
                    recovered_jobs=recovered_jobs,
                )
        self.user.register_handlers(
            self.database,
            group_ask_handler=self._handle_group_ai_ask,
        )
        self.bot = ControlBot(
            self.store.get("telegram_bot_token") or "",
            owner_id=int(me.id),
            database=self.database,
            policy=self.policy,
            ai=self.ai,
            rag=self.rag,
            budget=self.budget,
            coingecko=self.coingecko,
            ollama=self.ollama,
            ollama_activate_handler=self._activate_ollama,
            ai_provider_switch_handler=self._switch_ai_provider,
        )
        self.scheduler.start()
        self.scheduler.add_job(
            self._dispatch_reminders,
            "interval",
            seconds=30,
            id="dispatch_reminders",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._refresh_group_knowledge,
            "interval",
            minutes=5,
            id="refresh_group_knowledge",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._process_learning_jobs,
            "interval",
            seconds=self.settings.learning_job_interval_seconds,
            id="process_learning_jobs",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._collect_operations_metric,
            "interval",
            seconds=60,
            id="collect_operations_metric",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._run_storage_cleanup,
            "interval",
            hours=1,
            id="storage_cleanup",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._process_admin_jobs,
            "interval",
            seconds=3,
            id="process_admin_jobs",
            max_instances=1,
            coalesce=True,
        )
        log.info("application_started", owner_id=int(me.id))
        tasks = [
            asyncio.create_task(self.bot.run()),
            asyncio.create_task(self.user.client.run_until_disconnected()),
            asyncio.create_task(self._execute_actions()),
        ]
        if self.settings.admin_api_enabled:
            admin_app = create_admin_app(
                AdminContext(
                    database=self.database,
                    policy=self.policy,
                    owner_id=int(me.id),
                    settings_getter=lambda: self.settings,
                    vectors_getter=lambda: self.rag.vectors,
                    ollama=self.ollama,
                    ai_switch_handler=self._switch_ai_provider,
                    ollama_activate_handler=self._activate_ollama,
                    pause_all_handler=pause_learning_jobs,
                    resume_all_handler=resume_learning_jobs,
                    paths=self.paths,
                    admin_secret=ensure_dashboard_secret(self.store),
                    scheduler_getter=lambda: self.scheduler,
                    history_ai_filter_handler=self._classify_history_delete_with_openai,
                    history_sender_lookup_handler=self._resolve_history_sender_identity,
                )
            )
            self.admin_server = uvicorn.Server(
                uvicorn.Config(
                    admin_app,
                    host=self.settings.admin_api_host,
                    port=self.settings.admin_api_port,
                    access_log=False,
                    log_config=None,
                    server_header=False,
                )
            )
            self.admin_server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
            tasks.append(asyncio.create_task(self.admin_server.serve()))
            log.info(
                "admin_api_started",
                url=(
                    f"http://{self.settings.admin_api_host}:"
                    f"{self.settings.admin_api_port}"
                ),
            )
        try:
            await self.stopping.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.close()

    async def close(self) -> None:
        self.stopping.set()
        if self.admin_server:
            self.admin_server.should_exit = True
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        if self.bot:
            await self.bot.close()
        if self.rag.vectors:
            self.rag.vectors.close()
        await self.ai_router.close()
        await self.ollama.close()
        await self.coingecko.close()
        await self.user.close()
        await self.database.close()
        log.info("application_stopped")


async def run_application() -> None:
    app = Application()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, app.stopping.set)
        except NotImplementedError:
            pass
    await app.run()
