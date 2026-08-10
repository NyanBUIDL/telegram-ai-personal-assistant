from __future__ import annotations

import pytest
import typer

from tg_assistant import runtime


class FakeSecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value


def test_openai_key_is_prompted_hidden_and_persisted_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeSecretStore()
    saved_env: list[dict[str, str]] = []
    monkeypatch.setattr(runtime.getpass, "getpass", lambda _prompt: "sk-test-12345678901234567890")
    monkeypatch.setattr(runtime, "_save_env_values", saved_env.append)

    assert runtime.configure_ai_key(store, "openai")
    assert store.values["openai_api_key"].startswith("sk-")
    assert saved_env == [
        {"TG_ASSISTANT_OPENAI_API_KEY_SOURCE": "windows_credential_manager"}
    ]
    assert all("sk-" not in str(item) for item in saved_env)


def test_openrouter_key_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeSecretStore()
    monkeypatch.setattr(runtime.getpass, "getpass", lambda _prompt: "invalid")

    with pytest.raises(typer.BadParameter, match="sk-or-v1"):
        runtime.configure_ai_key(store, "openrouter")


def test_existing_key_is_kept_when_user_presses_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeSecretStore()
    store.values["openrouter_api_key"] = "sk-or-v1-existing"
    monkeypatch.setattr(runtime.getpass, "getpass", lambda _prompt: "")

    assert runtime.configure_ai_key(store, "openrouter")
    assert store.values["openrouter_api_key"] == "sk-or-v1-existing"
