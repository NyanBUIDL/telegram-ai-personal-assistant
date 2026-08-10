from __future__ import annotations

import pytest

from tg_assistant.ai.router import AiRoute, AiRouter


class FakeEngine:
    def __init__(self, provider: str, *, fail: bool = False) -> None:
        self.provider = provider
        self.fail = fail
        self.calls = 0

    async def answer(self, session, question, context, *, include_source_refs=True, **_kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"{self.provider} unavailable")
        return f"answer from {self.provider}"

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_local_first_falls_back_to_cloud() -> None:
    local = FakeEngine("ollama", fail=True)
    cloud = FakeEngine("openai")
    router = AiRouter(
        {"ollama": local, "openai": cloud},
        default_provider="openai",
    )

    answer = await router.answer(
        None,
        "question",
        ["context"],
        route=AiRoute(
            mode="local_first",
            preferred_cloud_provider="openai",
            cloud_fallback=True,
        ),
    )

    assert answer == "answer from openai"
    assert local.calls == 1
    assert cloud.calls == 1


def test_local_only_never_routes_to_cloud() -> None:
    router = AiRouter(
        {"ollama": FakeEngine("ollama"), "openai": FakeEngine("openai")},
        default_provider="openai",
    )

    assert router.plan(AiRoute(mode="local_only", cloud_fallback=True)) == ["ollama"]


def test_global_off_disables_all_group_routes() -> None:
    router = AiRouter(
        {"ollama": FakeEngine("ollama"), "openai": FakeEngine("openai")},
        default_provider="off",
    )

    assert router.plan(AiRoute(mode="local_only")) == []
