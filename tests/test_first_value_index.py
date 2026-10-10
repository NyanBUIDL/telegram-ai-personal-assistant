"""RED contract for the bounded current-index reader (U03 Task 3, reader half only).

Real disposable SQLite, the installed QdrantLocal and the real FirstValueService /
OnboardingCoordinator / MaintenanceService are used. No network or provider call
is made. The only stand-in is a minimal runtime shell that carries exactly the
attributes FirstValueService._admit and the reader borrow (see the report).
Restore hook tests are intentionally absent: root owns that schema and hook.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import hashlib
import importlib
import importlib.util
import inspect
import os
import pickle
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, delete, event, insert, select, update

from tg_assistant.ai.budget import PricingSnapshot
from tg_assistant.ai.local_first import embedding_decision
from tg_assistant.ai.observations import (
    ChatModelCandidate,
    CitedReference,
    RetrievalScope,
    SourceIndexBinding,
    candidate_fingerprint,
)
from tg_assistant.ai.vector import LocalVectorStore
from tg_assistant.config import Settings
from tg_assistant.db.base import Base, Database, configure_sqlite
from tg_assistant.db.models import (
    AiBudgetLock,
    AiBudgetReservation,
    AppSetting,
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
    TelegramMessage,
    VectorStore,
)
from tg_assistant.paths import current_user_sid, ensure_runtime_dirs
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.connections import (
    ConnectionHealth,
    HealthObservation,
    storage_probe,
)
from tg_assistant.services.first_value import FirstValueService
from tg_assistant.services.maintenance import MaintenanceService
from tg_assistant.services.onboarding import OnboardingCoordinator
from tg_assistant.services.vector_paths import pointer_key, write_ready
from tg_assistant.services.vector_reliability import SourceIndexService

MODULE = "tg_assistant.services.first_value_index"
PROFILE_ID = "owner-profile"
OWNER = 9007199254740993
CHAT = -1009007199254740993
OTHER_CHAT = -1002
EMBED_OPERATION, EMBED_FEATURE, EMBED_ROUTE = "embedding", "embedding", "cloud_embedding"


def load_module():
    assert importlib.util.find_spec(MODULE) is not None, (
        "services/first_value_index.py with CurrentIndexReader is not implemented"
    )
    return importlib.import_module(MODULE)


def test_current_index_reader_module_exists():
    module = load_module()
    assert inspect.isclass(getattr(module, "CurrentIndexReader", None))
    reader = module.CurrentIndexReader
    assert inspect.iscoroutinefunction(reader.read_source_binding)
    assert inspect.iscoroutinefunction(reader.check_used_references)
    assert inspect.iscoroutinefunction(reader.close)
    for name in ("bind_current", "still_current_in_transaction", "withdraw"):
        assert callable(getattr(reader, name, None)), name
        assert not inspect.iscoroutinefunction(getattr(reader, name)), name
    assert inspect.signature(reader).parameters.keys() >= {"runtime", "first_value", "windows_sid"}


# --------------------------------------------------------------------------- harness


class BotShell:
    def __init__(self, runtime, engine, fence):
        self._runtime, self.engine, self.fence = runtime, engine, fence
        self.service = SimpleNamespace(verified_identity=None)

    def management_admitted(self):
        return self._runtime.admitted


class RuntimeShell:
    """Only what FirstValueService._admit and the reader may borrow."""

    def __init__(self, settings, vectors):
        self.settings, self.admitted = settings, True
        self.rag = SimpleNamespace(vectors=vectors)
        self._knowledge_lock = asyncio.Lock()
        self.user = SimpleNamespace(owner_id=OWNER)
        self.first_value = self.bot_runtime = None

    def management_admitted(self):
        return self.admitted


def new_vectors(settings, path):
    return LocalVectorStore(path, vector_size=3, profile=settings.embedding_profile)


class Env:
    @classmethod
    async def create(cls, tmp_path, **settings_options):
        module = load_module()
        self = cls()
        self.tmp, self.sid, self.chat = tmp_path, current_user_sid(), CHAT
        options = {
            "ollama_vector_size": 3,
            "ai_provider": "ollama",
            "max_input_tokens_per_request": 10_000_000,
            **settings_options,
        }
        self.settings = Settings(
            _env_file=None, data_dir=tmp_path / "data", profile_id=PROFILE_ID, **options
        )
        ensure_runtime_dirs(self.settings.data_dir, profile_id=PROFILE_ID)
        self.profile = self.settings.embedding_profile
        self.path = self.settings.resolved_semantic_vector_path
        self.vectors = new_vectors(self.settings, self.path)
        self.engine = create_engine("sqlite:///" + str(tmp_path / "selected.db"))
        event.listen(self.engine, "connect", configure_sqlite)
        Base.metadata.create_all(self.engine)
        self.statements: list[str] = []
        event.listen(
            self.engine,
            "before_cursor_execute",
            lambda conn, cur, statement, *rest: self.statements.append(statement),
        )
        self.fence = MaintenanceService(tmp_path / "config", profile_id=PROFILE_ID)
        self.clock = datetime(2026, 10, 10, tzinfo=UTC)
        names = ("storage", "telegram_account", "control_bot", "chat_ai", "embeddings", "runtime")
        probes = {
            name: (lambda: HealthObservation(state="ready", capabilities=("measured",)))
            for name in names
        }
        probes["storage"] = storage_probe(self.engine)
        health = ConnectionHealth(probes=probes, now=lambda: self.clock, ttl_seconds=60)
        health.refresh()
        from tg_assistant.contracts import OnboardingStage

        self.coordinator = OnboardingCoordinator(
            engine=self.engine,
            profile_id=PROFILE_ID,
            storage_backend="sqlite",
            fence=self.fence,
            connections=health,
            verifiers={stage: (lambda: None) for stage in OnboardingStage if stage.value != "ready"},
            now=lambda: self.clock,
        )
        self.runtime = RuntimeShell(self.settings, self.vectors)
        self.runtime.database = Database("sqlite+aiosqlite:///" + str(tmp_path / "selected.db"))
        self.runtime.policy = PolicyEngine()
        self.runtime.rag.database = self.runtime.database
        self.runtime.rag.policy = self.runtime.policy
        self.runtime.bot_runtime = BotShell(self.runtime, self.engine, self.fence)
        self.first_value = FirstValueService(
            runtime=self.runtime, coordinator=self.coordinator, windows_sid=self.sid
        )
        self.runtime.first_value = self.first_value
        self.points = []
        self.closed_vectors = []
        with self.engine.begin() as connection:
            connection.execute(insert(AiBudgetLock).values(profile_id=PROFILE_ID, version=0))
            connection.execute(
                insert(VectorStore).values(
                    store_id=self.profile.store_id,
                    path=str(self.path.resolve()),
                    collection=LocalVectorStore.COLLECTION,
                    provider=self.profile.provider,
                    endpoint_id=self.profile.endpoint_id,
                    model=self.profile.model,
                    embedding_version=self.profile.embedding_version,
                    dimension=self.profile.dimension,
                    role="semantic_active",
                    state="active",
                )
            )
        self.add_source(CHAT)
        await self.first_value.select_source(CHAT)
        self.reader = module.CurrentIndexReader(
            runtime=self.runtime, first_value=self.first_value, windows_sid=self.sid
        )
        return self

    async def aclose(self):
        try:
            await self.reader.close()
        finally:
            await self.first_value.close()
            for vectors in {id(v): v for v in (self.vectors, *self.closed_vectors)}.values():
                if vectors.observation_token() is not None:
                    vectors.close()
            self.fence.close()
            await self.runtime.database.engine.dispose()
            self.engine.dispose()

    # ---- SQL / vector fixtures
    def add_source(self, chat_id, **policy):
        policy.setdefault("filtering_level", "relaxed")  # keeps the intentional 2-char rows
        with self.engine.begin() as connection:
            connection.execute(
                insert(TelegramChat).values(chat_id=chat_id, chat_type="supergroup", title="A")
            )
            connection.execute(
                insert(TelegramChatPolicy).values(
                    chat_id=chat_id,
                    allowed=True,
                    authorization_epoch=1,
                    ai_mode="local_first",
                    **policy,
                )
            )
            connection.execute(
                insert(TelegramChatPermission).values(
                    chat_id=chat_id, permission="auto_knowledge", enabled=True
                )
            )
            connection.execute(insert(TelegramChatPermission).values(
                chat_id=chat_id, permission="search_messages", enabled=True))

    def add_indexed(self, message_id, text="Kế hoạch ra mắt là thứ Sáu", *, chat_id=CHAT,
                    state="settled", sender_id=None, vectors=None, metadata=None,
                    point=True, sent_at=None, has_media=False):
        digest = embedding_decision(text, metadata=None, filtering_level="relaxed").content_hash
        request_id = hashlib.sha256(f"{chat_id}:{message_id}".encode()).hexdigest()
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            # Production stores the exact current policy_version with the indexed row.
            policy_version = SourceIndexService.policy_version(connection.execute(
                select(TelegramChatPolicy.__table__).where(
                    TelegramChatPolicy.__table__.c.chat_id == chat_id)).one())
            row_id = connection.execute(
                insert(TelegramMessage).values(
                    chat_id=chat_id,
                    message_id=message_id,
                    sender_id=sender_id,
                    text=text,
                    has_media=has_media,
                    sent_at=sent_at or now - timedelta(minutes=5),
                    vector_status="indexed",
                    vector_dirty=False,
                    content_hash=digest,
                    normalized_text=text,
                    embedding_provider=self.profile.provider,
                    embedding_model=self.profile.model,
                    embedding_version=self.profile.embedding_version,
                    embedded_at=now,
                    metadata_json={
                        "embedding_store_id": self.profile.store_id,
                        "embedding_content_hash": digest,
                        "embedding_request_id": request_id,
                        "embedding_policy_version": policy_version,
                        **(metadata or {}),
                    },
                )
            ).inserted_primary_key[0]
            if state is not None:
                connection.execute(
                    insert(AiBudgetReservation).values(
                        request_id=request_id,
                        profile_id=PROFILE_ID,
                        occurred_at=now,
                        provider=self.profile.provider,
                        model=self.profile.model,
                        pricing_version="local-zero-v1",
                        pricing_rates=PricingSnapshot(self.profile.provider, self.profile.model,
                            "local-zero-v1", "local", *(Decimal(0) for _ in range(4)),
                            datetime(9999, 1, 1, tzinfo=UTC)).as_dict(),
                        operation=EMBED_OPERATION,
                        feature=EMBED_FEATURE,
                        route=EMBED_ROUTE,
                        chat_id=chat_id,
                        reserved_input_tokens=10,
                        reserved_output_tokens=0,
                        reserved_cost_usd=0,
                        is_local=True,
                        fallback_used=False,
                        state=state,
                        submitted_at=now if state == "settled" else None,
                        settled_at=now if state == "settled" else None,
                        actual_input_tokens=10 if state == "settled" else None,
                        actual_output_tokens=0 if state == "settled" else None,
                        cached_tokens=0 if state == "settled" else None,
                        cache_write_tokens=0 if state == "settled" else None,
                        actual_cost_usd=0 if state == "settled" else None,
                    )
                )
        if point:
            (vectors or self.vectors).upsert_many(
                [(row_id, [1.0, 0.0, 0.0], chat_id, message_id)], content_hashes={row_id: digest}
            )
        self.points.append((row_id, chat_id, message_id, digest))
        return SimpleNamespace(row_id=row_id, message_id=message_id, digest=digest,
                               request_id=request_id, chat_id=chat_id,
                               raw_hash=hashlib.sha256(text.encode("utf-8")).hexdigest())

    def ref(self, row, **changes):
        values = dict(chat_id=row.chat_id, message_id=row.message_id, reference_id=row.row_id,
                      content_hash=row.raw_hash, context_hash="c" * 64, semantic_used=True,
                      keyword_used=False)
        return CitedReference(**{**values, **changes})

    def scope(self, **changes):
        values = dict(selected_chat_id=CHAT, after=None,
                      before=datetime.now(UTC) + timedelta(days=1), sender_id=None, has=None,
                      content_type=None, query_route="local_rag", embedding_route="cloud_embedding")
        return RetrievalScope(**{**values, **changes})

    def header(self):
        with self.engine.connect() as connection:
            return self.first_value._read_header(connection)

    def binding(self, observation, **changes):
        header = self.header()
        values = dict(
            profile_id=PROFILE_ID,
            owner_id=OWNER,
            pair_fingerprint="a" * 64,
            selection_generation=header.selection_generation,
            restore_epoch=header.restore_epoch,
            selected_chat_id=observation.selected_chat_id,
            source_epoch=observation.source_epoch,
            source_policy_fingerprint=observation.source_policy_fingerprint,
            embedding_provider=observation.embedding_provider,
            embedding_endpoint_id=observation.embedding_endpoint_id,
            embedding_model=observation.embedding_model,
            embedding_model_version=observation.embedding_model_version,
            embedding_dimension=observation.embedding_dimension,
            embedding_store_id=observation.embedding_store_id,
            active_index_generation=observation.active_index_generation,
            vector_owner_fingerprint=observation.vector_owner_fingerprint,
            retrieval_eligibility_fingerprint=observation.retrieval_eligibility_fingerprint,
            chat_candidates=(
                ChatModelCandidate("ollama", "endpoint-local", "qwen3:8b",
                                   candidate_fingerprint("ollama", "endpoint-local", "qwen3:8b")),
            ),
        )
        return SourceIndexBinding(**{**values, **changes})

    async def bound(self):
        observation = await self.reader.read_source_binding(CHAT)
        assert observation is not None
        binding = self.binding(observation)
        assert self.reader.bind_current(observation, binding) is True
        return observation, binding

    async def checked(self, binding, *rows, scope=None):
        return await self.reader.check_used_references(
            binding, scope or self.scope(), tuple(self.ref(row) for row in rows)
        )

    def current(self, binding, checked):
        with self.engine.connect() as connection:
            return self.reader.still_current_in_transaction(connection, binding, checked)

    def update(self, table, where, **values):
        with self.engine.begin() as connection:
            connection.execute(update(table).where(where).values(**values))

    # ---- generation fixtures (mirrors VectorRecoveryService publication)
    def stage_generation(self, generation_id="b" * 32):
        from tg_assistant.services.vector_paths import generation_path

        path = generation_path(self.settings, generation_id)
        staged = new_vectors(self.settings, path)
        self.closed_vectors.append(staged)
        for row_id, chat_id, message_id, digest in self.points:
            staged.upsert_many([(row_id, [1.0, 0.0, 0.0], chat_id, message_id)],
                               content_hashes={row_id: digest})
        record = {
            "profile_id": PROFILE_ID, "sid": self.sid, "owner_id": OWNER,
            "store_id": self.profile.store_id,
            "identity": self.profile.model_dump(mode="json", exclude={"cloud_consent"}),
            "generation_id": generation_id, "generation": 1, "state": "ready",
            "verified_corpus": "d" * 64,
        }
        return staged, path, record

    def commit_pointer(self, path, record):
        write_ready(path, record)
        with self.engine.begin() as connection:
            connection.execute(insert(AppSetting).values(key=pointer_key(self.profile.store_id),
                                                         value=record))
            connection.execute(update(VectorStore).where(
                VectorStore.store_id == self.profile.store_id).values(path=str(path.resolve())))

    def publish(self, staged):
        old, self.runtime.rag.vectors = self.runtime.rag.vectors, staged
        self.vectors = staged
        old.close()


def run(tmp_path, body, **settings_options):
    async def main():
        env = await Env.create(tmp_path, **settings_options)
        try:
            await body(env)
        finally:
            await env.aclose()

    asyncio.run(main())


def forbid_corpus_and_io(env, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("final transaction/full corpus scan must not reach Qdrant")

    for name in ("scroll", "count", "query_points", "retrieve", "collection_exists",
                 "get_collection"):
        monkeypatch.setattr(env.vectors.client, name, refuse)
    for name in ("reference_ids", "count", "has_current_point", "search"):
        monkeypatch.setattr(env.vectors, name, refuse)


# --------------------------------------------------------------------------- happy path


def test_owned_current_source_binds_and_survives_final_transaction(tmp_path, monkeypatch):
    async def body(env):
        row = env.add_indexed(10)
        observation = await env.reader.read_source_binding(CHAT)
        assert observation is not None
        assert dataclasses.is_dataclass(observation) and observation.__dataclass_params__.frozen
        assert type(observation).__name__ == "SourceIndexObservation"
        assert observation.selected_chat_id == CHAT
        assert observation.embedding_store_id == env.profile.store_id
        assert (observation.embedding_provider, observation.embedding_model) == (
            env.profile.provider, env.profile.model)
        assert observation.embedding_dimension == 3
        assert observation.active_index_generation == 0  # initial pointer=None generation0
        assert observation.source_epoch == 1
        assert "://" not in repr(observation) and str(env.path) not in repr(observation)
        binding = env.binding(observation)
        assert env.reader.bind_current(observation, binding) is True
        checked = await env.checked(binding, row)
        assert checked is not None and type(checked).__name__ == "VerifiedReferences"
        # Final transaction: RAM + bounded SQL only; Qdrant and corpus scans are refused.
        forbid_corpus_and_io(env, monkeypatch)
        assert not inspect.iscoroutinefunction(env.reader.still_current_in_transaction)
        before = len(env.statements)
        assert env.current(binding, checked) is True
        assert len(env.statements) > before  # actually consulted SQL on the supplied connection
        assert all("data_version" not in s.lower() for s in env.statements)

    run(tmp_path, body)


def test_context_hash_is_carried_evidence_not_reconstructed_or_compared(tmp_path):
    async def body(env):
        row = env.add_indexed(10)
        _, binding = await env.bound()
        first = await env.reader.check_used_references(
            binding, env.scope(), (env.ref(row, context_hash="1" * 64),))
        second = await env.reader.check_used_references(
            binding, env.scope(), (env.ref(row, context_hash="2" * 64),))
        assert first is not None and second is not None

    run(tmp_path, body)


def test_read_and_check_do_not_scan_corpus_or_scale_with_source_size(tmp_path, monkeypatch):
    async def body(env):
        rows = [env.add_indexed(100 + n, f"nội dung {n}") for n in range(5)]
        forbid_corpus_and_io_reads = lambda *a, **k: (_ for _ in ()).throw(  # noqa: E731
            AssertionError("full corpus scan / count parity is not authority"))
        for name in ("scroll", "count"):
            monkeypatch.setattr(env.vectors.client, name, forbid_corpus_and_io_reads)
        monkeypatch.setattr(env.vectors, "reference_ids", forbid_corpus_and_io_reads)
        monkeypatch.setattr(env.vectors, "count", forbid_corpus_and_io_reads)
        env.statements.clear()
        observation = await env.reader.read_source_binding(CHAT)
        small = len(env.statements)
        assert observation is not None
        for n in range(60):
            env.add_indexed(1000 + n, f"thêm {n}")
        env.statements.clear()
        assert await env.reader.read_source_binding(CHAT) is not None
        assert len(env.statements) == small
        assert len(rows) == 5

    run(tmp_path, body)


# --------------------------------------------------------------------------- qualification


def test_authorized_selection_without_usable_index_has_no_binding(tmp_path):
    async def body(env):
        assert await env.reader.read_source_binding(CHAT) is None  # no indexed rows
        env.add_indexed(10, point=False)  # SQL says indexed but no actual point
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


@pytest.mark.parametrize("state", ["reserved", "submitted", "uncertain", None])
def test_pending_uncertain_or_missing_embedding_intent_does_not_qualify(tmp_path, state):
    async def body(env):
        env.add_indexed(10, state=state)
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


@pytest.mark.parametrize("mutation", ["blocked", "mode_off", "registry_missing",
                                      "registry_path", "registry_state", "profile_marker"])
def test_initial_generation_needs_current_actual_registry_and_source_authorization(
        tmp_path, mutation):
    async def body(env):
        env.add_indexed(10)
        if mutation == "blocked":
            env.update(TelegramChatPolicy.__table__, TelegramChatPolicy.chat_id == CHAT,
                       allowed=False, authorization_epoch=2)
        elif mutation == "mode_off":
            env.update(TelegramChatPolicy.__table__, TelegramChatPolicy.chat_id == CHAT,
                       ai_mode="off")
        elif mutation == "registry_missing":
            with env.engine.begin() as connection:
                connection.execute(delete(VectorStore))
        elif mutation == "registry_path":
            env.update(VectorStore.__table__, VectorStore.store_id == env.profile.store_id,
                       path=str(env.tmp / "elsewhere"))
        elif mutation == "registry_state":
            env.update(VectorStore.__table__, VectorStore.store_id == env.profile.store_id,
                       state="reconciliation_required")
        else:
            (env.settings.data_dir / ".tg-assistant-data").unlink()
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


@pytest.mark.parametrize("wrong", ["other_model", "other_dimension", "legacy_no_profile",
                                   "other_path_same_profile"])
def test_wrong_published_store_model_dimension_or_path_never_qualifies(tmp_path, wrong):
    async def body(env):
        env.add_indexed(10)
        if wrong == "other_model":
            other = env.settings.model_copy(update={"ollama_embedding_model": "other-model:1"})
            replacement = new_vectors(other, env.tmp / "wrong-model")
        elif wrong == "other_dimension":
            other = env.settings.model_copy(update={"ollama_vector_size": 4})
            replacement = LocalVectorStore(env.tmp / "wrong-dim", vector_size=4,
                                           profile=other.embedding_profile)
        elif wrong == "legacy_no_profile":
            replacement = LocalVectorStore(env.tmp / "legacy", vector_size=3)
        else:
            replacement = new_vectors(env.settings, env.tmp / "same-profile-other-path")
        env.closed_vectors.append(replacement)
        env.runtime.rag.vectors = replacement  # borrowed owner differs from selected registry/path
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


def test_unpublished_staged_vectors_with_matching_counts_never_qualify(tmp_path):
    async def body(env):
        env.add_indexed(10)
        staged, path, record = env.stage_generation()
        assert staged.count(chat_id=CHAT) == env.vectors.count(chat_id=CHAT) == 1
        # Neither owned: pointer absent and published vectors are the old instance.
        observation = await env.reader.read_source_binding(CHAT)
        assert observation is not None and observation.active_index_generation == 0
        # Staged owner substituted without a committed pointer/registry path.
        env.runtime.rag.vectors = staged
        assert await env.reader.read_source_binding(CHAT) is None
        env.runtime.rag.vectors = env.vectors
        # Pointer committed but staged instance still unpublished: not the actual owner.
        env.commit_pointer(path, record)
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


def test_recovery_publishing_same_staged_instance_qualifies_only_after_publication(tmp_path):
    async def body(env):
        row = env.add_indexed(10)
        old_observation, old_binding = await env.bound()
        old_checked = await env.checked(old_binding, row)
        assert old_checked is not None
        staged, path, record = env.stage_generation()
        env.commit_pointer(path, record)
        env.publish(staged)  # same instance now owns the active pointer/path generation
        assert env.current(old_binding, old_checked) is False
        assert env.reader.bind_current(old_observation, old_binding) is False
        assert await env.checked(old_binding, row) is None
        fresh = await env.reader.read_source_binding(CHAT)
        assert fresh is not None and fresh.active_index_generation == 1
        assert fresh.vector_owner_fingerprint != old_observation.vector_owner_fingerprint
        binding = env.binding(fresh)
        assert env.reader.bind_current(fresh, binding) is True
        assert await env.checked(binding, row) is not None

    run(tmp_path, body)


def test_reopened_equal_corpus_is_a_new_incarnation(tmp_path):
    async def body(env):
        row = env.add_indexed(10)
        observation, binding = await env.bound()
        checked = await env.checked(binding, row)
        env.runtime.rag.vectors.close()
        reopened = LocalVectorStore(env.path, vector_size=3, profile=env.profile,
                                    require_existing=True)
        env.closed_vectors.append(reopened)
        env.vectors = env.runtime.rag.vectors = reopened
        assert env.reader.bind_current(observation, env.binding(observation)) is False
        assert await env.checked(binding, row) is None
        assert env.current(binding, checked) is False

    run(tmp_path, body)


# --------------------------------------------------------------------------- authority


def test_copied_cloned_or_equal_objects_grant_no_authority(tmp_path):
    async def body(env):
        row = env.add_indexed(10)
        observation = await env.reader.read_source_binding(CHAT)
        clones = [copy.copy(observation), copy.deepcopy(observation),
                  dataclasses.replace(observation), pickle.loads(pickle.dumps(observation))]  # noqa: S301 - trusted test-created roundtrip
        binding = env.binding(observation)
        for clone in clones:
            assert env.reader.bind_current(clone, binding) is False
        assert await env.checked(binding, row) is None  # never bound by this reader
        assert env.reader.bind_current(observation, binding) is True
        # Only the exact retained binding instance is accepted, not an equal clone.
        equal_binding = dataclasses.replace(binding)
        assert equal_binding == binding and equal_binding is not binding
        assert await env.checked(equal_binding, row) is None
        checked = await env.checked(binding, row)
        assert checked is not None
        for clone in (copy.copy(checked), copy.deepcopy(checked), dataclasses.replace(checked)
                      if dataclasses.is_dataclass(checked) else copy.copy(checked)):
            assert env.current(binding, clone) is False
        assert env.current(equal_binding, checked) is False
        # checked=None is the pre-provider check: only the exact retained binding passes.
        assert env.current(equal_binding, None) is False
        assert env.current(binding, None) is True
        assert env.current(binding, checked) is True

    run(tmp_path, body)


def test_binding_with_any_measured_field_mismatch_is_not_retained(tmp_path):
    async def body(env):
        env.add_indexed(10)
        observation = await env.reader.read_source_binding(CHAT)
        wrong = {
            "embedding_store_id": "embedding-other",
            "embedding_model": "other-model",
            "embedding_dimension": 4,
            "embedding_provider": "openai",
            "embedding_model_version": "other-v9",
            "embedding_endpoint_id": "endpoint-other",
            "source_epoch": 99,
            "source_policy_fingerprint": "f" * 64,
            "active_index_generation": 7,
            "vector_owner_fingerprint": "e" * 64,
            "retrieval_eligibility_fingerprint": "e" * 64,
            "selected_chat_id": OTHER_CHAT,
            "selection_generation": 99,
            "restore_epoch": "restore-other",
            "owner_id": OWNER + 1,
            "profile_id": "someone-else",
        }
        for name, value in wrong.items():
            assert env.reader.bind_current(observation, env.binding(observation, **{name: value})) \
                is False, name
        assert env.reader.bind_current(observation, env.binding(observation)) is True

    run(tmp_path, body)


def test_withdraw_invalidates_every_issued_object_and_close_is_terminal(tmp_path):
    async def body(env):
        row = env.add_indexed(10)
        observation, binding = await env.bound()
        checked = await env.checked(binding, row)
        pending = await env.reader.read_source_binding(CHAT)
        env.reader.withdraw()
        env.reader.withdraw()  # idempotent
        assert env.reader.bind_current(pending, env.binding(pending)) is False
        assert env.current(binding, checked) is False
        assert await env.checked(binding, row) is None
        await env.reader.close()
        assert await env.reader.read_source_binding(CHAT) is None
        assert env.reader.bind_current(observation, binding) is False

    run(tmp_path, body)


# --------------------------------------------------------------------------- exact references


def test_references_must_be_exact_selected_rows(tmp_path):
    async def body(env):
        env.add_source(OTHER_CHAT)
        row = env.add_indexed(10)
        other = env.add_indexed(11, chat_id=OTHER_CHAT)
        too_many = tuple(env.ref(env.add_indexed(200 + n, f"r{n}")) for n in range(9))
        eight = tuple(env.ref(env.add_indexed(300 + n, f"s{n}")) for n in range(8))
        _, binding = await env.bound()
        ok = await env.reader.check_used_references(binding, env.scope(), (env.ref(row),))
        assert ok is not None
        bad_refs = [
            (env.ref(row, message_id=99),),
            (env.ref(row, reference_id=row.row_id + 500),),
            (env.ref(row, content_hash="0" * 64),),
            (env.ref(row, reference_id=other.row_id, chat_id=CHAT, message_id=other.message_id,
                     content_hash=other.raw_hash),),
            (env.ref(other),),  # an indexed row of a different (also authorized) chat
            (env.ref(row), env.ref(row)),  # duplicate
            (),
        ]
        for used in bad_refs:
            assert await env.reader.check_used_references(binding, env.scope(), used) is None
        assert await env.reader.check_used_references(binding, env.scope(), too_many) is None
        assert await env.reader.check_used_references(binding, env.scope(), eight) is not None
        assert await env.reader.check_used_references(
            binding, env.scope(selected_chat_id=OTHER_CHAT), (env.ref(row),)) is None

    run(tmp_path, body)


def test_scope_window_and_sender_filters_are_enforced_on_actual_rows(tmp_path):
    async def body(env):
        old = datetime.now(UTC) - timedelta(days=3)
        row = env.add_indexed(10, sender_id=77, sent_at=old)
        _, binding = await env.bound()
        base = dict(before=datetime.now(UTC) + timedelta(hours=1))
        assert await env.reader.check_used_references(
            binding, env.scope(**base, sender_id=77), (env.ref(row),)) is not None
        assert await env.reader.check_used_references(
            binding, env.scope(**base, sender_id=78), (env.ref(row),)) is None
        assert await env.reader.check_used_references(
            binding, env.scope(after=datetime.now(UTC) - timedelta(days=1), **base),
            (env.ref(row),)) is None
        assert await env.reader.check_used_references(
            binding, env.scope(before=old - timedelta(hours=1)), (env.ref(row),)) is None

    run(tmp_path, body)


# --------------------------------------------------------------------------- bounded rows


@pytest.mark.parametrize("kind", ["text_ascii", "text_multibyte", "metadata"])
def test_oversized_rows_are_refused_without_poisoning_other_references(tmp_path, kind):
    async def body(env):
        small = env.add_indexed(10)
        if kind == "text_ascii":
            big = env.add_indexed(11, "a" * (64 * 1024 + 1))
        elif kind == "text_multibyte":
            big = env.add_indexed(11, "é" * 40_000)  # 40k characters, 80k UTF-8 bytes
        else:
            big = env.add_indexed(11, metadata={"pad": "x" * (8 * 1024 + 1)})
        env.statements.clear()
        _, binding = await env.bound()
        assert await env.checked(binding, big) is None
        assert any("length(" in s.lower() for s in env.statements), (
            "raw SQL lengths must be measured before row/JSON materialization")
        assert await env.checked(binding, small) is not None

    run(tmp_path, body)


def test_rows_exactly_at_limits_are_accepted(tmp_path):
    async def body(env):
        row = env.add_indexed(10, "a" * (64 * 1024 - 16), metadata={"pad": "x" * 4000})
        _, binding = await env.bound()
        assert await env.checked(binding, row) is not None

    run(tmp_path, body)


# --------------------------------------------------------------------------- mutation


SOURCE_MUTATIONS = {
    "block": lambda e, r: e.update(TelegramChatPolicy.__table__, TelegramChatPolicy.chat_id == CHAT,
                                   allowed=False, authorization_epoch=2),
    "block_then_regrant": lambda e, r: (
        e.update(TelegramChatPolicy.__table__, TelegramChatPolicy.chat_id == CHAT,
                 allowed=False, authorization_epoch=2),
        e.update(TelegramChatPolicy.__table__, TelegramChatPolicy.chat_id == CHAT,
                 allowed=True, authorization_epoch=3)),
    "permission_revoked": lambda e, r: e.update(
        TelegramChatPermission.__table__, TelegramChatPermission.chat_id == CHAT, enabled=False),
    "ai_mode_off": lambda e, r: e.update(TelegramChatPolicy.__table__,
                                         TelegramChatPolicy.chat_id == CHAT, ai_mode="off"),
    "retention_expiry": lambda e, r: (
        e.update(TelegramChatPolicy.__table__, TelegramChatPolicy.chat_id == CHAT,
                 retention_days=1),
        e.update(TelegramMessage.__table__, TelegramMessage.id == r.row_id,
                 sent_at=datetime.now(UTC) - timedelta(days=3))),
    "text_edit": lambda e, r: e.update(TelegramMessage.__table__, TelegramMessage.id == r.row_id,
                                       text="Nội dung đã bị sửa"),
    "metadata_edit": lambda e, r: e.update(
        TelegramMessage.__table__, TelegramMessage.id == r.row_id,
        metadata_json={"embedding_store_id": "embedding-other",
                       "embedding_content_hash": r.digest,
                       "embedding_request_id": r.request_id}),
    "content_hash_column": lambda e, r: e.update(
        TelegramMessage.__table__, TelegramMessage.id == r.row_id, content_hash="9" * 64),
    "soft_delete": lambda e, r: e.update(TelegramMessage.__table__,
                                         TelegramMessage.id == r.row_id, is_deleted=True),
    "hard_delete": lambda e, r: _delete_row(e, r),
    "vector_dirty": lambda e, r: e.update(TelegramMessage.__table__,
                                          TelegramMessage.id == r.row_id,
                                          vector_status="pending", vector_dirty=True),
    "intent_uncertain": lambda e, r: e.update(
        AiBudgetReservation.__table__, AiBudgetReservation.request_id == r.request_id,
        state="uncertain"),
    "intent_deleted": lambda e, r: _delete_reservation(e, r),
    "registry_state": lambda e, r: e.update(
        VectorStore.__table__, VectorStore.store_id == e.profile.store_id,
        state="reconciliation_required"),
    "pointer_inserted": lambda e, r: _insert_pointer(e),
    "vector_upsert_unrelated": lambda e, r: e.vectors.upsert(
        9999, [0.0, 1.0, 0.0], chat_id=OTHER_CHAT, message_id=1),
    "vector_point_deleted": lambda e, r: e.vectors.delete_reference_ids([r.row_id]),
}


def _delete_row(env, row):
    with env.engine.begin() as connection:
        connection.execute(delete(TelegramMessage).where(TelegramMessage.id == row.row_id))


def _delete_reservation(env, row):
    with env.engine.begin() as connection:
        connection.execute(delete(AiBudgetReservation).where(
            AiBudgetReservation.request_id == row.request_id))


def _insert_pointer(env):
    with env.engine.begin() as connection:
        connection.execute(insert(AppSetting).values(
            key=pointer_key(env.profile.store_id), value={"generation": 1}))


@pytest.mark.parametrize("name", sorted(SOURCE_MUTATIONS))
def test_source_or_index_mutation_invalidates_checked_references(tmp_path, name):
    async def body(env):
        row = env.add_indexed(10)
        _, binding = await env.bound()
        checked = await env.checked(binding, row)
        assert checked is not None and env.current(binding, checked) is True
        SOURCE_MUTATIONS[name](env, row)
        assert env.current(binding, checked) is False
        if name not in {"vector_upsert_unrelated"}:
            assert await env.checked(binding, row) is None

    run(tmp_path, body)


def test_vector_mutation_during_awaited_check_is_refused(tmp_path, monkeypatch):
    async def body(env):
        row = env.add_indexed(10)
        _, binding = await env.bound()
        original = env.vectors.client.retrieve

        def mutating_retrieve(*args, **kwargs):
            result = original(*args, **kwargs)
            env.vectors.upsert(9998, [0.0, 0.0, 1.0], chat_id=OTHER_CHAT, message_id=2)
            return result

        monkeypatch.setattr(env.vectors.client, "retrieve", mutating_retrieve)
        assert await env.checked(binding, row) is None
        monkeypatch.setattr(env.vectors.client, "retrieve", original)
        assert await env.checked(binding, row) is None  # revision moved; old binding is stale

    run(tmp_path, body)


def test_unrelated_budget_options_and_settings_commits_keep_owner_and_index_current(tmp_path):
    async def body(env):
        row = env.add_indexed(10)
        _, binding = await env.bound()
        checked = await env.checked(binding, row)
        with env.engine.begin() as connection:
            connection.execute(insert(AppSetting).values(key="daily_budget_note", value=1))
            connection.execute(update(AppSetting).where(AppSetting.key == "daily_budget_note")
                               .values(value=2))
            connection.execute(insert(AiBudgetReservation).values(
                request_id="f" * 64, profile_id=PROFILE_ID, occurred_at=datetime.now(UTC),
                provider="ollama", model="chat", pricing_version="test-v1", pricing_rates={},
                operation="answer", feature="rag", route="local_rag", chat_id=CHAT,
                reserved_input_tokens=1, reserved_output_tokens=1, reserved_cost_usd=0,
                is_local=True, fallback_used=False, state="settled"))
        # Re-saving the same source is a coordinator commit, not a selection change.
        await env.first_value.select_source(CHAT)
        assert env.current(binding, checked) is True
        assert await env.checked(binding, row) is not None

    run(tmp_path, body)


@pytest.mark.parametrize("path", ["selection_a_b_a", "selection_b"])
def test_source_selection_changes_invalidate_even_when_returning_to_same_source(tmp_path, path):
    async def body(env):
        row = env.add_indexed(10)
        env.add_source(OTHER_CHAT)
        _, binding = await env.bound()
        checked = await env.checked(binding, row)
        await env.first_value.select_source(OTHER_CHAT)
        if path == "selection_a_b_a":
            await env.first_value.select_source(CHAT)
        assert env.current(binding, checked) is False
        assert await env.checked(binding, row) is None

    run(tmp_path, body)


def test_restore_epoch_rotation_invalidates(tmp_path):
    async def body(env):
        row = env.add_indexed(10)
        _, binding = await env.bound()
        checked = await env.checked(binding, row)
        key = env.first_value._key
        with env.engine.begin() as connection:
            value = connection.execute(
                AppSetting.__table__.select().where(AppSetting.key == key)).one().value
            connection.execute(update(AppSetting).where(AppSetting.key == key).values(
                value={**value, "restore_epoch": "rotated" + "0" * 25,
                       "revision": value["revision"] + 1}))
        assert env.current(binding, checked) is False

    run(tmp_path, body)


@pytest.mark.parametrize("replace", ["vectors", "rag", "lock", "settings", "settings_equal_copy",
                                     "first_value", "bot_runtime", "admission_lost"])
def test_borrowed_owner_replacement_or_lost_admission_invalidates(tmp_path, replace):
    async def body(env):
        row = env.add_indexed(10)
        _, binding = await env.bound()
        checked = await env.checked(binding, row)
        runtime = env.runtime
        if replace == "vectors":
            other = new_vectors(env.settings, env.tmp / "replacement")
            env.closed_vectors.append(other)
            runtime.rag.vectors = other
        elif replace == "rag":
            runtime.rag = SimpleNamespace(vectors=env.vectors)
        elif replace == "lock":
            runtime._knowledge_lock = asyncio.Lock()
        elif replace == "settings":
            runtime.settings = env.settings.model_copy(update={"cloud_consent": True})
        elif replace == "settings_equal_copy":
            runtime.settings = env.settings.model_copy()
        elif replace == "first_value":
            runtime.first_value = object()
        elif replace == "bot_runtime":
            runtime.bot_runtime = BotShell(runtime, env.engine, env.fence)
        else:
            runtime.admitted = False
        assert env.current(binding, checked) is False
        assert await env.checked(binding, row) is None
        if replace in {"admission_lost", "bot_runtime", "first_value"}:
            assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


def test_model_and_consent_changes_invalidate_without_trusting_equal_fields(tmp_path):
    async def body(env):
        row = env.add_indexed(10)
        _, binding = await env.bound()
        checked = await env.checked(binding, row)
        runtime = env.runtime
        runtime.settings = env.settings.model_copy(update={"ollama_embedding_model": "m2:latest"})
        assert env.current(binding, checked) is False

    run(tmp_path, body)


# --------------------------------------------------------------------------- freshness


def test_observation_and_checked_reference_ages_are_bounded(tmp_path, monkeypatch):
    async def body(env):
        row = env.add_indexed(10)
        real = time.monotonic
        offset = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: real() + offset[0])
        observation = await env.reader.read_source_binding(CHAT)
        binding = env.binding(observation)
        offset[0] = 31.0  # beyond the 30 second issued-observation age
        assert env.reader.bind_current(observation, binding) is False
        offset[0] = 0.0
        fresh = await env.reader.read_source_binding(CHAT)
        binding = env.binding(fresh)
        assert env.reader.bind_current(fresh, binding) is True
        checked = await env.checked(binding, row)
        offset[0] = 4.0
        assert env.current(binding, checked) is True
        offset[0] = 6.0  # beyond the 5 second checked-reference age
        assert env.current(binding, checked) is False

    run(tmp_path, body)


def test_checked_reference_age_is_capped_by_retention(tmp_path):
    async def body(env):
        env.update(TelegramChatPolicy.__table__, TelegramChatPolicy.chat_id == CHAT,
                   retention_days=1)
        near_expiry = datetime.now(UTC) - timedelta(days=1) + timedelta(seconds=1)
        row = env.add_indexed(10, sent_at=near_expiry)
        _, binding = await env.bound()
        checked = await env.checked(binding, row)
        assert checked is not None
        await asyncio.sleep(1.3)  # real retention boundary passes inside the 5 s age
        assert env.current(binding, checked) is False

    run(tmp_path, body)


# --------------------------------------------------------------------------- tasks / deadlines


def test_caller_cancellation_keeps_worker_owned_until_close_drains_it(tmp_path, monkeypatch):
    async def body(env):
        row = env.add_indexed(10)
        _, binding = await env.bound()
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = env.vectors.client.retrieve

        def blocked(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            try:
                return original(*args, **kwargs)
            finally:
                finished.set()

        monkeypatch.setattr(env.vectors.client, "retrieve", blocked)
        task = asyncio.create_task(env.checked(binding, row))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not finished.is_set()
        closing = asyncio.create_task(env.reader.close())
        await asyncio.sleep(0.3)
        assert not closing.done(), "close must drain the retained worker task"
        release.set()
        await asyncio.wait_for(closing, 5)
        assert finished.is_set()

    run(tmp_path, body)


def test_check_deadline_returns_none_but_worker_stays_owned(tmp_path, monkeypatch):
    async def body(env):
        row = env.add_indexed(10)
        _, binding = await env.bound()
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = env.vectors.client.retrieve

        def slow(*args, **kwargs):
            entered.set()
            release.wait(10)
            try:
                return original(*args, **kwargs)
            finally:
                finished.set()

        monkeypatch.setattr(env.vectors.client, "retrieve", slow)
        started = time.monotonic()
        assert await env.checked(binding, row) is None
        assert 1.5 <= time.monotonic() - started < 4.0  # 2 second check deadline
        assert entered.is_set() and not finished.is_set()
        closing = asyncio.create_task(env.reader.close())
        await asyncio.sleep(0.2)
        assert not closing.done()
        release.set()
        await asyncio.wait_for(closing, 5)
        assert finished.is_set()

    run(tmp_path, body)


# --------------------------------------------------------------------------- contract fixes


def current_policy_version(env, chat=CHAT):
    with env.engine.connect() as connection:
        return SourceIndexService.policy_version(connection.execute(
            select(TelegramChatPolicy.__table__).where(
                TelegramChatPolicy.__table__.c.chat_id == chat)).one())


def test_current_standard_policy_with_long_text_still_qualifies(tmp_path):
    async def body(env):
        env.update(TelegramChatPolicy, TelegramChatPolicy.chat_id == CHAT,
                   filtering_level="standard")
        env.add_indexed(10, text="Kế hoạch ra mắt sản phẩm vào thứ Sáu tuần sau")
        assert await env.reader.read_source_binding(CHAT) is not None

    run(tmp_path, body)


def test_strict_policy_denies_short_row_even_with_a_current_policy_version(tmp_path):
    async def body(env):
        row = env.add_indexed(10, text="abc")
        assert await env.reader.read_source_binding(CHAT) is not None  # relaxed policy
        env.update(TelegramChatPolicy, TelegramChatPolicy.chat_id == CHAT, filtering_level="strict")
        env.update(TelegramMessage, TelegramMessage.id == row.row_id, metadata_json={
            "embedding_store_id": env.profile.store_id, "embedding_content_hash": row.digest,
            "embedding_request_id": row.request_id,
            "embedding_policy_version": current_policy_version(env)})
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


@pytest.mark.parametrize("kwargs,text,media,expected", [
    ({"has": "link"}, "xem https://example.com/a ở đây nhé", False, True),
    ({"has": "link"}, "HTTP://example.com/a viết hoa cũng là liên kết", False, True),
    ({"has": "link"}, "không có đường dẫn nào ở đây cả", False, False),
    ({"has": "image"}, "ảnh kế hoạch ra mắt thứ Sáu", True, True),
    ({"has": "image"}, "ảnh kế hoạch ra mắt thứ Sáu", False, False),
    ({"has": "document"}, "tài liệu kế hoạch ra mắt", True, True),
    ({"has": "audio"}, "ghi âm kế hoạch ra mắt", False, False),
    ({"content_type": "task"}, "Cần chốt phương án ra mắt", False, True),
    ({"content_type": "task"}, "Kế hoạch ra mắt là thứ Sáu", False, False),
    ({"content_type": "decision"}, "Đã QUYẾT ĐỊNH ra mắt vào thứ Sáu", False, True),
    ({"content_type": "decision"}, "Kế hoạch ra mắt là thứ Sáu", False, False),
])
def test_scope_filters_match_search_service_on_the_actual_row(
        tmp_path, kwargs, text, media, expected):
    async def body(env):
        row = env.add_indexed(10, text=text, has_media=media)
        _, binding = await env.bound()
        result = await env.checked(binding, row, scope=env.scope(**kwargs))
        assert (result is not None) is expected

    run(tmp_path, body)


def test_unsupported_scope_filter_values_cannot_be_constructed(tmp_path):
    for kwargs in ({"has": "video"}, {"content_type": "summary"}):
        with pytest.raises(ValueError):
            RetrievalScope(selected_chat_id=CHAT, after=None, before=datetime.now(UTC),
                           sender_id=None, has=kwargs.get("has"),
                           content_type=kwargs.get("content_type"), query_route="local_rag",
                           embedding_route="cloud_embedding")


def test_scope_before_is_inclusive(tmp_path):
    async def body(env):
        sent = datetime.now(UTC) - timedelta(minutes=5)
        row = env.add_indexed(10, sent_at=sent)
        _, binding = await env.bound()
        assert await env.checked(binding, row, scope=env.scope(before=sent)) is not None
        assert await env.checked(
            binding, row, scope=env.scope(before=sent - timedelta(microseconds=1))) is None

    run(tmp_path, body)


@pytest.mark.parametrize("values", [
    {"pricing_rates": {"pad": "x" * 9000}},
    {"cached_tokens": -1},
    {"cached_tokens": 11},
    {"actual_input_tokens": None},
    {"submitted_at": datetime.now(UTC) + timedelta(days=1)},
    {"settled_at": datetime.now(UTC) + timedelta(days=1)},
    {"occurred_at": datetime.now(UTC) + timedelta(days=1)},
    {"pricing_version": "another-version"},
    {"route": "local_rag"},
    {"feature": "answer"},
])
def test_settled_embedding_ledger_chronology_counts_and_snapshot_must_cohere(tmp_path, values):
    async def body(env):
        row = env.add_indexed(10)
        env.update(AiBudgetReservation, AiBudgetReservation.request_id == row.request_id, **values)
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


def test_local_snapshot_with_a_nonzero_rate_is_refused(tmp_path):
    async def body(env):
        row = env.add_indexed(10)
        rates = PricingSnapshot(env.profile.provider, env.profile.model, "local-zero-v1", "local",
                                Decimal(1), Decimal(0), Decimal(0), Decimal(0),
                                datetime(9999, 1, 1, tzinfo=UTC)).as_dict()
        env.update(AiBudgetReservation, AiBudgetReservation.request_id == row.request_id,
                   pricing_rates=rates)
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


def test_vector_owner_fingerprint_hashes_the_full_observation_token(tmp_path):
    async def body(env):
        env.add_indexed(10)
        first = await env.reader.read_source_binding(CHAT)
        env.add_indexed(11)
        second = await env.reader.read_source_binding(CHAT)
        assert first.active_index_generation == second.active_index_generation
        assert first.vector_owner_fingerprint != second.vector_owner_fingerprint

    run(tmp_path, body)


def test_pre_provider_check_needs_only_one_unchanged_initial_witness(tmp_path):
    async def body(env):
        first, second = env.add_indexed(10), env.add_indexed(11)
        _, binding = await env.bound()
        assert env.current(binding, None) is True
        env.update(TelegramMessage, TelegramMessage.id == first.row_id, is_deleted=True)
        assert env.current(binding, None) is True
        env.update(TelegramMessage, TelegramMessage.id == second.row_id, is_deleted=True)
        assert env.current(binding, None) is False

    run(tmp_path, body)


def test_final_checks_do_no_disk_io(tmp_path, monkeypatch):
    module = load_module()

    async def body(env):
        row = env.add_indexed(10)
        _, binding = await env.bound()
        checked = await env.checked(binding, row)

        def refuse(*args, **kwargs):
            raise AssertionError("final transaction must not touch the disk")

        monkeypatch.setattr(module, "_guard", refuse)
        monkeypatch.setattr(module, "_read_json", refuse)
        assert env.current(binding, None) is True
        assert env.current(binding, checked) is True

    run(tmp_path, body)


def _known_file(env, target):
    if target == "marker":
        return env.settings.data_dir / ".tg-assistant-data"
    return env.path / "embedding-profile.json"


@pytest.mark.parametrize("target", ["marker", "manifest"])
def test_hardlinked_known_file_is_refused(tmp_path, target):
    async def body(env):
        env.add_indexed(10)
        assert await env.reader.read_source_binding(CHAT) is not None
        os.link(_known_file(env, target), env.tmp / f"alias-{target}")
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


@pytest.mark.parametrize("target", ["marker", "manifest"])
def test_symlinked_known_file_is_refused(tmp_path, target):
    async def body(env):
        env.add_indexed(10)
        assert await env.reader.read_source_binding(CHAT) is not None
        path, real = _known_file(env, target), env.tmp / f"real-{target}"
        path.replace(real)
        try:
            os.symlink(real, path)
        except OSError as error:
            real.replace(path)
            if os.name != "nt" or getattr(error, "winerror", None) != 1314:
                raise
            pytest.skip("file symlinks need a privilege this account does not have")
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


def test_ownership_refusal_on_a_known_path_is_honoured(tmp_path, monkeypatch):
    from tg_assistant.desktop import instance

    async def body(env):
        env.add_indexed(10)
        assert await env.reader.read_source_binding(CHAT) is not None
        real, marker = instance._assert_owned_path, _known_file(env, "marker")

        def foreign(path):
            if Path(path) == marker:
                raise OSError("storage_access_denied")
            return real(path)

        monkeypatch.setattr(instance, "_assert_owned_path", foreign)
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)
