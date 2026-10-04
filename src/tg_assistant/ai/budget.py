from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.models import AiQueryCache, AiUsage

PRICING_VERSION = "official-2026-10-03-v1"


@dataclass(frozen=True, slots=True)
class PricingSnapshot:
    provider: str
    model: str
    version: str
    source: str
    input_rate: Decimal
    output_rate: Decimal
    cached_rate: Decimal
    cache_write_rate: Decimal
    expires_at: datetime
    long_context: bool = False

    def cost(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        cached_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> Decimal:
        if (
            min(input_tokens, output_tokens, cached_tokens, cache_write_tokens) < 0
            or cached_tokens + cache_write_tokens > input_tokens
        ):
            raise ValueError("Invalid token accounting")
        long = self.long_context and input_tokens > 272_000
        input_factor = Decimal(2) if long else Decimal(1)
        output_factor = Decimal("1.5") if long else Decimal(1)
        return (
            (input_tokens - cached_tokens - cache_write_tokens) * self.input_rate * input_factor
            + cached_tokens * self.cached_rate * input_factor
            + cache_write_tokens * self.cache_write_rate * input_factor
            + output_tokens * self.output_rate * output_factor
        ) / Decimal(1_000_000)

    def reserve_cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        # Reserve the expensive cache-write case; do not assume a cache hit.
        return self.cost(input_tokens, output_tokens, cache_write_tokens=input_tokens)

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "version": self.version,
            "source": self.source,
            "input_rate": str(self.input_rate),
            "output_rate": str(self.output_rate),
            "cached_rate": str(self.cached_rate),
            "cache_write_rate": str(self.cache_write_rate),
            "expires_at": self.expires_at.isoformat(),
            "long_context": self.long_context,
        }

    @classmethod
    def from_dict(cls, value: dict):
        return cls(
            **{
                **value,
                **{
                    name: Decimal(value[name])
                    for name in ("input_rate", "output_rate", "cached_rate", "cache_write_rate")
                },
                "expires_at": datetime.fromisoformat(value["expires_at"]),
            }
        )


