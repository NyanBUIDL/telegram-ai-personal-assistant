from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import keyring
from cryptography.fernet import Fernet, InvalidToken

SERVICE: Final = "TelegramAIPersonalAssistant"
SECRET_KEYS: Final = {
    "database_password",
    "telegram_api_id",
    "telegram_api_hash",
    "telegram_phone",
    "telegram_bot_token",
    "openai_api_key",
    "openrouter_api_key",
    "coingecko_api_key",
    "session_encryption_key",
    "admin_dashboard_secret",
}


class SecretStoreError(RuntimeError):
    pass


class SecretStore:
    """Thin Windows Credential Manager wrapper; secrets never enter app settings tables."""

    def get(self, name: str) -> str | None:
        self._validate_name(name)
        return keyring.get_password(SERVICE, name)

    def set(self, name: str, value: str) -> None:
        self._validate_name(name)
        if not value:
            raise SecretStoreError("Không thể lưu secret rỗng")
        keyring.set_password(SERVICE, name, value)

    def delete(self, name: str) -> None:
        self._validate_name(name)
        try:
            keyring.delete_password(SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            pass

    @staticmethod
    def _validate_name(name: str) -> None:
        if name not in SECRET_KEYS:
            raise SecretStoreError(f"Tên secret không được phép: {name}")


TOKEN_PATTERNS = [
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bCG-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)(api[_ -]?hash|password|token|secret|otp)\s*[:=]\s*\S+"),
    re.compile(r"\b[A-Fa-f0-9]{32}\b"),
]


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(term in key.lower() for term in ("password", "token", "secret", "hash", "otp"))
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    result = value
    for pattern in TOKEN_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def contains_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in TOKEN_PATTERNS)


@dataclass(slots=True)
class EncryptedSession:
    store: SecretStore

    def _fernet(self) -> Fernet:
        encoded_key = self.store.get("session_encryption_key")
        if not encoded_key:
            encoded_key = Fernet.generate_key().decode("ascii")
            self.store.set("session_encryption_key", encoded_key)
        return Fernet(encoded_key.encode("ascii"))

    def encrypt_file(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        encrypted = self._fernet().encrypt(source.read_bytes())
        target.write_bytes(encrypted)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass

    def decrypt_file(self, source: Path, target: Path) -> None:
        try:
            raw = self._fernet().decrypt(source.read_bytes())
        except InvalidToken as exc:
            raise SecretStoreError("Không thể giải mã Telegram session") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
