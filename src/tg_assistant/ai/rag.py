from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import folded_contains
from ..db.models import (
    AiQueryCache,
    PermissionName,
    TelegramChat,
    TelegramChatPolicy,
    TelegramMessage,
    VectorStore,
)
from ..policy import PolicyEngine
from ..security import redact
from ..services.provider_connections import ModelSelection
from ..services.revocation import AuthorizedAnswer, require_authorization, validate_answer
from ..services.search import SearchQuery, SearchService, parse_search_query
from .budget import BudgetService, PricingSnapshot, pricing_for, reservation_model
from .engine import AiEngine, AiPolicyError, AiUnavailableError, AiUncertainError
from .local_first import (
    PRESETS,
    classify_query,
    estimate_tokens,
    query_cache_key,
    query_cache_ttl,
    route_feature,
)
from .observations import (
    CitedReference,
    FirstValueAnswerObservation,
    NonqualifyingRagResult,
    QualifyingRagSuccess,
    RagObservedResult,
    RetrievalScope,
    SourceIndexBinding,
)
from .router import AiRouter
from .vector import LocalVectorStore

VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
PUBLIC_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,}$")
SOURCE_REFERENCE_RE = re.compile(r"\s*\[S\d+]", re.IGNORECASE)
SOURCE_REFERENCE_CAPTURE_RE = re.compile(r"\[S(\d+)]", re.IGNORECASE)
DEFAULT_RAG_LOOKBACK = timedelta(days=7)


def parse_selected_query(raw: str, *, selected_chat_id: int) -> SearchQuery:
    """Only standalone documented filters are syntax for the selected purpose."""
    values = {}
    filter_tokens = list(re.finditer(r"(?<!\S)(?:in|from|after|before|has|type):\S*", raw))
    for match in filter_tokens:
        token = match.group()
        name, separator, value = token.partition(":")
        if name in values or not value:
            raise ValueError("invalid_filter")
        if name in {"in", "from"} and not re.fullmatch(r"-?[1-9][0-9]*", value):
            raise ValueError("invalid_id")
        if name in {"after", "before"} and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("invalid_date")
        values[name] = value
    parsed = parse_search_query(" ".join(f"{name}:{value}" for name, value in values.items()))
    if parsed.chat_id not in (None, selected_chat_id):
        raise ValueError("foreign_source")
    parsed.chat_id = selected_chat_id
    parsed.text = re.sub(r"(?<!\S)(?:in|from|after|before|has|type):\S*", "", raw).strip()
    return parsed


def selected_evidence_indexes(answer: str, *, item_count: int) -> list[int] | None:
    if re.search(r"(?<!\[)\bS[0-9]+\]", answer, re.IGNORECASE):
        return None
    markers = re.findall(r"\[\s*S[^\]\s]*(?:\]|(?=\s|$))", answer, re.IGNORECASE)
    if not markers or any(
        not re.fullmatch(r"\[S[1-9][0-9]*\]", marker, re.IGNORECASE) for marker in markers
    ):
        return None
    if any(
        len(marker[2:-1]) > len(str(item_count)) or int(marker[2:-1]) > item_count
        for marker in markers
    ):
        return None
    return referenced_evidence_indexes(answer, item_count=item_count, fallback=0)


def _selected_filters(scope: RetrievalScope) -> list:
    filters = [
        TelegramMessage.chat_id == scope.selected_chat_id,
        TelegramMessage.sent_at <= scope.before,
        TelegramMessage.is_deleted.is_(False),
        TelegramMessage.text.is_not(None),
    ]
    if scope.after is not None:
        filters.append(TelegramMessage.sent_at >= scope.after)
    if scope.sender_id is not None:
        filters.append(TelegramMessage.sender_id == scope.sender_id)
    if scope.has == "link":
        filters.append(
            or_(
                folded_contains(TelegramMessage.text, "http://"),
                folded_contains(TelegramMessage.text, "https://"),
            )
        )
    elif scope.has:
        filters.append(TelegramMessage.has_media.is_(True))
    if scope.content_type:
        terms = (
            ("cần ", "deadline", "todo")
            if scope.content_type == "task"
            else ("quyết định", "thống nhất")
        )
        filters.append(or_(*(folded_contains(TelegramMessage.text, term) for term in terms)))
    return filters


