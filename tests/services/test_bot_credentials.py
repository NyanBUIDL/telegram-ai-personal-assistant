"""Private synthetic credential tests, not product/keychain acceptance."""

from __future__ import annotations

import builtins
import copy
import importlib.util
import re
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from tg_assistant.services import bot_credentials as prototype

SOURCE = Path(prototype.__file__)
SID = "S-1-5-21-111-222-333-1001"
TOKEN = "PRIVATE_SYNTHETIC_BOT_TOKEN_CANARY"  # noqa: S105 -- disposable fake backend only.
GLOBAL = ("TelegramAIPersonalAssistant", "telegram_bot_token")


class PublishedReference(list):
    """Synthetic durable publisher/history, separate from credential values."""

    def __init__(self):
        super().__init__([None])
        self.history = set()

    def __setitem__(self, index, value):
        if value is not None:
            self.history.add(value)
        super().__setitem__(index, value)


class StrictMemoryBackend:
    """Only this fake touches values; rejects any global backend operation."""

    def __init__(self):
        self.values = {GLOBAL: "UNRELATED_GLOBAL_TOKEN"}
        self.calls = []
        self.fail = None
        self.after_get = None

    def target(self, operation, service, name):
        assert re.fullmatch(r"TelegramAIPersonalAssistant/bot/v1/[a-f0-9]{64}", service)
        assert re.fullmatch(r"telegram_bot_token/[a-f0-9]{32}", name)
        self.calls.append((operation, service, name))
        return service, name

    def set_password(self, service, name, token):
        target = self.target("write", service, name)
        if self.fail == "write_before":
            raise RuntimeError(TOKEN)
        self.values[target] = token
        if self.fail == "write_after":
            raise RuntimeError(TOKEN)

    def get_password(self, service, name):
        target = self.target("read", service, name)
        if self.fail == "read":
            raise RuntimeError(TOKEN)
        value = self.values.get(target)
        if self.after_get:
            self.after_get()
        return value

    def delete_password(self, service, name):
        target = self.target("delete", service, name)
        if self.fail == "delete":
            raise RuntimeError(TOKEN)
        self.values.pop(target, None)


@pytest.fixture
def state():
    backend = StrictMemoryBackend()
    sid = [SID]
    owned = [True]
    lock = threading.RLock()

    def ownership():
        if not owned[0]:
            raise RuntimeError(TOKEN)

    def make(profile="profile-a", current=None, guard=None):
        selected = current if current is not None else PublishedReference()
        store = prototype.ScopedBotCredentials(
            profile, backend, ownership, lambda: selected[0], lambda: sid[0],
            publication_guard=guard or (lambda: lock),
            candidate_unpublished=lambda reference: reference not in selected.history,
        )
        return store, selected

    return backend, sid, owned, lock, make


def sanitized(error):
    assert TOKEN not in str(error)
    assert TOKEN not in repr(error)
    assert TOKEN not in "".join(traceback.format_exception(error))
    assert error.__context__ is None
    assert error.__cause__ is None
    assert re.fullmatch(r"bot_credentials_[a-z_]{1,40}", str(error))


def test_only_durable_current_reference_can_read_a_candidate(state):
    backend, _, _, _, make = state
    store, current = make()
    slot = store.allocate(TOKEN)
    assert TOKEN not in repr(slot)
    with pytest.raises(prototype.BotCredentialsUnavailable):
        store.get(slot.reference)
    current[0] = slot.reference
    assert store.get(slot.reference) == TOKEN
    assert backend.values[GLOBAL] == "UNRELATED_GLOBAL_TOKEN"


def test_same_sid_profiles_cannot_adopt_each_others_durable_reference(state):
    backend, _, _, _, make = state
    first, first_current = make("profile-a")
    second, second_current = make("profile-b")
    a, b = first.allocate("FIRST_PRIVATE_TOKEN"), second.allocate("SECOND_PRIVATE_TOKEN")
    first_current[0], second_current[0] = a.reference, b.reference
    assert first.get(a.reference) == "FIRST_PRIVATE_TOKEN"
    assert second.get(b.reference) == "SECOND_PRIVATE_TOKEN"
    second_current[0] = a.reference  # Incorrect durable state cannot override profile binding.
    with pytest.raises(prototype.BotCredentialsUnavailable):
        second.get(a.reference)
    assert len({service for _, service, _ in backend.calls}) == 2


@pytest.mark.parametrize("operation", ["allocate", "get", "discard"])
def test_changed_sid_denies_every_operation_before_backend_access(state, operation):
    backend, sid, _, _, make = state
    store, current = make()
    slot = store.allocate(TOKEN)
    current[0] = slot.reference if operation == "get" else None
    before = len(backend.calls)
    sid[0] = "S-1-5-21-111-222-333-1002"
    with pytest.raises(prototype.BotCredentialsUnavailable):
        if operation == "allocate":
            store.allocate("SECOND_TOKEN")
        elif operation == "get":
            store.get(slot.reference)
        else:
            store.discard_candidate(slot)
    assert len(backend.calls) == before


