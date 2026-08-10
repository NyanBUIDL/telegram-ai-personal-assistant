from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from .engine import AiEngine

log = structlog.get_logger(__name__)

AI_MODES = {"inherit", "local_only", "local_first", "cloud_only", "cloud_first", "off"}
CLOUD_PROVIDERS = {"openai", "openrouter"}


@dataclass(frozen=True, slots=True)
class AiRoute:
    mode: str = "inherit"
    preferred_cloud_provider: str | None = None
    cloud_fallback: bool = False


class AiRouter:
    """Route answer generation while keeping embeddings on the active corpus engine."""

    def __init__(self, engines: dict[str, AiEngine], *, default_provider: str) -> None:
        self.engines = engines
        self.default_provider = default_provider
        self.enabled = default_provider != "off"

    def _cloud_provider(self, preferred: str | None) -> str | None:
        if preferred in CLOUD_PROVIDERS and preferred in self.engines:
            return preferred
        if self.default_provider in CLOUD_PROVIDERS and self.default_provider in self.engines:
            return self.default_provider
        return next((name for name in ("openai", "openrouter") if name in self.engines), None)

    def plan(self, route: AiRoute) -> list[str]:
        if not self.enabled:
            return []
        mode = route.mode if route.mode in AI_MODES else "inherit"
        cloud = self._cloud_provider(route.preferred_cloud_provider)
        local = "ollama" if "ollama" in self.engines else None
        if mode == "off":
            return []
        if mode == "local_only":
            return [local] if local else []
        if mode == "cloud_only":
            return [cloud] if cloud else []
        if mode == "local_first":
            return [
                provider
                for provider in (local, cloud if route.cloud_fallback else None)
                if provider
            ]
        if mode == "cloud_first":
            return [
                provider
                for provider in (cloud, local if route.cloud_fallback else None)
                if provider
            ]
        return [self.default_provider] if self.default_provider in self.engines else []

    async def answer(
        self,
        session: AsyncSession,
        question: str,
        context: list[str],
        *,
        route: AiRoute,
        include_source_refs: bool = True,
        feature: str = "normal_ask",
        query_route: str = "local_rag",
        chat_id: int | None = None,
    ) -> str:
        plan = self.plan(route)
        if not plan:
            return "AI đang tắt hoặc mode đã chọn chưa có model/API khả dụng."
        last_error: Exception | None = None
        for index, provider in enumerate(plan):
            engine = self.engines[provider]
            try:
                answer = await engine.answer(
                    session,
                    question,
                    context,
                    include_source_refs=include_source_refs,
                    feature=feature,
                    route=query_route,
                    chat_id=chat_id,
                    fallback_used=index > 0,
                )
                log.info(
                    "ai_route_succeeded",
                    provider=provider,
                    mode=route.mode,
                    fallback_used=index > 0,
                )
                return answer
            except Exception as exc:
                last_error = exc
                log.warning(
                    "ai_route_failed",
                    provider=provider,
                    mode=route.mode,
                    has_fallback=index + 1 < len(plan),
                    error=str(exc),
                )
        raise RuntimeError(f"Tất cả AI provider trong route đều lỗi: {last_error}") from last_error

    async def close(self) -> None:
        seen: set[int] = set()
        for engine in self.engines.values():
            if id(engine) not in seen:
                seen.add(id(engine))
                await engine.close()
