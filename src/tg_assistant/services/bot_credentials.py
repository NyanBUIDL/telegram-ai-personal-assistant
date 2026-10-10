"""SID/profile scoped bot credentials; backend access requires owning admission."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

SERVICE = "TelegramAIPersonalAssistant"
PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
REFERENCE = re.compile(r"bot:v1:([a-f0-9]{64}):([a-f0-9]{32})\Z")


class CredentialBackend(Protocol):
    def set_password(self, service: str, name: str, value: str) -> None: ...
    def get_password(self, service: str, name: str) -> str | None: ...
    def delete_password(self, service: str, name: str) -> None: ...


class KeyringCredentialBackend:
    """Lazy native composition adapter; tests inject a disposable backend."""

    def __init__(self, backend=None):
        if backend is None:
            import keyring

            backend = keyring
        self._backend = backend

    def set_password(self, service: str, name: str, value: str) -> None:
        self._backend.set_password(service, name, value)

    def get_password(self, service: str, name: str) -> str | None:
        return self._backend.get_password(service, name)

    def delete_password(self, service: str, name: str) -> None:
        self._backend.delete_password(service, name)


class BotCredentialsUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class OwnedBotSlot:
    generation: str
    reference: str

    def __repr__(self):
        return "OwnedBotSlot()"


class ScopedBotCredentials:
    """Caller supplies real ownership and a guard shared with its SQL publisher.

    The guard must span that publisher's allocate/publish/discard decision.
    This adapter neither establishes OS ownership nor coordinates other
    processes, and cannot retire previously published slots.
    """

    def __init__(
        self, profile_id: str, backend: CredentialBackend,
        check_ownership: Callable[[], None], current_reference: Callable[[], str | None],
        sid_reader: Callable[[], str], *, publication_guard: Callable[[], AbstractContextManager],
        candidate_unpublished: Callable[[str], bool],
    ):
        self._backend = backend
        self._ownership = check_ownership
        self._current_reference = current_reference
        self._sid_reader = sid_reader
        self._publication_guard = publication_guard
        self._candidate_unpublished = candidate_unpublished
        self._issued = {}
        self._allocated_references = set()
        failed = False
        try:
            require(type(profile_id) is str and PROFILE.fullmatch(profile_id))
            sid = canonical_sid(sid_reader())
            self._sid = sid
            encoded = json.dumps([sid, profile_id], separators=(",", ":")).encode("ascii")
            self._binding = hashlib.sha256(encoded).hexdigest()
            self._service = SERVICE + "/bot/v1/" + self._binding
            with publication_guard():
                self._check()
        except Exception:
            failed = True
        if failed:
            # Raise after leaving the exception handler: no backend/callback
            # error is retained as context, not merely hidden by from None.
            raise BotCredentialsUnavailable("bot_credentials_unavailable") from None

    def _check(self):
        require(self._ownership() is None)
        require(canonical_sid(self._sid_reader()) == self._sid)

    def _reference_parts(self, value):
        require(type(value) is str and len(value) == 104)
        matched = REFERENCE.fullmatch(value)
        require(matched is not None and matched[1] == self._binding)
        return matched[2]

    def _current(self):
        value = self._current_reference()
        if value is not None:
            self._reference_parts(value)
        return value

    def _execute(self, operation, code):
        failed = False
        result = None
        try:
            with self._publication_guard():
                self._check()
                result = operation()
        except Exception:
            failed = True
        if failed:
            raise BotCredentialsUnavailable(code) from None
        return result

    def _delete_unpublished(self, reference, name):
        self._check()
        require(self._current() != reference)
        require(self._candidate_unpublished(reference) is True)
        # Recheck immediately before the external write, under the guard also
        # held by the durable publisher. A callback alone is not an atomic gate.
        self._check()
        require(self._current() != reference)
        require(self._candidate_unpublished(reference) is True)
        self._backend.delete_password(self._service, name)

    def _cleanup_new(self, reference, name):
        try:
            self._delete_unpublished(reference, name)
        except Exception:
            # Unknown/lost admission or delete failure leaves an orphan. It is
            # never adopted, enumerated or blindly retried by this adapter.
            return False
        return True

    def allocate(self, token: str) -> OwnedBotSlot:
        def create():
            require(type(token) is str and 0 < len(token) <= 4096)
            current = self._current()
            generation = secrets.token_hex(16)
            reference = "bot:v1:" + self._binding + ":" + generation
            self._reference_parts(reference)
            require(reference != current and reference not in self._allocated_references)
            slot = OwnedBotSlot(generation, reference)
            name = "telegram_bot_token/" + generation
            self._allocated_references.add(reference)
            failed = False
            try:
                self._backend.set_password(self._service, name, token)
                self._check()
                self._current()
            except Exception:
                failed = True
            if failed:
                self._cleanup_new(reference, name)
                raise BotCredentialsUnavailable("bot_credentials_write_unavailable") from None
            self._issued[slot] = (reference, name)
            return slot

        return self._execute(create, "bot_credentials_write_unavailable")

    def get(self, reference: str) -> str:
        def read():
            generation = self._reference_parts(reference)
            require(self._current() == reference)
            value = self._backend.get_password(self._service, "telegram_bot_token/" + generation)
            self._check()
            require(self._current() == reference)
            require(type(value) is str and 0 < len(value) <= 4096)
            return value

        return self._execute(read, "bot_credentials_read_unavailable")

    def discard_candidate(self, slot: OwnedBotSlot) -> None:
        def discard():
            require(type(slot) is OwnedBotSlot and slot in self._issued)
            reference, name = self._issued[slot]
            require(slot.reference == reference and slot.generation == name.split("/")[1])
            self._delete_unpublished(reference, name)
            del self._issued[slot]

        self._execute(discard, "bot_credentials_discard_unavailable")


def require(condition):
    if not condition:
        raise BotCredentialsUnavailable("bot_credentials_unavailable")


def canonical_sid(value):
    require(type(value) is str and len(value) <= 184)
    require(re.fullmatch(r"S-1-(?:0|[1-9][0-9]{0,14})(?:-(?:0|[1-9][0-9]{0,9})){1,15}", value))
    numbers = value.split("-")[2:]
    require(int(numbers[0]) <= 2**48 - 1)
    require(all(int(part) <= 0xFFFFFFFF for part in numbers[1:]))
    return value
