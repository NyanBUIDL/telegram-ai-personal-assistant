"""Bounded history tests use migrated files and independent real transactions."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import create_engine, delete, select, text

from alembic import command
from tg_assistant.db.base import Database
from tg_assistant.db.models import (
    SyncState,
    TelegramChat,
    TelegramChatPolicy,
    TelegramMessage,
    TelegramMessageVersion,
)
from tg_assistant.policy import PolicyEngine
from tg_assistant.telegram.user_client import UserClientAdapter


class HistoryClient:
    def __init__(self, ids):
        self.ids = list(ids)
        self.calls = []
        self.during_fetch = None

    async def iter_messages(self, chat_id, *, min_id=0, max_id=0, reverse=False, limit=1000):
        self.calls.append((min_id, max_id, reverse, limit))
        if self.during_fetch:
            await self.during_fetch()
        ids = sorted(
            (i for i in self.ids if i > min_id and (not max_id or i < max_id)), reverse=not reverse
        )
        for i in ids[:limit]:
            yield SimpleNamespace(
                id=i,
                chat_id=chat_id,
                message=f"message {i}",
                date=datetime.now(UTC),
                edit_date=None,
                media=None,
                sender_id=2,
                reply_to=None,
                out=False,
            )


@pytest.fixture
def migrated_path(tmp_path):
    path = tmp_path / "history.db"
    engine = create_engine(f"sqlite:///{path}")
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    engine.dispose()
    return path


@pytest_asyncio.fixture
async def history(migrated_path):
    db = Database(f"sqlite+aiosqlite:///{migrated_path}")
    adapter = object.__new__(UserClientAdapter)
    adapter.database = db
    adapter.policy = PolicyEngine()
    adapter.client = HistoryClient(range(1, 1003))
    async with db.session() as session:
        session.add(TelegramChat(chat_id=100, title="History", chat_type="group"))
        await adapter.policy.apply_template(session, 100, "knowledge")
        session.add(SyncState(chat_id=100, last_message_id=1))
    yield db, adapter
    await db.close()


@pytest.mark.asyncio
async def test_incremental_page_does_not_jump_completed_watermark(history):
    db, adapter = history
    async with db.session() as session:
        assert (
            await adapter.sync_history(session, chat_id=100, actor_id=1, owner_id=1, limit=1000)
            == 1000
        )
    async with db.session() as session:
        state = await session.scalar(select(SyncState))
        assert state.last_message_id == 1
        assert list(
            await session.scalars(
                select(TelegramMessage.message_id).order_by(TelegramMessage.message_id)
            )
        ) == list(range(2, 1002))


async def stage(db, adapter, limit=1000, **kwargs):
    async with db.session() as session:
        return await adapter.stage_history_page(
            session, chat_id=100, actor_id=1, owner_id=1, limit=limit, **kwargs
        )


async def apply(db, adapter, staged):
    async with db.session() as session:
        return await adapter.apply_history_page(session, staged)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [True, False, 0, -1, 1.0, "1", None, 100001])
async def test_invalid_limit_has_zero_fetch_and_mutation(history, limit):
    db, adapter = history
    with pytest.raises(ValueError, match="invalid_history_limit"):
        await stage(db, adapter, limit)
    assert adapter.client.calls == []
    async with db.session() as session:
        assert (await session.scalar(select(SyncState))).last_message_id == 1
        assert not list(await session.scalars(select(TelegramMessage)))


@pytest.mark.asyncio
async def test_initial_latest_three_and_exhaustion(history):
    db, adapter = history
    adapter.client.ids = list(range(1, 7))
    async with db.session() as session:
        await session.execute(delete(SyncState))
    first = await apply(db, adapter, await stage(db, adapter, 3))
    assert first.saved == 3 and not first.completed
    async with db.session() as session:
        state = await session.scalar(select(SyncState))
        assert (
            state.baseline_message_id,
            state.catchup_upper_id,
            state.catchup_after_id,
            state.last_message_id,
        ) == (4, 6, 6, None)
        assert set(await session.scalars(select(TelegramMessage.message_id))) == {4, 5, 6}
    assert (await apply(db, adapter, await stage(db, adapter, 3))).completed
    async with db.session() as session:
        state = await session.scalar(select(SyncState))
        assert state.last_message_id == 6 and state.last_message_date is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("backlog", [1001, 2501])
async def test_backlog_restart_and_arrivals_keep_original_upper(history, backlog):
    db, adapter = history
    adapter.client.ids = list(range(1, backlog + 2))
    result = await apply(db, adapter, await stage(db, adapter))
    assert result.upper_id == backlog + 1 and not result.completed
    adapter.client.ids += [backlog + 100, backlog + 1000]
    while not result.completed:
        result = await apply(db, adapter, await stage(db, adapter))
    async with db.session() as session:
        state = await session.scalar(select(SyncState))
        assert state.last_message_id == backlog + 1 and state.baseline_message_id is None
        assert set(await session.scalars(select(TelegramMessage.message_id))) == set(
            range(2, backlog + 2)
        )


@pytest.mark.asyncio
async def test_sparse_ids_and_large_initial_depth(history):
    db, adapter = history
    adapter.client.ids = [i * 3 for i in range(1, 2601)]
    async with db.session() as session:
        await session.execute(delete(SyncState))
    staged = await stage(db, adapter, 1500)
    assert len(staged.messages) == 1000 and staged.baseline == 3303 and staged.upper == 7800
    first = await apply(db, adapter, staged)
    assert not first.completed
    second = await apply(db, adapter, await stage(db, adapter, 500, check_exhaustion=True))
    assert second.completed and second.saved == 500
    async with db.session() as session:
        assert set(await session.scalars(select(TelegramMessage.message_id))) == set(
            adapter.client.ids[-1500:]
        )


@pytest.mark.asyncio
async def test_two_independent_stages_one_cas_winner(history):
    db, adapter = history
    first, second = await stage(db, adapter, 2), await stage(db, adapter, 3)
    await apply(db, adapter, first)
    with pytest.raises(RuntimeError, match="history_sync_conflict"):
        await apply(db, adapter, second)
    async with db.session() as session:
        assert set(await session.scalars(select(TelegramMessage.message_id))) == {2, 3}
        assert (await session.scalar(select(SyncState))).catchup_after_id == 3


@pytest.mark.asyncio
async def test_first_state_race_and_rollback(history):
    db, adapter = history
    async with db.session() as session:
        await session.execute(delete(SyncState))
    first, second = await stage(db, adapter, 2), await stage(db, adapter, 3)
    with pytest.raises(RuntimeError, match="commit failed"):
        async with db.session() as session:
            await adapter.apply_history_page(session, first)
            raise RuntimeError("commit failed")
    await apply(db, adapter, second)
    with pytest.raises(RuntimeError, match="history_sync_conflict"):
        await apply(db, adapter, first)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["pending", "flushed", "explicit"])
async def test_no_network_under_existing_writer(history, kind):
    db, adapter = history
    async with db.session() as session:
        if kind == "explicit":
            await session.execute(text("BEGIN IMMEDIATE"))
        else:
            state = await session.scalar(select(SyncState))
            state.error = "unrelated"
            if kind == "flushed":
                await session.flush()
        with pytest.raises(RuntimeError, match="history_sync_requires_read_stage"):
            await adapter.sync_history(session, chat_id=100, actor_id=1, owner_id=1)
    assert not adapter.client.calls


@pytest.mark.asyncio
async def test_other_writer_can_commit_during_network(history):
    db, adapter = history

    async def competing_writer():
        async with db.session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))

    adapter.client.during_fetch = competing_writer
    await apply(db, adapter, await stage(db, adapter, 3))


@pytest.mark.asyncio
@pytest.mark.parametrize("quota", [0, 2, None])
async def test_quota_stop_preserves_first_unsaved_id(history, quota):
    db, adapter = history
    adapter.client.ids = [2, 3, 4]
    async with db.session() as session:
        (await session.scalar(select(TelegramChatPolicy))).max_messages = quota
    result = await apply(db, adapter, await stage(db, adapter, 10))
    assert result.saved == (3 if quota is None else quota)
    assert result.completed is (quota is None)
    if quota is not None:
        assert result.stop_reason == "quota" and result.after_id == 1 + quota
        async with db.session() as session:
            (await session.scalar(select(TelegramChatPolicy))).max_messages = None
        assert (await apply(db, adapter, await stage(db, adapter, 10))).completed
    async with db.session() as session:
        assert set(await session.scalars(select(TelegramMessage.message_id))) == {2, 3, 4}


@pytest.mark.asyncio
async def test_current_retention_skips_and_current_quota_override_staged_policy(history):
    db, adapter = history
    staged = await stage(db, adapter, 3)
    for message in staged.messages:
        message.date = datetime.now(UTC) - timedelta(days=10)
    async with db.session() as session:
        policy = await session.scalar(select(TelegramChatPolicy))
        policy.retention_days, policy.max_messages = 1, 0
    result = await apply(db, adapter, staged)
    assert (
        result.saved == 0 and result.skipped == 3 and result.after_id == 4 and not result.completed
    )
    async with db.session() as session:
        assert not list(await session.scalars(select(TelegramMessage)))


@pytest.mark.asyncio
async def test_block_regrant_invalidates_stage_but_new_request_can_resume(history):
    from tg_assistant.services.revocation import AuthorizationRevoked

    db, adapter = history
    await apply(db, adapter, await stage(db, adapter, 2))
    stale = await stage(db, adapter, 2)
    async with db.session() as session:
        await adapter.policy.set_allowed(session, 100, False)
    async with db.session() as session:
        await adapter.policy.apply_template(session, 100, "knowledge")
    with pytest.raises(AuthorizationRevoked):
        await apply(db, adapter, stale)
    result = await apply(db, adapter, await stage(db, adapter, 2))
    assert result.saved == 2
    async with db.session() as session:
        state = await session.scalar(select(SyncState))
        assert state.last_message_id == 1 and state.authorization_epoch != stale.epoch


@pytest.mark.asyncio
async def test_stale_active_epoch_cannot_refresh_itself(history):
    from tg_assistant.services.revocation import AuthorizationRevoked

    db, adapter = history
    await apply(db, adapter, await stage(db, adapter, 2))
    async with db.session() as session:
        (await session.scalar(select(TelegramChatPolicy))).authorization_epoch += 1
    adapter.client.calls.clear()
    with pytest.raises(AuthorizationRevoked):
        await stage(db, adapter)
    assert not adapter.client.calls


@pytest.mark.asyncio
async def test_edit_versions_dirty_and_cursor_rollback_together(history):
    import asyncio

    db, adapter = history
    async with db.session() as session:
        session.add(
            TelegramMessage(
                chat_id=100,
                message_id=2,
                text="old",
                sent_at=datetime.now(UTC),
                vector_dirty=False,
                content_hash="old",
            )
        )
    staged = await stage(db, adapter, 1)
    with pytest.raises(asyncio.CancelledError):
        async with db.session() as session:
            await adapter.apply_history_page(session, staged)
            await session.flush()
            raise asyncio.CancelledError()
    async with db.session() as session:
        row = await session.scalar(select(TelegramMessage))
        assert row.text == "old" and not row.vector_dirty and row.content_hash == "old"
        assert not list(await session.scalars(select(TelegramMessageVersion)))
        assert (await session.scalar(select(SyncState))).last_message_id == 1


@pytest.mark.asyncio
async def test_sql_text_length_edit_undelete_and_attached_media_usage(history):
    from tg_assistant.db.models import TelegramAttachment

    db, adapter = history
    async with db.session() as session:
        row = TelegramMessage(
            chat_id=100, message_id=2, text="old", sent_at=datetime.now(UTC), is_deleted=True
        )
        session.add(row)
        await session.flush()
        session.add(
            TelegramAttachment(
                telegram_message_id=row.id, file_name="a", size_bytes=1024 * 1024 - 2
            )
        )
        policy = await session.scalar(select(TelegramChatPolicy))
        policy.max_storage_mb, policy.max_messages = 1, 1
    staged = await stage(db, adapter, 2)
    staged.messages[0].message = "ĐĐ\x00ignored suffix"
    result = await apply(db, adapter, staged)
    assert result.saved == 1 and result.stop_reason == "quota" and result.after_id == 2
    async with db.session() as session:
        row = await session.scalar(select(TelegramMessage))
        assert not row.is_deleted and row.text == "ĐĐ\x00ignored suffix"


async def learning_case(history, *, limit=2, attempts=2):
    import asyncio

    from tg_assistant.db.models import BackgroundJob
    from tg_assistant.runtime import Application

    db, adapter = history
    adapter.owner_id = 1
    app = object.__new__(Application)
    app.database, app.user, app.policy = db, adapter, adapter.policy
    app._knowledge_lock = asyncio.Lock()
    app.rag = SimpleNamespace(vectors=SimpleNamespace(count=lambda **kwargs: 0))
    app.settings = SimpleNamespace(ai_provider="ollama")
    app.embedding_ai = SimpleNamespace(available=True)
    async with db.session() as session:
        epoch = (await session.scalar(select(TelegramChatPolicy))).authorization_epoch
        job = BackgroundJob(
            job_type="learn_group",
            attempts=attempts,
            max_attempts=3,
            payload={"chat_id": 100, "owner_id": 1, "authorization_epoch": epoch, "limit": limit},
        )
        session.add(job)
        await session.flush()
        job_id = job.id
    return app, job_id


@pytest.mark.asyncio
async def test_learning_continuations_refund_only_healthy_claim_keep_token_and_prior_failures(
    history,
):
    from tg_assistant.db.models import BackgroundJob, KnowledgeSource
    from tg_assistant.services.jobs import LeaseLost, claim_runtime_job, worker_lease

    app, job_id = await learning_case(history)
    tokens = []
    for page_number in range(4):
        claimed = await claim_runtime_job(app.database, ("learn_group",))
        lease, _ = claimed
        assert lease.claim_token not in tokens
        tokens.append(lease.claim_token)
        with worker_lease(lease):
            await app._run_learning_lease(lease, 0)
        async with app.database.session() as session:
            job = await session.get(BackgroundJob, job_id)
            assert job.status == "queued" and job.attempts == 2
            assert job.claim_token == lease.claim_token and job.lease_expires_at is not None
            assert job.payload["history_completed"] is False
            assert job.payload["synced_count"] == (page_number + 1) * 2
            assert job.payload["phase"] == "syncing" and job.payload["total"] is None
            source = await session.get(KnowledgeSource, 100)
            assert source.requested_for_learning and source.last_learned_at is None
            job.run_after = None
        with pytest.raises(LeaseLost):
            with worker_lease(lease):
                async with app.database.session() as session:
                    (await session.get(BackgroundJob, job_id)).last_error = "obsolete writer"

    async def failing_fetch():
        raise RuntimeError("synthetic SDK failure")

    app.user.client.during_fetch = failing_fetch
    await app._process_learning_jobs()
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, job_id)
        assert job.status == "failed" and job.attempts == 3
        assert job.payload["synced_count"] == 8


@pytest.mark.asyncio
async def test_learning_quota_pauses_without_embedding_and_resume_keeps_failures(history):
    from tg_assistant.db.models import BackgroundJob, KnowledgeSource
    from tg_assistant.runtime import resume_learning_jobs

    app, job_id = await learning_case(history)
    async with app.database.session() as session:
        (await session.scalar(select(TelegramChatPolicy))).max_messages = 0
    await app._process_learning_jobs()
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, job_id)
        assert job.status == "paused" and job.attempts == 2 and job.paused_at
        assert job.last_error == "history_quota_reached"
        assert job.payload["history_completed"] is False
        source = await session.get(KnowledgeSource, 100)
        assert source.status == "paused" and source.requested_for_learning
        await resume_learning_jobs(session)
        assert job.attempts == 2 and job.status == "queued"


@pytest.mark.asyncio
async def test_learning_completed_payload_never_starts_another_interval(history, monkeypatch):
    from tg_assistant.db.models import BackgroundJob

    app, job_id = await learning_case(history)
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, job_id)
        job.payload = {**job.payload, "history_completed": True, "synced_count": 7}

    async def forbidden(*args, **kwargs):
        raise AssertionError("history restarted")

    monkeypatch.setattr(app.user, "stage_history_page", forbidden)
    app.settings.embedding_profile = SimpleNamespace(store_id="synthetic")
    embedded = []

    async def entered_embedding(*args, **kwargs):
        embedded.append(True)
        raise RuntimeError("synthetic embedding boundary")

    monkeypatch.setattr(app, "_index_knowledge_rows", entered_embedding)
    await app._process_learning_jobs()
    assert embedded == [True]
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, job_id)
        assert job.payload["synced_count"] == 7 and "history restarted" not in job.last_error
    assert app.user.client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("withdrawal", ["lease", "block"])
async def test_learning_network_withdrawal_cannot_publish_page(history, withdrawal):
    from tg_assistant.db.models import BackgroundJob

    app, job_id = await learning_case(history, attempts=0)

    async def withdraw():
        app.user.client.during_fetch = None
        async with app.database.session() as session:
            if withdrawal == "lease":
                (await session.get(BackgroundJob, job_id)).lease_expires_at = datetime.now(
                    UTC
                ) - timedelta(seconds=1)
            else:
                await app.policy.set_allowed(session, 100, False)

    app.user.client.during_fetch = withdraw
    await app._process_learning_jobs()
    async with app.database.session() as session:
        assert not list(await session.scalars(select(TelegramMessage)))
        assert (await session.scalar(select(SyncState))).last_message_id == 1
        assert not (await session.get(BackgroundJob, job_id)).payload.get("history_completed")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["completed", "partial", "quota", "cancelled", "final_fence"])
async def test_direct_one_use_action_truthful_atomic_outcome(history, monkeypatch, case):
    import asyncio

    from tg_assistant.db.models import AuditLog, PendingAction
    from tg_assistant.services.actions import PendingActionService
    from tg_assistant.services.revocation import AuthorizationRevoked

    app, _ = await learning_case(history, attempts=0)
    app.stopping, app.bot = asyncio.Event(), None
    app.user.client.ids = [2, 3] if case == "completed" else [2, 3, 4]
    async with app.database.session() as session:
        action = await PendingActionService().create(
            session,
            action_type="sync_chat_history",
            requested_by=1,
            chat_id=100,
            payload={"limit": 2},
        )
        action.status, action.confirmed_at = "confirmed", datetime.now(UTC)
        action_id = action.action_id
        if case == "quota":
            (await session.scalar(select(TelegramChatPolicy))).max_messages = 1
    if case == "cancelled":

        async def cancel_action():
            app.user.client.during_fetch = None
            async with app.database.session() as session:
                (await session.get(PendingAction, action_id)).status = "cancelled"

        app.user.client.during_fetch = cancel_action
    if case == "final_fence":
        original = app.user.apply_history_page

        async def rejecting_apply(session, staged):
            await original(session, staged)
            raise AuthorizationRevoked("synthetic final fence")

        monkeypatch.setattr(app.user, "apply_history_page", rejecting_apply)

    async def end_cycle(_):
        app.stopping.set()

    monkeypatch.setattr("tg_assistant.runtime.asyncio.sleep", end_cycle)
    await app._execute_actions()
    async with app.database.session() as session:
        action = await session.get(PendingAction, action_id)
        ids = set(await session.scalars(select(TelegramMessage.message_id)))
        successes = list(
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.correlation_id == action_id, AuditLog.outcome == "success"
                )
            )
        )
        if case == "completed":
            assert (
                action.status == "executed"
                and action.payload["history_completed"]
                and ids == {2, 3}
                and successes
            )
        elif case in {"cancelled", "final_fence"}:
            assert action.status == "cancelled" and ids == set() and not successes
        else:
            assert (
                action.status == "failed"
                and not action.payload["history_completed"]
                and not successes
            )
            assert ids == ({2} if case == "quota" else {2, 3})
            assert "Đã lưu bền vững" in action.error
            assert action.executed_at is None


@pytest.mark.asyncio
async def test_sdk_messageempty_discovery_counts_yielded_messages(history):
    from telethon.client.messages import MessageMethods
    from telethon.tl import types

    db, adapter = history
    now = datetime.now(UTC)
    visible = [i * 3 for i in range(1, 2101) if i % 7]

    class SyntheticSDK:
        iter_messages = MessageMethods.iter_messages

        def __init__(self):
            self.requests = []
            self.nodes = []
            for i in range(2100, 0, -1):
                if i % 7 == 0:
                    self.nodes.append(types.MessageEmpty(id=i * 3, peer_id=types.PeerUser(100)))
                else:
                    self.nodes.append(
                        SimpleNamespace(id=i * 3, date=now, _finish_init=lambda *args: None)
                    )

        async def get_input_entity(self, entity):
            return types.InputPeerUser(100, 0)

        async def __call__(self, request):
            self.requests.append((request.offset_id, request.add_offset, request.limit))
            index = (
                next(
                    (index for index, node in enumerate(self.nodes) if node.id < request.offset_id),
                    len(self.nodes),
                )
                if request.offset_id
                else 0
            )
            start = max(0, index + request.add_offset)
            return types.messages.MessagesSlice(
                count=len(self.nodes),
                messages=self.nodes[start : start + request.limit],
                chats=[],
                users=[],
                topics=[],
            )

    sdk = SyntheticSDK()
    adapter.client = sdk
    async with db.session() as session:
        await session.execute(delete(SyncState))
    staged = await stage(db, adapter, 1500)
    assert staged.baseline == visible[-1500] and staged.upper == visible[-1]
    assert [message.id for message in staged.messages] == visible[-1500:-500]
    assert all(request[2] <= 100 for request in sdk.requests)
    assert len(staged.messages) == 1000


@pytest.mark.asyncio
async def test_actual_commit_rejection_rolls_back_tentative_page(history):
    from sqlalchemy import event

    db, adapter = history
    staged = await stage(db, adapter, 2)

    def reject_commit(session):
        raise RuntimeError("synthetic commit rejection")

    with pytest.raises(RuntimeError, match="synthetic commit rejection"):
        async with db.session() as session:
            page = await adapter.apply_history_page(session, staged)
            assert page.saved == 2
            event.listen(session.sync_session, "before_commit", reject_commit)
    async with db.session() as session:
        assert not list(await session.scalars(select(TelegramMessage)))
        assert (await session.scalar(select(SyncState))).catchup_upper_id is None


@pytest.mark.asyncio
async def test_empty_initial_and_zero_initial_capacity(history):
    db, adapter = history
    async with db.session() as session:
        await session.execute(delete(SyncState))
        (await session.scalar(select(TelegramChatPolicy))).max_messages = 0
    adapter.client.ids = [4, 5, 6]
    result = await apply(db, adapter, await stage(db, adapter, 3))
    assert not result.completed and result.after_id == 3 and result.saved == 0
    async with db.session() as session:
        state = await session.scalar(select(SyncState))
        assert state.baseline_message_id == 4 and state.last_message_id is None
        await session.execute(delete(SyncState))
    adapter.client.ids = []
    assert (await apply(db, adapter, await stage(db, adapter, 100000))).completed
    async with db.session() as session:
        state = await session.scalar(select(SyncState))
        assert state.baseline_message_id == state.last_message_id == 0


def test_migration_0009_upgrade_preserves_unknown_legacy_coverage(tmp_path):
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}")
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0009")
        connection.execute(
            text(
                "INSERT INTO sync_states(chat_id,last_message_id,last_message_date,state) VALUES(100,234,'2026-01-01 00:00:00','idle')"
            )
        )
        connection.commit()
        command.upgrade(config, "head")
        row = connection.execute(
            text(
                "SELECT last_message_id,last_message_date,baseline_message_id,catchup_upper_id,catchup_after_id,authorization_epoch FROM sync_states"
            )
        ).one()
        assert tuple(row) == (234, "2026-01-01 00:00:00", None, None, None, None)
    engine.dispose()


@pytest.mark.parametrize(
    "mode", ["complete", "quota", "commit_failure", "near_rate_limit", "revoked_after_page"]
)
def test_cli_finite_latest_depth_truthful_output_and_finally_close(
    migrated_path, monkeypatch, capsys, mode
):
    import asyncio

    from sqlalchemy import event

    from tg_assistant import cli

    db = Database(f"sqlite+aiosqlite:///{migrated_path}")
    adapter = object.__new__(UserClientAdapter)
    adapter.database, adapter.policy = db, PolicyEngine()
    adapter.client = HistoryClient(range(1, 1601))
    closed = []

    async def setup():
        async with db.session() as session:
            session.add(TelegramChat(chat_id=100, title="CLI", chat_type="group"))
            await adapter.policy.apply_template(session, 100, "knowledge")
            if mode == "quota":
                (await session.scalar(select(TelegramChatPolicy))).max_messages = 2

    asyncio.run(setup())

    async def authenticate(*args):
        return SimpleNamespace(id=1)

    async def discover(*args):
        return None

    async def close():
        closed.append(True)

    adapter.authenticate, adapter.discover_dialogs, adapter.close = authenticate, discover, close
    monkeypatch.setattr(cli, "get_settings", lambda: None)
    monkeypatch.setattr(cli, "SecretStore", lambda: SimpleNamespace(get=lambda *args: None))
    monkeypatch.setattr(cli, "ensure_runtime_dirs", lambda: {})
    monkeypatch.setattr(cli, "make_database", lambda *args: db)
    monkeypatch.setattr(cli, "make_user_client", lambda *args: adapter)
    if mode == "near_rate_limit":
        from tg_assistant.db.models import PermissionName

        for _ in range(28):
            assert adapter.policy.limiter.allow(1, PermissionName.SYNC_HISTORY.value)
    if mode == "revoked_after_page":
        original_stage = adapter.stage_history_page
        staged_count = 0

        async def revoke_next_stage(session, **kwargs):
            nonlocal staged_count
            staged_count += 1
            if staged_count == 2:
                async with db.session() as current:
                    await adapter.policy.set_allowed(current, 100, False)
            return await original_stage(session, **kwargs)

        adapter.stage_history_page = revoke_next_stage
    if mode == "commit_failure":
        original = adapter.apply_history_page

        async def reject(session, staged):
            result = await original(session, staged)

            def fail_commit(_):
                raise RuntimeError("synthetic commit rejection")

            event.listen(session.sync_session, "before_commit", fail_commit)
            return result

        adapter.apply_history_page = reject
        with pytest.raises(RuntimeError, match="synthetic commit rejection"):
            cli.sync.__wrapped__(limit=1500)
    else:
        cli.sync.__wrapped__(limit=1500)
    output = capsys.readouterr().out
    assert closed == [True]
    engine = create_engine(f"sqlite:///{migrated_path}")
    with engine.connect() as connection:
        ids = set(connection.execute(text("SELECT message_id FROM telegram_messages")).scalars())
    engine.dispose()
    if mode in {"complete", "near_rate_limit"}:
        assert (
            ids == set(range(101, 1601))
            and "1500 tin" in output
            and "0 chat chưa hoàn tất" in output
        )
    elif mode == "revoked_after_page":
        assert (
            ids == set(range(101, 1101))
            and "1000 tin" in output
            and "1 chat chưa hoàn tất" in output
        )
    elif mode == "quota":
        assert ids == {101, 102} and "2 tin" in output and "1 chat chưa hoàn tất" in output
    else:
        assert not ids and "Đã đồng bộ" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [False, True])
async def test_competing_file_writers_accept_exactly_one_stage(history, initial):
    import asyncio

    db, adapter = history
    if initial:
        async with db.session() as session:
            await session.execute(delete(SyncState))
    first, second = await stage(db, adapter, 2), await stage(db, adapter, 3)
    outcomes = await asyncio.gather(
        apply(db, adapter, first), apply(db, adapter, second), return_exceptions=True
    )
    assert sum(not isinstance(value, BaseException) for value in outcomes) == 1
    assert (
        sum(
            isinstance(value, RuntimeError) and str(value) == "history_sync_conflict"
            for value in outcomes
        )
        == 1
    )
    async with db.session() as session:
        state = await session.scalar(select(SyncState))
        ids = set(await session.scalars(select(TelegramMessage.message_id)))
        winner = first if state.baseline_message_id == first.baseline and len(ids) == 2 else second
        assert ids == {message.id for message in winner.messages}


@pytest.mark.asyncio
async def test_nonincreasing_edit_over_quota_remains_admissible(history):
    db, adapter = history
    async with db.session() as session:
        session.add(
            TelegramMessage(
                chat_id=100, message_id=2, text="old long value", sent_at=datetime.now(UTC)
            )
        )
        policy = await session.scalar(select(TelegramChatPolicy))
        policy.max_messages = policy.max_storage_mb = 0
    staged = await stage(db, adapter, 2)
    staged.messages[0].message = ""
    result = await apply(db, adapter, staged)
    assert result.saved == 1 and result.stop_reason == "quota" and result.after_id == 2
    async with db.session() as session:
        assert (await session.scalar(select(TelegramMessage))).text == ""
        assert len(list(await session.scalars(select(TelegramMessageVersion)))) == 1


@pytest.mark.asyncio
async def test_fifty_healthy_learning_pages_do_not_spend_new_request_allowances(
    history, monkeypatch
):
    from tg_assistant.db.models import BackgroundJob

    app, job_id = await learning_case(history, limit=1, attempts=0)
    app.user.client.ids = list(range(1, 52))
    clock = [0.0]
    monkeypatch.setattr("tg_assistant.policy.monotonic", lambda: clock[0])
    for page_number in range(50):
        await app._process_learning_jobs()
        async with app.database.session() as session:
            job = await session.get(BackgroundJob, job_id)
            assert job.status == "queued" and job.attempts == 0 and job.last_error is None
            assert job.payload["synced_count"] == page_number + 1
            job.run_after = None
        clock[0] += 2
    assert app.policy.limiter.limit == 30 and app.policy.limiter.seconds == 60


@pytest.mark.asyncio
async def test_apply_authorization_recheck_does_not_consume_second_allowance(history):
    from tg_assistant.db.models import PermissionName

    db, adapter = history
    for _ in range(29):
        assert adapter.policy.limiter.allow(1, PermissionName.SYNC_HISTORY.value)
    staged = await stage(db, adapter, 2)
    result = await apply(db, adapter, staged)
    assert result.saved == 2
    adapter.client.calls.clear()
    with pytest.raises(PermissionError, match="rate_limited"):
        await stage(db, adapter, 2)
    assert adapter.client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["upper", "after", "epoch", "owner", "completed"])
async def test_continuation_cursor_never_bypasses_authority_or_current_state(history, mismatch):
    db, adapter = history
    adapter.owner_id = 1
    if mismatch == "completed":
        adapter.client.ids = [2]
    staged = await stage(db, adapter, 2)
    result = await apply(db, adapter, staged)
    cursor = (staged.epoch, result.upper_id, result.after_id)
    if mismatch in {"upper", "after", "epoch"}:
        index = {"epoch": 0, "upper": 1, "after": 2}[mismatch]
        cursor = tuple(value + int(position == index) for position, value in enumerate(cursor))
    adapter.client.calls.clear()
    async with db.session() as session:
        with pytest.raises(
            PermissionError if mismatch == "owner" else RuntimeError,
            match="not_owner" if mismatch == "owner" else "history_sync_conflict",
        ):
            await adapter.stage_history_page(
                session,
                chat_id=100,
                actor_id=2 if mismatch == "owner" else 1,
                owner_id=2 if mismatch == "owner" else 1,
                limit=2,
                continuation_cursor=cursor,
            )
    assert adapter.client.calls == []


@pytest.mark.asyncio
async def test_valid_continuation_rechecks_permission_and_epoch_without_spending_limit(history):
    from tg_assistant.db.models import PermissionName, TelegramChatPermission
    from tg_assistant.services.revocation import AuthorizationRevoked

    db, adapter = history
    staged = await stage(db, adapter, 2)
    result = await apply(db, adapter, staged)
    cursor = (staged.epoch, result.upper_id, result.after_id)
    for _ in range(29):
        assert adapter.policy.limiter.allow(1, PermissionName.SYNC_HISTORY.value)
    next_page = await stage(db, adapter, 2, continuation_cursor=cursor)
    assert len(next_page.messages) == 2
    async with db.session() as session:
        permission = await session.scalar(
            select(TelegramChatPermission).where(
                TelegramChatPermission.chat_id == 100,
                TelegramChatPermission.permission == PermissionName.SYNC_HISTORY.value,
            )
        )
        permission.enabled = False
    adapter.client.calls.clear()
    with pytest.raises(AuthorizationRevoked):
        await stage(db, adapter, 2, continuation_cursor=cursor)
    with pytest.raises(AuthorizationRevoked):
        await apply(db, adapter, next_page)
    assert adapter.client.calls == []
