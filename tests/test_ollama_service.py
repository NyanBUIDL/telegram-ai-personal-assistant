from __future__ import annotations

import httpx
import pytest

from tg_assistant.services.ollama import (
    OllamaError,
    OllamaService,
    format_model_size,
    validate_model_name,
)


def test_validate_ollama_model_name() -> None:
    assert validate_model_name("qwen3:8b") == "qwen3:8b"
    assert validate_model_name("hf.co/user/model:Q4_K_M") == "hf.co/user/model:Q4_K_M"
    with pytest.raises(OllamaError):
        validate_model_name("../bad model")


def test_format_model_size() -> None:
    assert format_model_size(5 * 1024**3) == "5.0 GB"
    assert format_model_size(0) == "cloud/không rõ"


@pytest.mark.asyncio
async def test_ollama_model_management_and_embedding_probe() -> None:
    requests: list[tuple[str, str]] = []
    pull_updates: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen3:8b",
                            "size": 5_000_000_000,
                            "modified_at": "2026-07-27T01:02:03Z",
                            "details": {
                                "family": "qwen3",
                                "parameter_size": "8B",
                                "quantization_level": "Q4_K_M",
                            },
                        }
                    ]
                },
            )
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1] * 768]})
        return httpx.Response(200, json={"status": "success"})

    service = OllamaService(
        "http://127.0.0.1:11434/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        models = await service.list_models()
        dimension = await service.embedding_dimension("nomic-embed-text:latest")

        async def record_pull(update: dict) -> None:
            pull_updates.append(update)

        await service.pull_model("gemma3:12b", on_progress=record_pull)
        await service.delete_model("gemma3:12b")
    finally:
        await service.close()

    assert models[0].name == "qwen3:8b"
    assert models[0].parameter_size == "8B"
    assert dimension == 768
    assert pull_updates[-1]["progress"] == 100
    assert requests == [
        ("GET", "/api/tags"),
        ("POST", "/api/embed"),
        ("POST", "/api/pull"),
        ("DELETE", "/api/delete"),
    ]
