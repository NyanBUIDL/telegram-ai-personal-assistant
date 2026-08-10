from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AiUsage

MODEL_PRICES: dict[str, tuple[float, float]] = {
    "gpt-5.6-sol": (5.0, 30.0),
    "gpt-5.6": (5.0, 30.0),
    "gpt-5.6-terra": (2.5, 15.0),
    "gpt-5.6-luna": (1.0, 6.0),
    "text-embedding-3-small": (0.02, 0.0),
    "openai/gpt-5.6-terra": (2.5, 15.0),
    "openai/text-embedding-3-small": (0.02, 0.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Ước tính USD và fail-closed nếu model chưa có bảng giá."""
    if model not in MODEL_PRICES:
        raise ValueError(
            f"Chưa có bảng giá cho model {model}; không gọi AI để tránh vượt ngân sách."
        )
    input_rate, output_rate = MODEL_PRICES[model]
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


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
    ) -> None:
        self.daily_limit, self.monthly_limit = daily_limit, monthly_limit
        self.daily_token_limit = daily_token_limit
        self.monthly_token_limit = monthly_token_limit
        self.group_daily_token_limit = group_daily_token_limit
        self.feature_daily_token_limit = feature_daily_token_limit
        self.provider_daily_token_limit = provider_daily_token_limit
        self.max_cloud_fallbacks_per_day = max_cloud_fallbacks_per_day

    @staticmethod
    def _token_total():
        return AiUsage.input_tokens + AiUsage.output_tokens

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

        daily_used = await used_since(day)
        monthly_used = await used_since(month)
        if daily_used + projected_tokens > self.daily_token_limit:
            raise RuntimeError("Request vượt hạn mức token AI trong ngày.")
        if monthly_used + projected_tokens > self.monthly_token_limit:
            raise RuntimeError("Request vượt hạn mức token AI trong tháng.")
        if chat_id is not None:
            group_used = await used_since(day, AiUsage.chat_id == chat_id)
            if group_used + projected_tokens > self.group_daily_token_limit:
                raise RuntimeError("Group đã đạt hạn mức token AI trong ngày.")
        if feature:
            feature_used = await used_since(day, AiUsage.feature == feature)
            if feature_used + projected_tokens > self.feature_daily_token_limit:
                raise RuntimeError("Tính năng đã đạt hạn mức token AI trong ngày.")
        if provider:
            provider_used = await used_since(day, AiUsage.provider == provider)
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
            float(daily or 0), float(monthly or 0), self.daily_limit, self.monthly_limit
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
