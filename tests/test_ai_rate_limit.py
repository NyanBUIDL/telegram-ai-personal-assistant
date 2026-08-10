from __future__ import annotations

import asyncio
from collections import deque

import pytest

from tg_assistant.ai import engine as engine_module
from tg_assistant.ai.engine import AiEngine


@pytest.mark.asyncio
async def test_background_embedding_waits_for_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = object.__new__(AiEngine)
    engine.max_requests_per_minute = 1
    engine._requests = deque([0.0])
    engine._request_lock = asyncio.Lock()
    times = iter([59.0, 60.1])
    delays: list[float] = []

    monkeypatch.setattr(engine_module, "monotonic", lambda: next(times))

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(engine_module.asyncio, "sleep", fake_sleep)

    await engine._admit_request(wait=True)

    assert delays == [1.0]
    assert list(engine._requests) == [60.1]