def pricing_for(provider: str, model: str, *, now: datetime | None = None) -> PricingSnapshot:
    # Independent official snapshots; no cross-provider inference or live billing lookup.
    rates = {
        ("openai", "gpt-5.6-terra"): ("2", "12", "0.2", "2.5", True),
        ("openai", "gpt-5.6-luna"): ("0.2", "1.2", "0.02", "0.25", True),
        ("openai", "gpt-5.6-sol"): ("4", "20", "0.4", "5", True),
        ("openai", "gpt-5.6"): ("4", "20", "0.4", "5", True),
        ("openai", "text-embedding-3-small"): ("0.02", "0", "0.02", "0.02", False),
        ("openai", "text-embedding-3-large"): ("0.13", "0", "0.13", "0.13", False),
        ("openrouter", "openai/gpt-5.6-terra"): ("2", "12", "0.2", "2.5", True),
        ("openrouter", "openai/text-embedding-3-small"): ("0.02", "0", "0.02", "0.02", False),
        ("openrouter", "openai/text-embedding-3-large"): ("0.13", "0", "0.13", "0.13", False),
    }
    rate = rates.get((provider, model))
    expiry = datetime(2026, 11, 22, tzinfo=UTC)
    if rate is None or (now or datetime.now(UTC)) >= expiry:
        raise ValueError("No current verified pricing for the selected provider/model")
    source = (
        f"https://developers.openai.com/api/docs/models/{model}"
        if provider == "openai"
        else "https://openrouter.ai/api/v1/embeddings/models"
        if "embedding" in model
        else "https://openrouter.ai/api/v1/models"
    )
    return PricingSnapshot(
        provider,
        model,
        PRICING_VERSION,
        source,
        *(Decimal(item) for item in rate[:4]),
        expiry,
        rate[4],
    )


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Ước tính USD và fail-closed nếu model chưa có bảng giá."""
    provider = "openrouter" if model.startswith("openai/") else "openai"
    return float(pricing_for(provider, model).cost(input_tokens, output_tokens))


@dataclass(slots=True)
class BudgetState:
    daily_spend: float
    monthly_spend: float
    daily_limit: float
    monthly_limit: float

    @property
    def exhausted(self) -> bool:
        return self.daily_spend >= self.daily_limit or self.monthly_spend >= self.monthly_limit

    @property
    def warning(self) -> bool:
        return (
            self.daily_spend >= self.daily_limit * 0.8
            or self.monthly_spend >= self.monthly_limit * 0.8
        )


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    request_id: str
    state: str
    reserved_cost_usd: Decimal


def reservation_model():
    from ..db.models import AiBudgetReservation

    return AiBudgetReservation


class BudgetService:
    def __init__(
        self,
        daily_limit: float,
        monthly_limit: float,
        *,
        daily_token_limit: int = 1_000_000,
        monthly_token_limit: int = 20_000_000,
        group_daily_token_limit: int = 150_000,
        feature_daily_token_limit: int = 500_000,
        provider_daily_token_limit: int = 1_000_000,
        max_cloud_fallbacks_per_day: int = 100,
        profile_id: str = "default",
    ) -> None:
        self.daily_limit, self.monthly_limit = daily_limit, monthly_limit
        self.daily_token_limit = daily_token_limit
        self.monthly_token_limit = monthly_token_limit
        self.group_daily_token_limit = group_daily_token_limit
        self.feature_daily_token_limit = feature_daily_token_limit
        self.provider_daily_token_limit = provider_daily_token_limit
        self.max_cloud_fallbacks_per_day = max_cloud_fallbacks_per_day
        self.profile_id = profile_id

    async def _lock(self, session):
        from ..db.models import AiBudgetLock

        if session.bind.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert

            statement = (
                insert(AiBudgetLock)
                .values(profile_id=self.profile_id, version=0)
                .on_conflict_do_nothing()
            )
        elif session.bind.dialect.name == "mysql":
            from sqlalchemy.dialects.mysql import insert

            statement = insert(AiBudgetLock).values(profile_id=self.profile_id, version=0)
            statement = statement.on_duplicate_key_update(profile_id=statement.inserted.profile_id)
        else:
            raise RuntimeError("Unsupported budget transaction backend")
        await session.execute(statement)
        await session.execute(
            update(AiBudgetLock)
            .where(AiBudgetLock.profile_id == self.profile_id)
            .values(version=AiBudgetLock.version + 1)
        )

    def _sessions(self, session):
        return async_sessionmaker(session.bind, expire_on_commit=False)

    async def reserve(
        self,
        session,
        *,
        request_id: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        operation: str,
        feature: str,
        route: str | None = None,
        chat_id: int | None = None,
        fallback_used: bool = False,
        is_local: bool = False,
        now: datetime | None = None,
    ) -> BudgetReservation:
        if not request_id or len(request_id) > 64 or min(input_tokens, output_tokens) < 0:
            raise ValueError("Invalid budget request metadata")
        if is_local != (provider == "ollama"):
            raise ValueError("Invalid local budget provider")
        now = now or datetime.now(UTC)
        pricing = (
            PricingSnapshot(
                provider,
                model,
                "local-zero-v1",
                "local",
                *(Decimal(0) for _ in range(4)),
                datetime(9999, 1, 1, tzinfo=UTC),
            )
            if is_local
            else pricing_for(provider, model, now=now)
        )
        cost = pricing.reserve_cost(input_tokens, output_tokens)
        metadata = {
            "profile_id": self.profile_id,
            "provider": provider,
            "model": model,
            "operation": operation,
            "feature": feature,
            "route": route,
            "chat_id": chat_id,
            "reserved_input_tokens": input_tokens,
            "reserved_output_tokens": output_tokens,
            "is_local": is_local,
            "fallback_used": fallback_used,
        }
        Reservation = reservation_model()
        async with self._sessions(session)() as current, current.begin():
            await self._lock(current)
            existing = await current.get(Reservation, request_id)
            if existing:
                if any(getattr(existing, key) != value for key, value in metadata.items()):
                    raise RuntimeError("Budget request id reused with different metadata")
                if existing.state != "released":
                    return BudgetReservation(
                        request_id, existing.state, Decimal(existing.reserved_cost_usd)
                    )
            await self.ensure_token_limits(
                current,
                projected_tokens=input_tokens + output_tokens,
                provider=provider,
                feature=feature,
                chat_id=chat_id,
                fallback_used=fallback_used,
                now=now,
            )
            state = await self.state(current, now=now)
            if not is_local and (
                Decimal(str(state.daily_spend)) + cost > Decimal(str(self.daily_limit))
                or Decimal(str(state.monthly_spend)) + cost > Decimal(str(self.monthly_limit))
            ):
                raise RuntimeError("Request exceeds remaining AI budget")
            if existing:
                # A release is recorded only before submission. Re-admit that
                # same intent under today's caps; submitted/uncertain never rearm.
                existing.occurred_at = now
                existing.pricing_version, existing.pricing_rates = (
                    pricing.version,
                    pricing.as_dict(),
                )
                existing.reserved_cost_usd, existing.state = cost, "reserved"
                existing.settled_at = None
                return BudgetReservation(request_id, "reserved", cost)
            current.add(
                Reservation(
                    request_id=request_id,
                    occurred_at=now,
                    pricing_version=pricing.version,
                    pricing_rates=pricing.as_dict(),
                    reserved_cost_usd=cost,
                    state="reserved",
                    **metadata,
                )
            )
            return BudgetReservation(request_id, "reserved", cost)

    async def _reservation(self, session, request_id):
        await self._lock(session)
        row = await session.get(reservation_model(), request_id)
        if row is None or row.profile_id != self.profile_id:
            raise RuntimeError("Budget reservation not owned by this profile")
        return row

    async def admit_cache_hit(
        self,
        session,
        *,
        cache_key: str,
        response: str,
        cached_tokens: int,
        provider: str | None,
        model: str,
        feature: str,
        route: str,
        chat_id: int | None,
        now: datetime | None = None,
    ) -> None:
        """Atomically admit/account local delivery without pricing a provider call."""
        if type(cached_tokens) is not int or cached_tokens < 0:
            raise ValueError("Invalid cache token metadata")
        now = now or datetime.now(UTC)
        request_id = uuid4().hex
        pricing = PricingSnapshot(
            provider or "cache",
            model,
            "cache-zero-v1",
            "local-cache",
            *(Decimal(0) for _ in range(4)),
            datetime(9999, 1, 1, tzinfo=UTC),
        )
        async with self._sessions(session)() as current, current.begin():
            await self._lock(current)
            cached = await current.scalar(
                select(AiQueryCache).where(
                    AiQueryCache.cache_key == cache_key,
                    AiQueryCache.expires_at > now,
                )
            )
            if cached is None or cached.response != response:
                raise RuntimeError("cache_entry_unavailable")
            await self.ensure_token_limits(
                current,
                projected_tokens=cached_tokens,
                provider=provider,
                feature=feature,
                chat_id=chat_id,
                now=now,
            )
            current.add(
                reservation_model()(
                    request_id=request_id,
                    profile_id=self.profile_id,
                    occurred_at=now,
                    provider=provider or "cache",
                    model=model,
                    pricing_version=pricing.version,
                    pricing_rates=pricing.as_dict(),
                    operation="cache_hit",
                    feature=feature,
                    route=route,
                    chat_id=chat_id,
                    reserved_input_tokens=cached_tokens,
                    reserved_output_tokens=0,
                    reserved_cost_usd=Decimal(0),
                    is_local=provider == "ollama",
                    fallback_used=False,
                    state="settled",
                    settled_at=now,
                    actual_input_tokens=cached_tokens,
                    actual_output_tokens=0,
                    cached_tokens=cached_tokens,
                    cache_write_tokens=0,
                    actual_cost_usd=Decimal(0),
                )
            )
            await current.flush()
            current.add(
                AiUsage(
                    reservation_id=request_id,
                    pricing_version=pricing.version,
                    occurred_at=now,
                    provider=provider,
                    model=model,
                    operation="cache_hit",
                    feature=feature,
                    route=route,
                    chat_id=chat_id,
                    input_tokens=0,
                    output_tokens=0,
                    cached_tokens=cached_tokens,
                    estimated_cost_usd=0,
                    success=True,
                    embedding_tokens=0,
                    cache_hit=True,
                    is_local=provider == "ollama",
                    fallback_used=False,
                )
            )
            # The budget lock serializes concurrent hits, including the count.
            cached.hit_count += 1
            cached.last_hit_at = now

    async def mark_submitted(self, session, request_id: str) -> bool:
        async with self._sessions(session)() as current, current.begin():
            row = await self._reservation(current, request_id)
            if row.state != "reserved":
                return False
            row.state, row.submitted_at = "submitted", datetime.now(UTC)
            return True

    async def mark_uncertain(self, session, request_id: str):
        async with self._sessions(session)() as current, current.begin():
            row = await self._reservation(current, request_id)
            if row.state == "submitted":
                row.state, row.last_error_code = "uncertain", "provider_outcome_unknown"

    async def release(self, session, request_id: str):
        async with self._sessions(session)() as current, current.begin():
            row = await self._reservation(current, request_id)
            if row.state == "released":
                return
            if row.state != "reserved":
                raise RuntimeError("Submitted reservation requires verified reconciliation")
            row.state, row.settled_at = "released", datetime.now(UTC)

    async def reconcile(
        self,
        session,
        request_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        cache_write_tokens: int = 0,
    ):
        actual = {
            "actual_input_tokens": input_tokens,
            "actual_output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "cache_write_tokens": cache_write_tokens,
        }
        async with self._sessions(session)() as current, current.begin():
            row = await self._reservation(current, request_id)
            pricing = PricingSnapshot.from_dict(row.pricing_rates)
            cost = pricing.cost(
                input_tokens,
                output_tokens,
                cached_tokens=cached_tokens,
                cache_write_tokens=cache_write_tokens,
            )
            if row.state == "settled":
                if any(getattr(row, key) != value for key, value in actual.items()):
                    raise RuntimeError("Conflicting budget reconciliation")
                return
            if row.state not in {"submitted", "uncertain"}:
                raise RuntimeError("Budget request was not submitted")
            for name, value in actual.items():
                setattr(row, name, value)
            row.actual_cost_usd, row.state, row.settled_at = cost, "settled", datetime.now(UTC)
            current.add(
                AiUsage(
                    reservation_id=request_id,
                    pricing_version=row.pricing_version,
                    occurred_at=row.occurred_at,
                    model=row.model,
                    operation=row.operation,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    estimated_cost_usd=float(cost),
                    provider=row.provider,
                    feature=row.feature,
                    route=row.route,
                    chat_id=row.chat_id,
                    cached_tokens=cached_tokens,
                    cache_write_tokens=cache_write_tokens,
                    embedding_tokens=input_tokens if row.operation == "embedding" else 0,
                    is_local=row.is_local,
                    fallback_used=row.fallback_used,
                    success=True,
                )
            )

    @staticmethod
    def _token_total():
        return (
            AiUsage.input_tokens
            + AiUsage.output_tokens
            + case((AiUsage.cache_hit.is_(True), AiUsage.cached_tokens), else_=0)
        )

    async def _pending(
        self,
        session,
        since,
        *,
        tokens=False,
        provider=None,
        feature=None,
        chat_id=None,
        fallback=False,
    ):
        Reservation = reservation_model()
        conditions = [
            Reservation.profile_id == self.profile_id,
            Reservation.state.in_(["reserved", "submitted", "uncertain"]),
            Reservation.occurred_at >= since,
        ]
        for field, value in (("provider", provider), ("feature", feature), ("chat_id", chat_id)):
            if value is not None:
                conditions.append(getattr(Reservation, field) == value)
        if fallback:
            conditions.extend(
                [Reservation.fallback_used.is_(True), Reservation.is_local.is_(False)]
            )
        value = (
            func.count(Reservation.request_id)
            if fallback
            else func.sum(Reservation.reserved_input_tokens + Reservation.reserved_output_tokens)
            if tokens
            else func.sum(Reservation.reserved_cost_usd)
        )
        return (await session.scalar(select(func.coalesce(value, 0)).where(*conditions))) or 0

    async def ensure_token_limits(
        self,
        session: AsyncSession,
        *,
        projected_tokens: int,
        provider: str | None,
        feature: str | None,
        chat_id: int | None,
        fallback_used: bool = False,
        now: datetime | None = None,
    ) -> None:
        if projected_tokens <= 0:
            return
        now = now or datetime.now(UTC)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = day.replace(day=1)

        async def used_since(since: datetime, *conditions) -> int:
            return int(
                (
                    await session.scalar(
                        select(func.coalesce(func.sum(self._token_total()), 0)).where(
                            AiUsage.occurred_at >= since,
                            *conditions,
                        )
                    )
                )
                or 0
            )

        daily_used = await used_since(day) + await self._pending(session, day, tokens=True)
        monthly_used = await used_since(month) + await self._pending(session, month, tokens=True)
        if daily_used + projected_tokens > self.daily_token_limit:
            raise RuntimeError("Request vượt hạn mức token AI trong ngày.")
        if monthly_used + projected_tokens > self.monthly_token_limit:
            raise RuntimeError("Request vượt hạn mức token AI trong tháng.")
        if chat_id is not None:
            group_used = await used_since(day, AiUsage.chat_id == chat_id) + await self._pending(
                session, day, tokens=True, chat_id=chat_id
            )
            if group_used + projected_tokens > self.group_daily_token_limit:
                raise RuntimeError("Group đã đạt hạn mức token AI trong ngày.")
        if feature:
            feature_used = await used_since(day, AiUsage.feature == feature) + await self._pending(
                session, day, tokens=True, feature=feature
            )
            if feature_used + projected_tokens > self.feature_daily_token_limit:
                raise RuntimeError("Tính năng đã đạt hạn mức token AI trong ngày.")
        if provider:
            provider_used = await used_since(
                day, AiUsage.provider == provider
            ) + await self._pending(session, day, tokens=True, provider=provider)
            if provider_used + projected_tokens > self.provider_daily_token_limit:
                raise RuntimeError("Provider đã đạt hạn mức token AI trong ngày.")
        if fallback_used and provider not in {None, "ollama"}:
            fallback_count = int(
                (
                    await session.scalar(
                        select(func.count(AiUsage.id)).where(
                            AiUsage.occurred_at >= day,
                            AiUsage.fallback_used.is_(True),
                            AiUsage.is_local.is_(False),
                        )
                    )
                )
                or 0
            )
            fallback_count += await self._pending(session, day, fallback=True)
            if fallback_count >= self.max_cloud_fallbacks_per_day:
                raise RuntimeError("Đã đạt hạn mức cloud fallback trong ngày.")

    async def state(self, session: AsyncSession, now: datetime | None = None) -> BudgetState:
        now = now or datetime.now(UTC)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = day.replace(day=1)
        daily = await session.scalar(
            select(func.coalesce(func.sum(AiUsage.estimated_cost_usd), 0.0)).where(
                AiUsage.occurred_at >= day
            )
        )
        monthly = await session.scalar(
            select(func.coalesce(func.sum(AiUsage.estimated_cost_usd), 0.0)).where(
                AiUsage.occurred_at >= month
            )
        )
        return BudgetState(
            float(daily or 0) + float(await self._pending(session, day)),
            float(monthly or 0) + float(await self._pending(session, month)),
            self.daily_limit,
            self.monthly_limit,
        )

    async def ensure_available(self, session: AsyncSession) -> BudgetState:
        state = await self.state(session)
        if state.exhausted:
            raise RuntimeError("Đã đạt 100% ngân sách AI; bot vẫn hoạt động ở chế độ local.")
        return state

    async def ensure_request_within_budget(
        self,
        session: AsyncSession,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        provider: str | None = None,
        feature: str | None = None,
        chat_id: int | None = None,
        fallback_used: bool = False,
    ) -> BudgetState:
        state = await self.ensure_available(session)
        await self.ensure_token_limits(
            session,
            projected_tokens=input_tokens + output_tokens,
            provider=provider,
            feature=feature,
            chat_id=chat_id,
            fallback_used=fallback_used,
        )
        projected = estimate_cost(model, input_tokens, output_tokens)
        if (
            state.daily_spend + projected > state.daily_limit
            or state.monthly_spend + projected > state.monthly_limit
        ):
            raise RuntimeError(
                "Request AI dự kiến vượt ngân sách còn lại; bot vẫn chạy chế độ local."
            )
        return state

    async def record(
        self,
        session: AsyncSession,
        *,
        model: str,
        operation: str,
        input_tokens: int,
        output_tokens: int,
        cost: float | None = None,
        success: bool = True,
        provider: str | None = None,
        feature: str | None = None,
        route: str | None = None,
        chat_id: int | None = None,
        cached_tokens: int = 0,
        embedding_tokens: int = 0,
        latency_ms: float | None = None,
        cache_hit: bool = False,
        is_local: bool = False,
        fallback_used: bool = False,
        error_code: str | None = None,
    ) -> None:
        if cost is None:
            cost = estimate_cost(model, input_tokens, output_tokens)
        session.add(
            AiUsage(
                occurred_at=datetime.now(UTC),
                model=model,
                operation=operation,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=cost,
                success=success,
                provider=provider,
                feature=feature or operation,
                route=route,
                chat_id=chat_id,
                cached_tokens=cached_tokens,
                embedding_tokens=embedding_tokens,
                latency_ms=latency_ms,
                cache_hit=cache_hit,
                is_local=is_local,
                fallback_used=fallback_used,
                error_code=error_code,
            )
        )
