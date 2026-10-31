from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_assistant.ai.engine import AiEngine


def classifier_engine():
    budget = SimpleNamespace(
        ensure_request_within_budget=AsyncMock(), record=AsyncMock()
    )
    engine = AiEngine(
        api_key="test-key", budget=budget, provider="openai",
        base_url="https://api.openai.com/v1", model="gpt-5.6-terra",
        embedding_model="text-embedding-3-small", max_output_tokens=300,
    )
    engine.client.responses.create = AsyncMock(
        return_value=SimpleNamespace(output_text='{"results":[]}', usage=None)
    )
    return engine


@pytest.mark.asyncio
async def test_classifier_rejects_secret_owner_criteria_before_cloud_call():
    engine = classifier_engine()
    try:
        with pytest.raises(RuntimeError, match="secret"):
            await engine.classify_history_delete_candidates(
                object(), instruction="Find " + "123456789:" + "a" * 35,
                posts=[{"message_id": 1, "text": "ordinary post"}], chat_id=100,
            )
        engine.client.responses.create.assert_not_awaited()
        engine.budget.ensure_request_within_budget.assert_not_awaited()
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", "ordinary " * 109 + " "])
async def test_classifier_redacts_source_before_truncation_without_mutating_posts(prefix):
    engine = classifier_engine()
    secret = "123456789:" + "a" * 35
    original_text = prefix + secret + " trailing context"
    posts = [{"message_id": 1, "text": original_text}]
    try:
        await engine.classify_history_delete_candidates(
            object(), instruction="Find advertisements", posts=posts, chat_id=100,
        )
        payload = json.loads(engine.client.responses.create.await_args.kwargs["input"])
        assert payload["posts"][0]["text"] == (prefix + "[REDACTED] trailing context")[:1000]
        assert "123456789:" not in payload["posts"][0]["text"]
        assert posts == [{"message_id": 1, "text": original_text}]
        assert engine.client.responses.create.await_args.kwargs["store"] is False
    finally:
        await engine.close()
