from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from tg_assistant.ai.vector import LocalVectorStore


@pytest.fixture
def store(tmp_path):
    value = LocalVectorStore(tmp_path / "vectors", vector_size=3)
    try:
        yield value
    finally:
        value.close()


def token(store):
    assert callable(getattr(store, "observation_token", None)), (
        "The actual vector owner must expose its RAM observation token"
    )
    return store.observation_token()


def test_successful_mutations_advance_once_and_preserve_points(store):
    initial = token(store)
    assert initial is not None
    assert str(uuid.UUID(initial[0])) == initial[0]
    assert initial[1] == 0

    assert store.upsert_many([(1, [1.0, 0.0, 0.0], 100, 10)], content_hashes={1: "digest"}) is None
    assert token(store) == (initial[0], 1)
    assert store.has_current_point(1, chat_id=100, message_id=10, content_hash="digest")
    assert store.upsert(2, [0.0, 1.0, 0.0], chat_id=200, message_id=20) is None
    assert token(store) == (initial[0], 2)
    assert store.delete_reference_ids([1, 1, 99]) == 2
    assert token(store) == (initial[0], 3)
    assert store.reference_ids() == [2]


def test_reads_and_empty_mutations_do_not_renew_observation(store, monkeypatch):
    store.upsert(1, [1.0, 0.0, 0.0], chat_id=100, message_id=10)
    observed = token(store)
    assert store.count() == store.count(chat_id=100) == 1
    assert store.reference_ids(chat_id=100) == [1]
    assert store.has_current_point(1, chat_id=100, message_id=10, content_hash=None)
    assert store.search([1.0, 0.0, 0.0], allowed_chat_ids=[100]) == [
        (
            1,
            1.0,
            {
                "chat_id": 100,
                "message_id": 10,
                "reference_id": 1,
                "content_hash": None,
                "store_id": None,
            },
        )
    ]
    assert store.search([1.0, 0.0, 0.0], allowed_chat_ids=[]) == []
    assert store.search([1.0, 0.0, 0.0], allowed_chat_ids=[100], allowed_reference_ids=[]) == []

    def unexpected_mutation(*args, **kwargs):
        pytest.fail("Empty input must not reach the client")

    monkeypatch.setattr(store.client, "upsert", unexpected_mutation)
    monkeypatch.setattr(store.client, "delete", unexpected_mutation)
    assert store.upsert_many([]) is None
    assert store.delete_reference_ids([]) == 0
    assert token(store) == observed


@pytest.mark.parametrize("operation", ["upsert", "delete", "close"])
def test_token_is_withdrawn_before_client_io_without_blocking_reads(store, monkeypatch, operation):
    store.upsert(1, [1.0, 0.0, 0.0], chat_id=100, message_id=10)
    before = token(store)
    entered, release = Event(), Event()
    original = getattr(store.client, operation)

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5), "Test did not release client I/O"
        return original(*args, **kwargs)

    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=2) as pool:
        patch.setattr(store.client, operation, blocked)
        action = {
            "upsert": lambda: store.upsert(2, [0.0, 1.0, 0.0], chat_id=100, message_id=20),
            "delete": lambda: store.delete_reference_ids([1]),
            "close": store.close,
        }[operation]
        work = pool.submit(action)
        try:
            assert entered.wait(5)
            assert pool.submit(token, store).result(timeout=2) is None
        finally:
            release.set()
        result = work.result(timeout=5)
    assert result == (1 if operation == "delete" else None)
    if operation == "close":
        assert token(store) is None
    else:
        assert token(store) == (before[0], before[1] + 1)
        assert store.reference_ids() == ([1, 2] if operation == "upsert" else [])