@pytest.mark.parametrize("candidate", ["current", "foreign", "copy", "arbitrary"])
def test_discard_requires_exact_ram_issued_unpublished_object(state, candidate):
    backend, _, _, _, make = state
    store, current = make()
    slot = store.allocate(TOKEN)
    other, _ = make("profile-b")
    supplied = {
        "current": slot, "foreign": other.allocate("FOREIGN_TOKEN"),
        "copy": copy.copy(slot), "arbitrary": object(),
    }[candidate]
    if candidate == "current":
        current[0] = slot.reference
    before = dict(backend.values)
    with pytest.raises(prototype.BotCredentialsUnavailable):
        store.discard_candidate(supplied)
    assert backend.values == before


def test_discard_removes_only_its_candidate_and_cannot_be_replayed(state):
    backend, _, _, _, make = state
    store, current = make()
    live = store.allocate("PUBLISHED_TOKEN")
    current[0] = live.reference
    candidate = store.allocate(TOKEN)
    store.discard_candidate(candidate)
    assert list(backend.values.values()) == ["UNRELATED_GLOBAL_TOKEN", "PUBLISHED_TOKEN"]
    with pytest.raises(prototype.BotCredentialsUnavailable):
        store.discard_candidate(candidate)
    assert store.get(live.reference) == "PUBLISHED_TOKEN"


@pytest.mark.parametrize("failure", ["write_before", "write_after"])
def test_failed_candidate_write_cleans_only_its_own_slot(state, failure):
    backend, _, _, _, make = state
    store, current = make()
    live = store.allocate("PUBLISHED_TOKEN")
    current[0] = live.reference
    before = dict(backend.values)
    backend.fail = failure
    with pytest.raises(prototype.BotCredentialsUnavailable) as caught:
        store.allocate(TOKEN)
    sanitized(caught.value)
    assert backend.values == before


@pytest.mark.parametrize("failure", ["read", "delete"])
def test_backend_failure_is_fixed_and_has_no_raw_exception_context(state, failure):
    backend, _, _, _, make = state
    store, current = make()
    slot = store.allocate(TOKEN)
    backend.fail = failure
    with pytest.raises(prototype.BotCredentialsUnavailable) as caught:
        if failure == "read":
            current[0] = slot.reference
            store.get(slot.reference)
        else:
            store.discard_candidate(slot)
    sanitized(caught.value)
    assert TOKEN in backend.values.values()


def test_lost_owning_guard_is_sanitized_and_does_not_read_or_delete(state):
    backend, _, owned, _, make = state
    store, current = make()
    slot = store.allocate(TOKEN)
    current[0], owned[0] = slot.reference, False
    before = len(backend.calls)
    with pytest.raises(prototype.BotCredentialsUnavailable) as caught:
        store.get(slot.reference)
    sanitized(caught.value)
    assert len(backend.calls) == before


def test_get_rechecks_binding_after_backend_read(state):
    backend, sid, _, _, make = state
    store, current = make()
    slot = store.allocate(TOKEN)
    current[0] = slot.reference
    backend.after_get = lambda: sid.__setitem__(0, "S-1-5-21-111-222-333-1002")
    with pytest.raises(prototype.BotCredentialsUnavailable) as caught:
        store.get(slot.reference)
    sanitized(caught.value)


def test_queued_discard_observes_publication_after_entering_shared_guard(state):
    backend, _, _, lock, make = state
    attempted = threading.Event()

    @contextmanager
    def admission():
        attempted.set()
        with lock:
            yield

    store, current = make(guard=admission)
    slot = store.allocate(TOKEN)
    attempted.clear()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with lock:
            future = executor.submit(store.discard_candidate, slot)
            assert attempted.wait(2)
            current[0] = slot.reference  # Actual publisher holds the same admission guard.
        with pytest.raises(prototype.BotCredentialsUnavailable):
            future.result(timeout=2)
    assert store.get(slot.reference) == TOKEN
    assert not any(operation == "delete" for operation, *_ in backend.calls)


def test_restart_cannot_adopt_or_discard_an_orphan_slot(state):
    backend, _, _, _, make = state
    original, _ = make()
    orphan = original.allocate(TOKEN)
    restarted, _ = make()
    with pytest.raises(prototype.BotCredentialsUnavailable):
        restarted.get(orphan.reference)
    with pytest.raises(prototype.BotCredentialsUnavailable):
        restarted.discard_candidate(orphan)
    assert TOKEN in backend.values.values()


