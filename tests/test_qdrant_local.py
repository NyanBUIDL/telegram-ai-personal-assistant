from __future__ import annotations

from tg_assistant.ai.vector import LocalVectorStore


def test_qdrant_local_filters_chat(tmp_path) -> None:
    store = LocalVectorStore(tmp_path / "qdrant", vector_size=3)
    store.upsert(1, [1.0, 0.0, 0.0], chat_id=100, message_id=1)
    store.upsert(2, [1.0, 0.0, 0.0], chat_id=200, message_id=1)
    results = store.search([1.0, 0.0, 0.0], allowed_chat_ids=[100])
    assert [item[0] for item in results] == [1]


def test_qdrant_local_filters_recent_reference_ids(tmp_path) -> None:
    store = LocalVectorStore(tmp_path / "qdrant-recent", vector_size=3)
    store.upsert(1, [1.0, 0.0, 0.0], chat_id=100, message_id=1)
    store.upsert(2, [1.0, 0.0, 0.0], chat_id=100, message_id=2)

    results = store.search(
        [1.0, 0.0, 0.0],
        allowed_chat_ids=[100],
        allowed_reference_ids=[2],
    )

    assert [item[0] for item in results] == [2]


def test_qdrant_local_batch_upsert(tmp_path) -> None:
    store = LocalVectorStore(tmp_path / "qdrant-batch", vector_size=3)
    store.upsert_many(
        [
            (10, [1.0, 0.0, 0.0], 100, 10),
            (11, [0.9, 0.1, 0.0], 100, 11),
        ]
    )

    results = store.search([1.0, 0.0, 0.0], allowed_chat_ids=[100])

    assert {item[0] for item in results} == {10, 11}


def test_relearning_upserts_into_the_same_shared_corpus(tmp_path) -> None:
    store = LocalVectorStore(tmp_path / "qdrant-shared", vector_size=3)
    store.upsert(10, [1.0, 0.0, 0.0], chat_id=100, message_id=10)
    store.upsert(10, [0.0, 1.0, 0.0], chat_id=100, message_id=10)

    results = store.search([0.0, 1.0, 0.0], allowed_chat_ids=[100])

    assert [item[0] for item in results] == [10]