@pytest.mark.parametrize("operation", ["upsert", "delete"])
def test_partial_mutation_failure_stays_uncertain_after_unrelated_success(
    store, monkeypatch, operation
):
    store.upsert(1, [1.0, 0.0, 0.0], chat_id=100, message_id=10)
    assert token(store) is not None
    original = getattr(store.client, operation)
    error = RuntimeError("injected after partial vector effect")

    def partial_failure(collection, *args, **kwargs):
        if operation == "upsert":
            original(collection, args[0][:1], **kwargs)
        else:
            original(collection, *args, **kwargs)
        raise error

    with monkeypatch.context() as patch:
        patch.setattr(store.client, operation, partial_failure)
        with pytest.raises(RuntimeError) as caught:
            if operation == "upsert":
                store.upsert_many(
                    [
                        (2, [0.0, 1.0, 0.0], 100, 20),
                        (3, [0.0, 0.0, 1.0], 100, 30),
                    ]
                )
            else:
                store.delete_reference_ids([1])
        assert caught.value is error
    assert store.reference_ids() == ([1, 2] if operation == "upsert" else [])
    assert token(store) is None
    store.upsert(4, [1.0, 0.0, 0.0], chat_id=200, message_id=40)
    assert store.has_current_point(4, chat_id=200, message_id=40, content_hash=None)
    assert token(store) is None
    store.upsert_many([])
    store.delete_reference_ids([])
    assert token(store) is None


def test_client_validation_failure_is_preserved_and_withdraws_token(store):
    assert token(store) is not None
    with pytest.raises(ValueError):
        store.upsert(1, [1.0, 0.0], chat_id=100, message_id=10)
    assert token(store) is None


@pytest.mark.parametrize("close_took_effect", [False, True])
def test_close_failure_permanently_withdraws_token(store, monkeypatch, close_took_effect):
    assert token(store) is not None
    original = store.client.close
    error = RuntimeError("injected close uncertainty")

    def failed_close():
        if close_took_effect:
            original()
        raise error

    with monkeypatch.context() as patch:
        patch.setattr(store.client, "close", failed_close)
        with pytest.raises(RuntimeError) as caught:
            store.close()
        assert caught.value is error
    assert token(store) is None
    if not close_took_effect:
        store.upsert(1, [1.0, 0.0, 0.0], chat_id=100, message_id=10)
        assert store.count() == 1
        assert token(store) is None


def test_reopened_owner_has_new_incarnation_for_identical_persisted_corpus(tmp_path):
    path = tmp_path / "vectors"
    old = LocalVectorStore(path, vector_size=3)
    old.upsert(1, [1.0, 0.0, 0.0], chat_id=100, message_id=10)
    before = token(old)
    old.close()
    assert token(old) is None
    old.upsert_many([])
    assert old.delete_reference_ids([]) == 0
    assert token(old) is None
    reopened = LocalVectorStore(path, vector_size=3, require_existing=True)
    try:
        assert reopened.reference_ids() == [1]
        after = token(reopened)
        assert after is not None and after[0] != before[0] and after[1] == 0
        assert token(old) is None
    finally:
        reopened.close()


def test_one_finished_overlapping_mutation_cannot_revalidate_other_inflight_work(
    store, monkeypatch
):
    initial = token(store)
    entered = [Event(), Event()]
    release = [Event(), Event()]
    original = store.client.upsert

    def blocked(collection, points, **kwargs):
        index = points[0].id - 1
        entered[index].set()
        assert release[index].wait(5), "Test did not release overlapping operation"
        return original(collection, points, **kwargs)

    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=3) as pool:
        patch.setattr(store.client, "upsert", blocked)
        first = pool.submit(store.upsert, 1, [1.0, 0.0, 0.0], chat_id=100, message_id=10)
        second = pool.submit(store.upsert, 2, [0.0, 1.0, 0.0], chat_id=100, message_id=20)
        try:
            assert all(event.wait(5) for event in entered)
            assert pool.submit(token, store).result(timeout=2) is None
            release[0].set()
            assert first.result(timeout=5) is None
            assert pool.submit(token, store).result(timeout=2) is None
        finally:
            for event in release:
                event.set()
        assert second.result(timeout=5) is None
    assert store.reference_ids() == [1, 2]
    assert token(store) == (initial[0], initial[1] + 2)
