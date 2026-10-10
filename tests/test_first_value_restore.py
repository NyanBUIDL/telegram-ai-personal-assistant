"""Real staged restore preserves current history and fails before oversized JSON loading."""
# ruff: noqa: F811 - shared fixture
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from integration.test_backup_restore import service  # noqa: F401

from tg_assistant.paths import current_user_sid
from tg_assistant.services.first_value import _namespace, _SelectionHeader


def install_header(service, value=None):
    namespace = _namespace(service.settings.profile_id, current_user_sid())
    header = _SelectionHeader(schema_version=1, namespace=namespace, revision=3,
        selection_generation=2, restore_epoch="a" * 32)
    engine = sa.create_engine(service._url(async_driver=False))
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO app_settings(key,value) VALUES(:key,:value)"),
            {"key": "fv1." + namespace, "value": value or header.model_dump_json()})
    engine.dispose()
    return namespace, header


def read_header(service, namespace):
    engine = sa.create_engine(service._url(async_driver=False))
    with engine.connect() as connection:
        value = connection.execute(sa.text("SELECT value FROM app_settings WHERE key=:key"),
            {"key": "fv1." + namespace}).scalar_one()
    engine.dispose()
    return value


def test_restore_rotates_current_host_namespace(service, tmp_path):
    archive = tmp_path / "saved.zip"
    service.backup(archive)
    namespace, before = install_header(service)
    lease = service.fence.acquire("restore")
    service.restore(archive, lease)
    after = json.loads(read_header(service, namespace))
    assert after["restore_epoch"] != before.restore_epoch
    assert after["selection_generation"] == before.selection_generation + 1


@pytest.mark.parametrize("value", ['"' + 'x' * 9000 + '"', '{"malformed":true}'], ids=["oversized", "malformed"])
def test_bad_namespace_aborts_before_generic_setting_materialization(service, tmp_path, value):
    archive = tmp_path / "saved.zip"
    service.backup(archive)
    namespace, _ = install_header(service, value)
    statements = []
    def trace(conn, cursor, statement, *args):
        statements.append(statement.lower())
    sa.event.listen(sa.engine.Engine, "before_cursor_execute", trace)
    lease = service.fence.acquire("restore")
    try:
        with pytest.raises(RuntimeError) as caught:
            service.restore(archive, lease)
        assert caught.value.original_preserved
        assert not any("select app_settings.key, app_settings.value" in statement for statement in statements)
    finally:
        sa.event.remove(sa.engine.Engine, "before_cursor_execute", trace)
    assert read_header(service, namespace) == value


def test_restore_retains_exact_completed_receipt_and_attempt_bytes(service, tmp_path):
    from tg_assistant.ai.observations import ChatModelCandidate, SourceIndexBinding
    from tg_assistant.services.first_value import _Attempt, _Receipt, _request_digest, _row_key
    archive = tmp_path / "saved.zip"
    service.backup(archive)
    namespace, header = install_header(service)
    stamp = datetime.now(UTC)
    digest = _request_digest(namespace, bot_id=456, enrollment_generation="c" * 32, owner_id=123, incoming_message_id=1)
    attempt = _Attempt(schema_version=1, namespace=namespace, revision=4, request_digest=digest,
        bot_id=456, enrollment_generation="c" * 32, owner_id=123, incoming_message_id=1,
        pair_fingerprint="b" * 64, selection_generation=header.selection_generation,
        restore_epoch=header.restore_epoch, source_id=-123, source_epoch=1, accepted_at=stamp,
        expires_at=stamp + timedelta(seconds=300), phase="completed", chunk_count=1,
        submitted_ordinal=1, returned_message_ids=(999,))
    binding = SourceIndexBinding(service.settings.profile_id, 123, "b" * 64, header.selection_generation,
        header.restore_epoch, -123, 1, "policy", "ollama", "local", "embed", "1", 3, "store", 1,
        "vector", "eligibility", (ChatModelCandidate("ollama", "local", "model", "candidate"),))
    receipt = _Receipt(schema_version=1, namespace=namespace, revision=0, request_digest=digest,
        completed_at=stamp, attempt_revision=4, configuration_fingerprint="d" * 64, binding=asdict(binding),
        execution=dict(request_id="answer", profile_id=binding.profile_id, provider="ollama", endpoint_id="local",
            requested_model="model", reported_model=None, capability_fingerprint="execution", route="local_rag",
            fallback_used=False, pricing_version="local-zero-v1", settled_at=stamp, input_tokens=1, output_tokens=1,
            cached_tokens=0, cache_write_tokens=0, cost_usd="0"), query_embedding_request_id="query",
        cited_refs=(dict(chat_id=-123, message_id=1, reference_id=1, content_hash="a" * 64,
            context_hash="b" * 64, semantic_used=True, keyword_used=False),), retrieval_mode="semantic",
        scope=dict(selected_chat_id=-123, after=None, before=stamp, sender_id=None, has=None, content_type=None,
            query_route="local_rag", embedding_route="cloud_embedding"), message_ids=(999,))
    original = {_row_key(namespace, kind, digest): json.dumps(record.model_dump(mode="json"), indent=1).replace('"completed"', '"\\u0063ompleted"')
        for kind, record in (("attempt", attempt), ("receipt", receipt))}
    engine = sa.create_engine(service._url(async_driver=False))
    with engine.begin() as connection:
        for key, value in original.items():
            connection.execute(sa.text("INSERT INTO app_settings(key,value) VALUES(:key,:value)"), {"key": key, "value": value})
    engine.dispose()
    lease = service.fence.acquire("restore")
    service.restore(archive, lease)
    engine = sa.create_engine(service._url(async_driver=False))
    try:
        with engine.connect() as connection:
            for key, value in original.items():
                assert connection.execute(sa.text("SELECT value FROM app_settings WHERE key=:key"), {"key": key}).scalar_one() == value
    finally:
        engine.dispose()


def test_failure_after_staged_invalidation_rolls_back_with_original_intact(service, tmp_path, monkeypatch):
    from tg_assistant.services import first_value
    archive = tmp_path / "saved.zip"
    service.backup(archive)
    namespace, _ = install_header(service)
    before = read_header(service, namespace)
    invalidate = first_value.invalidate_first_value_after_restore
    def fail(connection, **kwargs):
        invalidate(connection, **kwargs)
        raise RuntimeError("synthetic_staging_failure")
    monkeypatch.setattr(first_value, "invalidate_first_value_after_restore", fail)
    lease = service.fence.acquire("restore")
    with pytest.raises(RuntimeError) as caught:
        service.restore(archive, lease)
    assert caught.value.original_preserved
    assert read_header(service, namespace) == before