def test_formerly_published_slot_is_never_retired_by_candidate_cleanup(state):
    backend, _, _, _, make = state
    store, current = make()
    slot = store.allocate(TOKEN)
    current[0] = slot.reference
    assert store.get(slot.reference) == TOKEN
    current[0] = None  # Withdrawal is not an owned durable retirement record.
    with pytest.raises(prototype.BotCredentialsUnavailable):
        store.discard_candidate(slot)
    assert TOKEN in backend.values.values()


@pytest.mark.parametrize("history_result", [False, None, 1, "true", "error"])
def test_missing_or_uncertain_durable_candidate_history_never_deletes(state, history_result):
    backend, _, _, lock, _ = state

    def history(_reference):
        if history_result == "error":
            raise RuntimeError(TOKEN)
        return history_result

    store = prototype.ScopedBotCredentials(
        "profile-a", backend, lambda: None, lambda: None, lambda: SID,
        publication_guard=lambda: lock, candidate_unpublished=history,
    )
    slot = store.allocate(TOKEN)
    with pytest.raises(prototype.BotCredentialsUnavailable) as caught:
        store.discard_candidate(slot)
    sanitized(caught.value)
    assert TOKEN in backend.values.values()
    assert not any(operation == "delete" for operation, *_ in backend.calls)


def test_failed_write_with_unknown_history_retains_orphan_instead_of_blind_delete(state):
    backend, _, _, lock, _ = state
    store = prototype.ScopedBotCredentials(
        "profile-a", backend, lambda: None, lambda: None, lambda: SID,
        publication_guard=lambda: lock, candidate_unpublished=lambda _reference: False,
    )
    backend.fail = "write_after"
    with pytest.raises(prototype.BotCredentialsUnavailable) as caught:
        store.allocate(TOKEN)
    sanitized(caught.value)
    assert TOKEN in backend.values.values()
    assert backend.values[GLOBAL] == "UNRELATED_GLOBAL_TOKEN"
    assert not any(operation == "delete" for operation, *_ in backend.calls)


@pytest.mark.parametrize("reference", [None, True, "telegram_bot_token", "bot:v1:" + "0" * 64 + ":" + "0" * 32])
def test_arbitrary_reference_is_not_a_credential_address(state, reference):
    backend, _, _, _, make = state
    store, _ = make()
    before = len(backend.calls)
    with pytest.raises(prototype.BotCredentialsUnavailable):
        store.get(reference)
    assert len(backend.calls) == before


@pytest.mark.parametrize("changed", ["ownership", "reference"])
def test_get_does_not_return_token_when_owning_proof_changes_during_read(state, changed):
    backend, _, owned, _, make = state
    store, current = make()
    slot = store.allocate(TOKEN)
    current[0] = slot.reference
    if changed == "ownership":
        backend.after_get = lambda: owned.__setitem__(0, False)
    else:
        backend.after_get = lambda: current.__setitem__(0, None)
    with pytest.raises(prototype.BotCredentialsUnavailable) as caught:
        store.get(slot.reference)
    sanitized(caught.value)


@pytest.mark.parametrize("sid", ["S-1-5-21-01", "s-1-5-21-1", "S-1-5-4294967296", "", True])
def test_noncanonical_sid_never_addresses_backend(state, sid):
    backend, _, _, lock, _ = state
    with pytest.raises(prototype.BotCredentialsUnavailable) as caught:
        prototype.ScopedBotCredentials(
            "profile-a", backend, lambda: None, lambda: None, lambda: sid,
            publication_guard=lambda: lock, candidate_unpublished=lambda _reference: True,
        )
    sanitized(caught.value)
    assert not backend.calls


def test_import_never_constructs_or_imports_global_credential_backend(monkeypatch):
    original = builtins.__import__

    def controlled(name, *args, **kwargs):
        if name == "keyring" or name.startswith("keyring.") or name == "tg_assistant.security":
            raise AssertionError("private_draft_imported_global_credential_backend")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", controlled)
    fresh_spec = importlib.util.spec_from_file_location("o04_private_credentials_import_check", SOURCE)
    fresh = importlib.util.module_from_spec(fresh_spec)
    monkeypatch.setitem(sys.modules, fresh_spec.name, fresh)
    fresh_spec.loader.exec_module(fresh)


def test_lazy_keyring_adapter_delegates_only_exact_scoped_addresses(state):
    backend, _, _, _, _ = state
    adapter = prototype.KeyringCredentialBackend(backend)
    service = "TelegramAIPersonalAssistant/bot/v1/" + "a" * 64
    name = "telegram_bot_token/" + "b" * 32
    adapter.set_password(service, name, TOKEN)
    assert adapter.get_password(service, name) == TOKEN
    adapter.delete_password(service, name)
    assert backend.calls == [("write", service, name), ("read", service, name), ("delete", service, name)]
    assert backend.values == {GLOBAL: "UNRELATED_GLOBAL_TOKEN"}
