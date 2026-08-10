from __future__ import annotations

import pytest
from pydantic import ValidationError

from tg_assistant.config import Settings


def test_openai_base_url_defaults_to_official_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TG_ASSISTANT_OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://ezaiapi.com/v1")

    settings = Settings(_env_file=None)

    assert settings.openai_base_url == "https://api.openai.com/v1"
    assert settings.max_ai_requests_per_minute == 60
    assert settings.learning_job_interval_seconds == 2
    assert settings.max_output_tokens_per_request == 3_000


def test_openai_base_url_rejects_proxy_endpoint() -> None:
    with pytest.raises(ValidationError, match="API chính thức của OpenAI"):
        Settings(
            _env_file=None,
            openai_base_url="https://ezaiapi.com/v1",
        )


def test_openrouter_provider_uses_official_models_and_endpoint() -> None:
    settings = Settings(_env_file=None, ai_provider="openrouter")

    assert settings.ai_secret_name == "openrouter_api_key"
    assert settings.active_ai_base_url == "https://openrouter.ai/api/v1"
    assert settings.active_ai_model == "openai/gpt-5.6-terra"
    # Chat provider selection never changes the shared knowledge corpus embedding.
    assert settings.active_embedding_model == "nomic-embed-text:latest"
    assert settings.provider_embedding_model == "openai/text-embedding-3-small"


def test_openrouter_base_url_rejects_proxy_endpoint() -> None:
    with pytest.raises(ValidationError, match="API chính thức của OpenRouter"):
        Settings(
            _env_file=None,
            openrouter_base_url="https://example.com/api/v1",
        )


def test_off_provider_does_not_require_secret() -> None:
    settings = Settings(_env_file=None, ai_provider="off")

    assert settings.ai_secret_name is None


def test_ollama_provider_uses_loopback_and_separate_vector_store(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        ai_provider="ollama",
        data_dir=tmp_path,
    )

    assert settings.ai_secret_name is None
    assert settings.active_ai_base_url == "http://127.0.0.1:11434/v1"
    assert settings.active_ai_model == "qwen3:8b"
    assert settings.active_embedding_model == "nomic-embed-text:latest"
    assert settings.active_qdrant_vector_size == 768
    assert settings.resolved_active_qdrant_path == (
        tmp_path / "qdrant_ollama" / "nomic-embed-text-latest-768"
    )


def test_ollama_rejects_remote_endpoint() -> None:
    with pytest.raises(ValidationError, match="máy cục bộ"):
        Settings(
            _env_file=None,
            ai_provider="ollama",
            ollama_base_url="https://ollama.example.com/v1",
        )