def rag_time_window(asked_at: datetime | None = None) -> tuple[datetime, datetime]:
    """Return the rolling 7×24-hour retrieval window ending when the question is asked."""
    end = asked_at or datetime.now(UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return end - DEFAULT_RAG_LOOKBACK, end


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    chat_id: int
    message_id: int
    score: float
    text: str
    sent_at: datetime | None
    title: str
    url: str | None
    citation_priority: str = "standard"


def telegram_message_url(chat: TelegramChat, message_id: int) -> str | None:
    """Build a Telegram post URL when the chat type supports one."""
    username = (chat.username or "").strip().lstrip("@")
    if username and PUBLIC_USERNAME_RE.fullmatch(username):
        return f"https://t.me/{username}/{message_id}"
    raw_chat_id = str(chat.chat_id)
    if chat.chat_type in {"supergroup", "channel"} and raw_chat_id.startswith("-100"):
        internal_id = raw_chat_id[4:]
        if internal_id.isdigit():
            return f"https://t.me/c/{internal_id}/{message_id}"
    return None


def _source_time(value: datetime | None) -> str:
    if value is None:
        return "không rõ thời gian"
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(VIETNAM_TZ).strftime("%d/%m/%Y %H:%M")


def _clean_title(value: str | None, chat_id: int) -> str:
    title = str(redact(value or "")).replace("\r", " ").replace("\n", " ").strip()
    return title or f"Chat {chat_id}"


def source_context(index: int, evidence: SourceEvidence) -> str:
    link = evidence.url or "không tạo được link bài viết"
    return (
        f"[S{index}]\n"
        f"Group/channel: {evidence.title}\n"
        f"Thời gian đăng: {_source_time(evidence.sent_at)} (giờ Việt Nam)\n"
        f"Link: {link}\n"
        f"Evidence priority: {evidence.citation_priority}\n"
        f"Tham chiếu nội bộ: {evidence.chat_id}/{evidence.message_id}\n"
        f"Nội dung: {evidence.text}"
    )


def citation_appendix(
    evidence_rows: list[SourceEvidence],
    *,
    source_numbers: list[int] | None = None,
) -> str:
    lines = ["DẪN CHỨNG"]
    numbers = source_numbers or list(range(1, len(evidence_rows) + 1))
    for index, evidence in zip(numbers, evidence_rows, strict=True):
        posted_at = _source_time(evidence.sent_at)
        if evidence.url:
            lines.append(
                f"• [S{index}] {evidence.title} — đăng lúc {posted_at} (giờ Việt Nam)\n"
                f"  {evidence.url}"
            )
        else:
            lines.append(
                f"• [S{index}] Tên group/channel: {evidence.title} — "
                f"đăng lúc {posted_at} (giờ Việt Nam)"
            )
    return "\n".join(lines)


def without_source_references(answer: str) -> str:
    """Remove inline source codes and any model-generated evidence appendix."""
    body = re.split(
        r"(?im)^\s*(?:#+\s*)?DẪN\s+CHỨNG\s*$",
        answer,
        maxsplit=1,
    )[0]
    body = SOURCE_REFERENCE_RE.sub("", body)
    body = re.sub(r"[ \t]+([,.;:!?])", r"\1", body)
    body = re.sub(r"[ \t]+\n", "\n", body)
    return body.strip()


def referenced_evidence_indexes(
    answer: str,
    *,
    item_count: int,
    fallback: int = 3,
) -> list[int]:
    """Return zero-based evidence indexes explicitly used by the answer."""
    indexes: list[int] = []
    for raw_index in SOURCE_REFERENCE_CAPTURE_RE.findall(answer):
        index = int(raw_index) - 1
        if 0 <= index < item_count and index not in indexes:
            indexes.append(index)
    if indexes:
        return indexes
    return list(range(min(item_count, fallback)))


def renumber_source_references(answer: str, indexes: list[int]) -> str:
    """Renumber retained source references so the visible sequence starts at S1."""
    mapping = {
        source_index + 1: visible_index for visible_index, source_index in enumerate(indexes, 1)
    }

    def replace(match: re.Match[str]) -> str:
        original = int(match.group(1))
        return f"[S{mapping[original]}]" if original in mapping else ""

    return SOURCE_REFERENCE_CAPTURE_RE.sub(replace, answer)


class RagService:
    def __init__(
        self,
        policy: PolicyEngine,
        ai: AiEngine,
        vectors: LocalVectorStore | None = None,
        router: AiRouter | None = None,
        embedding_ai: AiEngine | None = None,
        default_preset: str = "balanced",
    ) -> None:
        self.policy, self.ai, self.vectors = policy, ai, vectors
        self.router = router
        self.embedding_ai = embedding_ai or ai
        self.default_preset = default_preset if default_preset in PRESETS else "balanced"
        self.keyword = SearchService(policy)

    async def answer_selected_observed(
        self,
        session: AsyncSession,
        question: str,
        *,
        actor_id: int,
        owner_id: int,
        binding: SourceIndexBinding,
        pre_submit,
    ) -> RagObservedResult:
        if not callable(pre_submit):
            raise AiPolicyError("Admission callback required")
        if not isinstance(binding, SourceIndexBinding):
            raise AiPolicyError("Selected source binding required")
        if (
            type(actor_id) is not int
            or type(owner_id) is not int
            or actor_id != owner_id
            or owner_id != binding.owner_id
        ):
            raise AiPolicyError("Selected answer requires the bound owner")
        try:
            parsed = parse_selected_query(question, selected_chat_id=binding.selected_chat_id)
        except ValueError:
            return NonqualifyingRagResult("invalid_filter")
        if not parsed.text:
            return NonqualifyingRagResult("empty_question")
        database = getattr(self, "database", None)
        provider_fence = getattr(self, "provider_fence", None)
        ai, embedding, vectors, router = self.ai, self.embedding_ai, self.vectors, self.router
        policy_owner, keyword = self.policy, self.keyword
        database_engine = getattr(database, "engine", None)
        session_bind, sync_bind = session.bind, session.sync_session.bind
        database_sessions = getattr(database, "sessions", None)
        database_sessions_engine = getattr(database_sessions, "kw", {}).get("bind")
        if (
            database is None
            or database_engine is None
            or database_sessions_engine is not database_engine
            or session.bind is not database_engine
            or sync_bind is not database_engine.sync_engine
            or not callable(provider_fence)
            or not isinstance(ai, AiEngine)
            or not isinstance(embedding, AiEngine)
            or not isinstance(embedding.budget, BudgetService)
            or not isinstance(vectors, LocalVectorStore)
        ):
            return NonqualifyingRagResult("owner_unavailable")
        profile = vectors.profile
        vector_client = vectors.client
        embedding_budget = embedding.budget
        embedding_client, embedding_model = embedding.client, embedding.embedding_model
        configured_engines = router.engines if router else None
        configured_chat_owners = {
            provider: (provider, engine, engine.client, engine.model, engine.budget)
            for provider, engine in (configured_engines.items() if router else [(ai.provider, ai)])
        }
        router_settings = (
            (router.enabled, router.default_provider, router.cloud_consent) if router else None
        )
        engine_owners = tuple(
            (
                engine,
                engine.client,
                engine.model,
                engine.budget,
                getattr(engine.budget, "profile_id", None),
                engine.provider,
                engine.embedding_model,
                engine.embedding_dimension,
                engine.cloud_consent,
                str(engine.client.base_url) if engine.client is not None else None,
            )
            for engine in [ai, embedding] + [owner[1] for owner in configured_chat_owners.values()]
        )

        def ensure_owners():
            if (
                self.ai is not ai
                or self.embedding_ai is not embedding
                or self.vectors is not vectors
                or self.router is not router
                or self.policy is not policy_owner
                or self.keyword is not keyword
                or getattr(self, "database", None) is not database
                or database.engine is not database_engine
                or getattr(database, "sessions", None) is not database_sessions
                or database_sessions.kw.get("bind") is not database_sessions_engine
                or session.sync_session.bind is not sync_bind
                or session.bind is not session_bind
                or getattr(self, "provider_fence", None) is not provider_fence
                or vectors.profile is not profile
                or vectors.client is not vector_client
                or (
                    router
                    and (
                        router.engines is not configured_engines
                        or tuple(router.engines) != tuple(configured_chat_owners)
                        or (router.enabled, router.default_provider, router.cloud_consent)
                        != router_settings
                        or any(
                            router.engines.get(provider) is not owner[1]
                            for provider, owner in configured_chat_owners.items()
                        )
                    )
                )
            ):
                raise AiPolicyError("Selected request owner changed")
            for (
                engine,
                client,
                model,
                budget,
                budget_profile,
                provider,
                emb_model,
                dim,
                consent,
                endpoint,
            ) in engine_owners:
                if (
                    engine.client is not client
                    or engine.model != model
                    or engine.budget is not budget
                    or getattr(budget, "profile_id", None) != budget_profile
                    or engine.provider != provider
                    or engine.embedding_model != emb_model
                    or engine.embedding_dimension != dim
                    or engine.cloud_consent != consent
                    or (str(client.base_url) if client is not None else None) != endpoint
                ):
                    raise AiPolicyError("Selected request owner changed")

        await pre_submit()
        ensure_owners()
        if profile is None or embedding_client is None:
            return NonqualifyingRagResult("embedding_unavailable")
        expected_identity = (
            binding.embedding_provider,
            binding.embedding_endpoint_id,
            binding.embedding_model,
            binding.embedding_model_version,
            binding.embedding_dimension,
            binding.embedding_store_id,
        )
        if (
            profile.provider,
            profile.endpoint_id,
            profile.model,
            profile.embedding_version,
            profile.dimension,
            profile.store_id,
        ) != expected_identity or embedding.budget.profile_id != binding.profile_id:
            return NonqualifyingRagResult("embedding_identity_changed")
        recent_since, asked_at = rag_time_window()
        after = (
            parsed.after if parsed.after is not None or parsed.before is not None else recent_since
        )
        before = (
            min(asked_at, parsed.before - timedelta(microseconds=1)) if parsed.before else asked_at
        )
        try:
            scope = RetrievalScope(
                binding.selected_chat_id,
                after,
                before,
                parsed.sender_id,
                parsed.has,
                parsed.content_type,
                classify_query(parsed.text, in_group=True).value,
                "cloud_embedding",
            )
        except ValueError:
            return NonqualifyingRagResult("empty_window")
        allowed = await self.policy.filter_allowed_chat_ids(
            session,
            actor_id=actor_id,
            owner_id=owner_id,
            chat_ids=[binding.selected_chat_id],
            permission=PermissionName.SEARCH_MESSAGES,
        )
        if allowed != [binding.selected_chat_id]:
            return NonqualifyingRagResult("source_denied")
        epoch = await require_authorization(
            session, binding.selected_chat_id, PermissionName.SEARCH_MESSAGES, binding.source_epoch
        )
        epochs = {binding.selected_chat_id: epoch}
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == binding.selected_chat_id)
        )
        ai_route = await self.policy.ai_route(session, allowed)
        provider_plan = tuple(router.plan(ai_route) if router else [ai.provider])
        source_modes = [policy.ai_mode]
        cloud_modes = {"inherit", "local_first", "cloud_only", "cloud_first"}
        if not provider_plan or policy.ai_mode == "off":
            return NonqualifyingRagResult("route_unavailable")
        if (
            any(provider in {"openai", "openrouter"} for provider in provider_plan)
            and policy.ai_mode not in cloud_modes
        ):
            return NonqualifyingRagResult("source_cloud_denied")
        if not embedding.is_local and policy.ai_mode not in cloud_modes:
            return NonqualifyingRagResult("embedding_cloud_denied")
        candidates = {candidate.provider: candidate for candidate in binding.chat_candidates}
        chat_owners = []
        for provider in provider_plan:
            owner = configured_chat_owners.get(provider)
            engine = owner[1] if owner else None
            if (
                not isinstance(engine, AiEngine)
                or not isinstance(engine.budget, BudgetService)
                or provider not in candidates
            ):
                return NonqualifyingRagResult("provider_owner_unavailable")
            chat_owners.append(owner)
        preset = PRESETS.get(policy.ai_efficiency_preset, PRESETS[self.default_preset])
        top_k = min(8, policy.rag_top_k or preset.top_k, preset.max_chunks_per_source)
        max_context_tokens = min(
            preset.max_context_tokens, policy.rag_max_context_tokens or preset.max_context_tokens
        )
        feature = route_feature(classify_query(parsed.text, in_group=True))

        def ensure_current():
            ensure_owners()
            if (
                embedding.client is not embedding_client
                or embedding.embedding_model != embedding_model
                or embedding.budget is not embedding_budget
                or embedding_budget.profile_id != binding.profile_id
            ):
                raise AiPolicyError("Selected request owner changed")
            selection = ModelSelection.parse(
                embedding.provider,
                {
                    "service": "embeddings",
                    "model": embedding.embedding_model,
                    "endpoint": str(embedding_client.base_url),
                    "cloud_consent": embedding.cloud_consent,
                },
            )
            if (
                embedding.provider,
                selection.endpoint_id,
                embedding.embedding_model,
                profile.embedding_version,
                embedding.embedding_dimension,
                profile.store_id,
            ) != expected_identity:
                raise AiPolicyError("Selected embedding identity changed")
            if router and tuple(router.plan(ai_route)) != provider_plan:
                raise AiPolicyError("Selected provider plan changed")
            for provider, engine, client, model, budget in chat_owners:
                if (
                    (router and router.engines.get(provider) is not engine)
                    or engine.budget is not budget
                    or budget.profile_id != binding.profile_id
                ):
                    raise AiPolicyError("Selected provider owner changed")
                engine._observed_identity(candidates[provider], client, model)

        async def fence(*, for_embedding=False):
            ensure_current()
            await provider_fence(
                epochs,
                PermissionName.SEARCH_MESSAGES,
                cloud=not embedding.is_local
                if for_embedding
                else any(provider in {"openai", "openrouter"} for provider in provider_plan),
                store_id=profile.store_id if for_embedding else None,
            )
            ensure_current()
            await pre_submit()
            ensure_current()

        await fence()
        store = await session.get(VectorStore, profile.store_id)
        if (
            store is None
            or store.role != "semantic_active"
            or store.state != "active"
            or (
                store.provider,
                store.endpoint_id,
                store.model,
                store.embedding_version,
                store.dimension,
                store.store_id,
            )
            != expected_identity
        ):
            return NonqualifyingRagResult("index_unavailable")
        # SearchQuery remains ordinary mutable state; this private copy comes only from the frozen scope.
        keyword_query = SearchQuery(
            parsed.text,
            scope.selected_chat_id,
            scope.sender_id,
            scope.after,
            scope.before + timedelta(microseconds=1),
            scope.has,
            scope.content_type,
        )
        keywords = await keyword.keyword(
            session,
            parsed.text,
            owner_id=owner_id,
            actor_id=actor_id,
            chat_ids=allowed,
            limit=top_k,
            preauthorized=True,
            parsed_query=keyword_query,
        )
        keyword_keys = {(result.chat_id, result.message_id) for result in keywords}
        await fence()
        filters = _selected_filters(scope)
        reference_ids = list(
            (await session.scalars(select(TelegramMessage.id).where(*filters))).all()
        )
        await fence(for_embedding=True)
        await session.commit()
        request_id = uuid4().hex
        started_at = datetime.now(UTC)

        async def embedding_fence():
            await fence(for_embedding=True)

        try:
            query_vector = await embedding.embed(
                session,
                parsed.text,
                feature="embedding",
                chat_id=scope.selected_chat_id,
                source_modes=source_modes,
                request_id=request_id,
                pre_submit=embedding_fence,
            )
        except (AiUncertainError, AiUnavailableError):
            return NonqualifyingRagResult("embedding_failed")
        finished_at = datetime.now(UTC)
        await fence(for_embedding=True)
        if (
            not isinstance(query_vector, list)
            or len(query_vector) != profile.dimension
            or any(
                type(value) not in (int, float) or not math.isfinite(value)
                for value in query_vector
            )
        ):
            return NonqualifyingRagResult("embedding_invalid")
        async with embedding_budget._sessions(session)() as fresh:
            ledger = await fresh.get(reservation_model(), request_id)

            def utc(value):
                return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value

            stamps = tuple(
                utc(getattr(ledger, name, None))
                for name in ("occurred_at", "submitted_at", "settled_at")
            )
            counts = tuple(
                getattr(ledger, name, None)
                for name in (
                    "actual_input_tokens",
                    "actual_output_tokens",
                    "cached_tokens",
                    "cache_write_tokens",
                )
            )
            if (
                ledger is None
                or (
                    ledger.profile_id,
                    ledger.provider,
                    ledger.model,
                    ledger.operation,
                    ledger.feature,
                    ledger.route,
                    ledger.chat_id,
                )
                != (
                    binding.profile_id,
                    embedding.provider,
                    embedding_model,
                    "embedding",
                    "embedding",
                    "cloud_embedding",
                    scope.selected_chat_id,
                )
                or ledger.is_local is not embedding.is_local
                or ledger.fallback_used is not False
                or ledger.state != "settled"
                or any(value is None for value in stamps)
                or not started_at <= stamps[0] <= stamps[1] <= stamps[2] <= finished_at
                or any(type(count) is not int or count < 0 for count in counts)
                or counts[1] != 0
                or counts[2] + counts[3] > counts[0]
                or type(ledger.actual_cost_usd) is not Decimal
                or not ledger.actual_cost_usd.is_finite()
                or ledger.actual_cost_usd < 0
                or not ledger.pricing_version
            ):
                return NonqualifyingRagResult("embedding_unsettled")
            try:
                pricing = PricingSnapshot.from_dict(ledger.pricing_rates)
                expected_pricing = (
                    PricingSnapshot(
                        embedding.provider,
                        embedding_model,
                        "local-zero-v1",
                        "local",
                        *(Decimal(0) for _ in range(4)),
                        datetime(9999, 1, 1, tzinfo=UTC),
                    )
                    if embedding.is_local
                    else pricing_for(embedding.provider, embedding_model, now=stamps[0])
                )
                if (
                    pricing != expected_pricing
                    or ledger.pricing_version != pricing.version
                    or ledger.actual_cost_usd
                    != pricing.cost(
                        counts[0], counts[1], cached_tokens=counts[2], cache_write_tokens=counts[3]
                    )
                ):
                    return NonqualifyingRagResult("embedding_unsettled")
            except (ValueError, TypeError, KeyError, ArithmeticError):
                return NonqualifyingRagResult("embedding_unsettled")
        await fence()
        semantic_keys = set()
        combined = {(result.chat_id, result.message_id): result.score for result in keywords}
        for _, score, payload in vectors.search(
            query_vector, allowed_chat_ids=allowed, allowed_reference_ids=reference_ids, limit=top_k
        ):
            row = await session.scalar(
                select(TelegramMessage).where(
                    *filters,
                    TelegramMessage.chat_id == payload.get("chat_id"),
                    TelegramMessage.message_id == payload.get("message_id"),
                    TelegramMessage.id == payload.get("reference_id"),
                )
            )
            if row is not None:
                key = (row.chat_id, row.message_id)
                semantic_keys.add(key)
                combined[key] = score
        await fence()
        chat = await session.get(TelegramChat, scope.selected_chat_id)
        contexts, evidence_rows, actual_rows = [], [], []
        context_tokens = 0
        for (chat_id, message_id), score in sorted(
            combined.items(), key=lambda item: item[1], reverse=True
        )[:top_k]:
            row = await session.scalar(
                select(TelegramMessage)
                .where(*filters, TelegramMessage.message_id == message_id)
                .execution_options(populate_existing=True)
            )
            if row is None or not row.text:
                continue
            evidence = SourceEvidence(
                chat_id,
                message_id,
                score,
                str(redact(row.text))[: preset.max_chunk_tokens * 4],
                row.sent_at,
                _clean_title(chat.title if chat else None, chat_id),
                telegram_message_url(chat, message_id) if chat else None,
            )
            context = source_context(len(contexts) + 1, evidence)
            tokens = estimate_tokens(context)
            if contexts and context_tokens + tokens > max_context_tokens:
                break
            contexts.append(context)
            evidence_rows.append(evidence)
            # Capture actual bytes before provider awaits or ORM expiry/mutation.
            actual_rows.append((row.id, hashlib.sha256(row.text.encode()).hexdigest()))
            context_tokens += tokens
        if not contexts:
            return NonqualifyingRagResult("no_content")
        await fence()
        await session.commit()
        try:
            if router:
                result = await router.answer_observed(
                    session,
                    parsed.text,
                    contexts,
                    route=ai_route,
                    candidates=binding.chat_candidates,
                    feature=feature,
                    query_route=scope.query_route,
                    chat_id=scope.selected_chat_id,
                    source_modes=source_modes,
                    pre_submit=fence,
                )
            else:
                candidate = next(
                    (
                        candidate
                        for candidate in binding.chat_candidates
                        if candidate.provider == ai.provider
                    ),
                    None,
                )
                result = await ai.answer_observed(
                    session,
                    parsed.text,
                    contexts,
                    candidate=candidate,
                    feature=feature,
                    route=scope.query_route,
                    chat_id=scope.selected_chat_id,
                    source_modes=source_modes,
                    pre_submit=fence,
                )
        except (AiUncertainError, AiUnavailableError):
            return NonqualifyingRagResult("provider_failed")
        await fence()
        indexes = selected_evidence_indexes(result.answer, item_count=len(evidence_rows))
        if not indexes:
            return NonqualifyingRagResult("citations_invalid")
        cited_refs = tuple(
            CitedReference(
                evidence_rows[index].chat_id,
                evidence_rows[index].message_id,
                actual_rows[index][0],
                actual_rows[index][1],
                hashlib.sha256(contexts[index].encode()).hexdigest(),
                (evidence_rows[index].chat_id, evidence_rows[index].message_id) in semantic_keys,
                (evidence_rows[index].chat_id, evidence_rows[index].message_id) in keyword_keys,
            )
            for index in indexes
        )
        semantic = any(ref.semantic_used for ref in cited_refs)
        keyword = any(ref.keyword_used for ref in cited_refs)
        observation = FirstValueAnswerObservation(
            binding,
            result.execution,
            request_id,
            cited_refs,
            "hybrid" if semantic and keyword else "semantic" if semantic else "keyword",
            scope.after,
            scope.before,
            scope.sender_id,
            scope.has,
            scope.content_type,
        )
        answer = renumber_source_references(result.answer, indexes)
        answer = (
            f"{answer.rstrip()}\n\n{citation_appendix([evidence_rows[index] for index in indexes])}"
        )
        return QualifyingRagSuccess(AuthorizedAnswer(answer, epochs), observation)

    async def answer(
        self,
        session: AsyncSession,
        question: str,
        *,
        actor_id: int,
        owner_id: int,
        chat_ids: list[int],
        top_k: int = 8,
        include_citations: bool = True,
    ) -> str:
        parsed = parse_search_query(question)
        available_chat_ids = set(chat_ids)
        requested_chat_ids = (
            [parsed.chat_id]
            if parsed.chat_id is not None and parsed.chat_id in available_chat_ids
            else ([] if parsed.chat_id is not None else chat_ids)
        )
        allowed = await self.policy.filter_allowed_chat_ids(
            session,
            actor_id=actor_id,
            owner_id=owner_id,
            chat_ids=requested_chat_ids,
            permission=PermissionName.SEARCH_MESSAGES,
        )
        if not allowed:
            return (
                "Không tìm thấy đủ thông tin trong 7 ngày gần nhất "
                "từ dữ liệu Telegram đã được cấp quyền."
            )
        epochs = {
            chat_id: await require_authorization(session, chat_id, PermissionName.SEARCH_MESSAGES)
            for chat_id in allowed
        }

        async def fence(*, embedding=False):
            provider_fence = getattr(self, "provider_fence", None)
            if provider_fence:
                await provider_fence(
                    epochs,
                    PermissionName.SEARCH_MESSAGES,
                    cloud=(self.embedding_ai.provider in {"openai", "openrouter"})
                    if embedding
                    else any(provider in {"openai", "openrouter"} for provider in provider_plan),
                    store_id=getattr(getattr(self.vectors, "profile", None), "store_id", None)
                    if embedding
                    else None,
                )
                return
            database = getattr(self, "database", None)
            if database:
                async with database.session() as current:
                    await validate_answer(current, AuthorizedAnswer("", epochs))
            else:
                await validate_answer(session, AuthorizedAnswer("", epochs))

        policies = list(
            (
                await session.scalars(
                    select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id.in_(allowed))
                )
            ).all()
        )
        configured_presets = {
            policy.ai_efficiency_preset
            for policy in policies
            if policy.ai_efficiency_preset in PRESETS
        }
        preset = PRESETS[
            configured_presets.pop() if len(configured_presets) == 1 else self.default_preset
        ]
        configured_top_k = [policy.rag_top_k for policy in policies if policy.rag_top_k]
        effective_top_k = min(
            top_k,
            min(configured_top_k) if configured_top_k else preset.top_k,
        )
        query_route = classify_query(question, in_group=len(allowed) == 1)
        feature = route_feature(query_route)
        ai_route = await self.policy.ai_route(session, allowed)
        provider_plan = self.router.plan(ai_route) if self.router else [self.ai.provider]
        cloud_modes = {"inherit", "local_first", "cloud_only", "cloud_first"}
        modes_by_chat = {policy.chat_id: policy.ai_mode for policy in policies}
        if any(provider in {"openai", "openrouter"} for provider in provider_plan):
            # Filter before keyword/vector retrieval, context and cache lookup.
            # LOCAL ONLY remains excluded even when cloud is merely a fallback.
            allowed = [chat_id for chat_id in allowed if modes_by_chat.get(chat_id) in cloud_modes]
            policies = [policy for policy in policies if policy.chat_id in allowed]
            epochs = {chat_id: epoch for chat_id, epoch in epochs.items() if chat_id in allowed}
            if not allowed:
                return "Không có nguồn được phép gửi tới AI cloud."
        source_modes = [modes_by_chat[chat_id] for chat_id in allowed]
        planned_provider = provider_plan[0] if provider_plan else "off"
        planned_engine = (
            self.router.engines.get(planned_provider) if self.router else self.ai
        ) or self.ai
        knowledge_updated_at = await session.scalar(
            select(func.max(TelegramMessage.updated_at)).where(
                TelegramMessage.chat_id.in_(allowed),
                TelegramMessage.is_deleted.is_(False),
            )
        )
        knowledge_version = knowledge_updated_at.isoformat() if knowledge_updated_at else "empty"
        cache_key, normalized_query_hash = query_cache_key(
            question,
            chat_ids=allowed,
            sender_id=parsed.sender_id,
            route=query_route,
            provider=planned_provider,
            model=planned_engine.model,
            knowledge_version=knowledge_version,
            rag_config_version=(
                f"epochs:{sorted(epochs.items())}:"
                f"{preset.name}:{effective_top_k}:{preset.max_chunks_per_source}:"
                f"{preset.max_sources}:{preset.max_context_tokens}:{preset.max_chunk_tokens}"
            ),
        )
        now = datetime.now(UTC)
        cached = await session.scalar(
            select(AiQueryCache).where(
                AiQueryCache.cache_key == cache_key,
                AiQueryCache.expires_at > now,
            )
        )
        if cached:
            await fence()
            await session.commit()
            budget = getattr(planned_engine, "budget", None) or BudgetService(1, 10)
            await budget.admit_cache_hit(
                session,
                cache_key=cache_key,
                response=cached.response,
                cached_tokens=estimate_tokens(cached.response),
                provider=cached.provider,
                model=cached.model or planned_engine.model,
                feature=feature,
                route=query_route.value,
                chat_id=allowed[0] if len(allowed) == 1 else None,
                now=now,
            )
            await fence()
            return AuthorizedAnswer(cached.response, epochs)
        recent_since, asked_at = rag_time_window()
        effective_after = (
            parsed.after if parsed.after is not None or parsed.before is not None else recent_since
        )
        effective_before = parsed.before or asked_at
        effective_question = parsed.text or question
        keywords = await self.keyword.keyword(
            session,
            question,
            owner_id=owner_id,
            actor_id=actor_id,
            chat_ids=allowed,
            limit=effective_top_k,
            preauthorized=True,
            default_after=recent_since,
        )
        combined = {
            (result.chat_id, result.message_id): (
                result.score,
                result.text,
                result.sent_at,
            )
            for result in keywords
        }
        cloud_embedding = getattr(self.embedding_ai, "provider", "ollama") in {
            "openai",
            "openrouter",
        }
        embedding_allowed = bool(provider_plan) and (
            not cloud_embedding or all(mode in cloud_modes for mode in source_modes)
        )
        profile = getattr(self.vectors, "profile", None)
        if profile is not None:
            store = await session.get(VectorStore, profile.store_id)
            embedding_allowed = (
                embedding_allowed
                and store is not None
                and store.role == "semantic_active"
                and store.state == "active"
            )
        if self.vectors and self.embedding_ai.available and embedding_allowed:
            reference_filters = [
                TelegramMessage.chat_id.in_(allowed),
                TelegramMessage.sent_at <= effective_before,
                TelegramMessage.is_deleted.is_(False),
                TelegramMessage.text.is_not(None),
            ]
            if effective_after is not None:
                reference_filters.append(TelegramMessage.sent_at >= effective_after)
            if parsed.sender_id is not None:
                reference_filters.append(TelegramMessage.sender_id == parsed.sender_id)
            if parsed.has == "link":
                reference_filters.append(
                    or_(
                        TelegramMessage.text.contains("http://"),
                        TelegramMessage.text.contains("https://"),
                    )
                )
            elif parsed.has:
                reference_filters.append(TelegramMessage.has_media.is_(True))
            if parsed.content_type == "task":
                reference_filters.append(
                    or_(
                        TelegramMessage.text.contains("cần "),
                        TelegramMessage.text.contains("deadline"),
                        TelegramMessage.text.contains("todo"),
                    )
                )
            elif parsed.content_type == "decision":
                reference_filters.append(
                    or_(
                        TelegramMessage.text.contains("quyết định"),
                        TelegramMessage.text.contains("thống nhất"),
                    )
                )
            recent_reference_ids = list(
                (
                    await session.scalars(
                        select(TelegramMessage.id).where(and_(*reference_filters))
                    )
                ).all()
            )
            try:
                await fence()
                if isinstance(self.embedding_ai, AiEngine):
                    await session.commit()

                    async def embedding_fence():
                        await fence(embedding=True)

                    query_vector = await self.embedding_ai.embed(
                        session,
                        effective_question,
                        source_modes=source_modes,
                        pre_submit=embedding_fence,
                        chat_id=allowed[0] if len(allowed) == 1 else None,
                    )
                else:
                    query_vector = await self.embedding_ai.embed(session, effective_question)
            except AiPolicyError:
                raise
            except Exception:
                query_vector = None
            if query_vector:
                await fence()
                for _, score, payload in self.vectors.search(
                    query_vector,
                    allowed_chat_ids=allowed,
                    allowed_reference_ids=recent_reference_ids,
                    limit=effective_top_k,
                ):
                    row_filters = [
                        TelegramMessage.chat_id == payload["chat_id"],
                        TelegramMessage.message_id == payload["message_id"],
                        TelegramMessage.chat_id.in_(allowed),
                        TelegramMessage.sent_at <= effective_before,
                        TelegramMessage.is_deleted.is_(False),
                    ]
                    if effective_after is not None:
                        row_filters.append(TelegramMessage.sent_at >= effective_after)
                    row = await session.scalar(select(TelegramMessage).where(*row_filters))
                    if row:
                        combined[(row.chat_id, row.message_id)] = (
                            score,
                            str(redact(row.text or "")),
                            row.sent_at,
                        )
        ranked = sorted(combined.items(), key=lambda item: item[1][0], reverse=True)
        selected = []
        per_source: dict[int, int] = {}
        selected_sources: set[int] = set()
        for item in ranked:
            chat_id = item[0][0]
            if per_source.get(chat_id, 0) >= preset.max_chunks_per_source:
                continue
            if chat_id not in selected_sources and len(selected_sources) >= preset.max_sources:
                continue
            selected.append(item)
            selected_sources.add(chat_id)
            per_source[chat_id] = per_source.get(chat_id, 0) + 1
            if len(selected) >= effective_top_k:
                break
        if not selected:
            return (
                "Không tìm thấy đủ thông tin trong 7 ngày gần nhất "
                "từ dữ liệu Telegram đã được cấp quyền."
            )
        selected_chat_ids = {chat_id for (chat_id, _message_id), _value in selected}
        chats = (
            await session.scalars(
                select(TelegramChat).where(TelegramChat.chat_id.in_(selected_chat_ids))
            )
        ).all()
        chats_by_id = {chat.chat_id: chat for chat in chats}
        evidence_rows: list[SourceEvidence] = []
        for (chat_id, message_id), (score, text, sent_at) in selected:
            chat = chats_by_id.get(chat_id)
            evidence_rows.append(
                SourceEvidence(
                    chat_id=chat_id,
                    message_id=message_id,
                    score=score,
                    text=text[: preset.max_chunk_tokens * 4],
                    sent_at=sent_at,
                    title=_clean_title(chat.title if chat else None, chat_id),
                    url=telegram_message_url(chat, message_id) if chat else None,
                )
            )
        contexts: list[str] = []
        bounded_evidence: list[SourceEvidence] = []
        context_tokens = 0
        policy_context_limits = [
            policy.rag_max_context_tokens for policy in policies if policy.rag_max_context_tokens
        ]
        max_context_tokens = min([preset.max_context_tokens, *policy_context_limits])
        for evidence in evidence_rows:
            context = source_context(len(contexts) + 1, evidence)
            tokens = estimate_tokens(context)
            if contexts and context_tokens + tokens > max_context_tokens:
                break
            contexts.append(context)
            bounded_evidence.append(evidence)
            context_tokens += tokens
        evidence_rows = bounded_evidence
        await fence()
        if isinstance(self.ai, AiEngine):
            await session.commit()
        if self.router:
            answer = await self.router.answer(
                session,
                effective_question,
                contexts,
                route=ai_route,
                include_source_refs=include_citations,
                feature=feature,
                query_route=query_route.value,
                chat_id=allowed[0] if len(allowed) == 1 else None,
                source_modes=source_modes,
                pre_submit=fence,
            )
        elif isinstance(self.ai, AiEngine):
            answer = await self.ai.answer(
                session,
                effective_question,
                contexts,
                include_source_refs=include_citations,
                source_modes=source_modes,
                pre_submit=fence,
            )
        elif include_citations:
            answer = await self.ai.answer(session, effective_question, contexts)
        else:
            answer = await self.ai.answer(
                session,
                effective_question,
                contexts,
                include_source_refs=False,
            )
        await fence()
        if include_citations:
            indexes = referenced_evidence_indexes(answer, item_count=len(evidence_rows))
            cited_rows = [evidence_rows[index] for index in indexes]
            answer = renumber_source_references(answer, indexes)
            answer = f"{answer.rstrip()}\n\n{citation_appendix(cited_rows)}"
        else:
            answer = without_source_references(answer)
        session.add(
            AiQueryCache(
                cache_key=cache_key,
                normalized_query_hash=normalized_query_hash,
                scope_chat_id=allowed[0] if len(allowed) == 1 else None,
                knowledge_version=knowledge_version,
                route=query_route.value,
                feature=feature,
                provider=planned_provider,
                model=planned_engine.model,
                response=answer,
                citations=[
                    {
                        "chat_id": evidence.chat_id,
                        "message_id": evidence.message_id,
                        "url": evidence.url,
                        "title": evidence.title,
                        "sent_at": (evidence.sent_at.isoformat() if evidence.sent_at else None),
                    }
                    for evidence in evidence_rows
                ],
                expires_at=now + query_cache_ttl(query_route),
            )
        )
        await fence()
        return AuthorizedAnswer(answer, epochs)
