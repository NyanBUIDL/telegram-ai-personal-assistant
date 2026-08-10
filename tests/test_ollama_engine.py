from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_assistant.ai.engine import AiEngine


class LocalBudget:
    def __init__(self) -> None:
        self.records: list[dict] = []

    async def ensure_request_within_budget(self, *_args, **_kwargs) -> None:
        raise AssertionError("Ollama local không được chặn bởi ngân sách API cloud")

    async def ensure_token_limits(self, *_args, **_kwargs) -> None:
        return None

    async def record(self, _session, **kwargs) -> None:
        self.records.append(kwargs)


def make_engine(budget: LocalBudget) -> AiEngine:
    return AiEngine(
        api_key=None,
        budget=budget,
        provider="ollama",
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3:8b",
        embedding_model="nomic-embed-text:latest",
        max_output_tokens=300,
    )


def test_ollama_engine_is_available_without_api_key() -> None:
    engine = make_engine(LocalBudget())

    assert engine.available
    assert engine.is_local


@pytest.mark.asyncio
async def test_ollama_answer_costs_zero_and_omits_store() -> None:
    budget = LocalBudget()
    engine = make_engine(budget)
    engine.client.responses.create = AsyncMock(
        return_value=SimpleNamespace(
            output_text="Câu trả lời local",
            usage=SimpleNamespace(input_tokens=20, output_tokens=5),
        )
    )

    answer = await engine.answer(object(), "Câu hỏi", ["[S1] Dữ liệu"])

    assert answer == "Câu trả lời local"
    kwargs = engine.client.responses.create.await_args.kwargs
    assert "store" not in kwargs
    assert budget.records[0]["cost"] == 0.0


@pytest.mark.asyncio
async def test_ollama_embeddings_cost_zero() -> None:
    budget = LocalBudget()
    engine = make_engine(budget)
    engine.client.embeddings.create = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])],
            usage=SimpleNamespace(total_tokens=3),
        )
    )

    vectors = await engine.embed_many(object(), ["xin chào"])

    assert vectors == [[0.1, 0.2, 0.3]]
    assert budget.records[0]["cost"] == 0.0
