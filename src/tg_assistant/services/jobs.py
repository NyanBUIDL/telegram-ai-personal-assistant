"""Short transactions and private durable tokens; no external I/O in claims."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from secrets import token_hex

from sqlalchemy import create_engine, event, or_, select, text, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..contracts import JobLease, OperationResult
from ..db.base import configure_sqlite
from ..db.models import BackgroundJob, TelegramChatPolicy
from .revocation import AuthorizationRevoked, source_ids


class LeaseLost(AuthorizationRevoked):
    """An obsolete runtime worker must abandon all further writes/effects."""


active_job_lease: ContextVar[JobLease | None] = ContextVar("active_job_lease", default=None)


@contextmanager
def worker_lease(lease):
    token = active_job_lease.set(lease)
    try:
        yield
    finally:
        active_job_lease.reset(token)


def fence_runtime_writes(session, lease, *, phase="commit"):
    job_dirty = any(
        isinstance(job, BackgroundJob) and session.is_modified(job, include_collections=False)
        for job in session.dirty
    )
    if phase == "flush" and not job_dirty:
        return
    if phase == "commit" and not (
        session.new
        or session.dirty
        or session.deleted
        or session.info.get("job_lease_fenced")
        or session.info.get("job_core_write")
    ):
        return
    with session.no_autoflush:
        row = session.execute(
            select(
                BackgroundJob.claim_token,
                BackgroundJob.lease_expires_at,
                BackgroundJob.status,
                BackgroundJob.payload,
            )
            .where(BackgroundJob.id == lease.id)
            .with_for_update()
        ).first()
        own_transaction = session.info.get("job_lease_fenced") == lease.claim_token
        if (
            not row
            or row.claim_token != lease.claim_token
            or row.lease_expires_at is None
            or utc(row.lease_expires_at) <= datetime.now(UTC)
            or (
                not own_transaction
                and row.status not in {"running", "pause_requested", "cancel_requested"}
            )
        ):
            raise LeaseLost("job_lease_lost")
        # Cancellation/revocation cannot become a successful worker completion.
        if row.status in {"cancelled", "uncertain"}:
            raise LeaseLost("job_lease_lost")
        if row.status in {"cancel_requested", "pause_requested"}:
            target = "cancelled" if row.status == "cancel_requested" else "paused"
            finishing_request = any(
                isinstance(job, BackgroundJob)
                and job.id == lease.id
                and job.status in {target, "uncertain"}
                for job in session.dirty
            )
            if not finishing_request:
                raise LeaseLost("job_control_requested")
        job = BackgroundJob(payload=row.payload)
        if phase == "commit" and not JobRepository.authorized(session, job):
            raise LeaseLost("source_authorization_revoked")
        session.info["job_lease_fenced"] = lease.claim_token


def track_runtime_statement(state, lease):
    if state.is_insert or state.is_update or state.is_delete:
        state.session.info["job_core_write"] = True
        if (state.is_update or state.is_delete) and getattr(
            getattr(state.statement, "table", None), "name", None
        ) == BackgroundJob.__tablename__:
            fence_runtime_writes(state.session, lease, phase="statement")


async def claim_runtime_job(
    database, job_types, *, worker_id="telegram-assistant-runtime", lease_seconds=900
):
    async with database.session() as session:
        if session.get_bind().dialect.name == "sqlite":
            await session.execute(text("BEGIN IMMEDIATE"))
        now = datetime.now(UTC)
        lease = await session.run_sync(
            lambda sync: JobRepository.claim_in_session(
                sync, tuple(job_types), worker_id, now, lease_seconds
            )
        )
        if lease:
            job_type = await session.scalar(
                select(BackgroundJob.job_type).where(BackgroundJob.id == lease.id)
            )
            return lease, job_type
    return None


async def finish_requested_lease(database, lease):
    async with database.session() as session:
        job = await session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.id == lease.id,
                BackgroundJob.claim_token == lease.claim_token,
                BackgroundJob.status.in_(("cancel_requested", "pause_requested")),
            )
            .with_for_update()
        )
        if not job:
            return
        destructive_started = (job.payload or {}).get(
            "external_effect_started"
        ) and job.job_type not in {"history_backfill", "learn_group", "ollama_pull"}
        job.status = (
            "uncertain"
            if destructive_started
            else ("cancelled" if job.status == "cancel_requested" else "paused")
        )
        job.claim_token = job.lease_expires_at = job.locked_by = job.locked_at = None
        if destructive_started:
            job.payload = {**(job.payload or {}), "requires_reconciliation": True}


class StorageBusy(RuntimeError):
    """Storage is busy; callers may schedule a bounded later attempt."""


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class JobRepository:
    def __init__(self, url: str | URL, *, profile_id: str, fence=None):
        value = make_url(url)
        if value.get_backend_name() == "sqlite":
            value = value.set(drivername="sqlite")
            self.engine = create_engine(value, connect_args={"timeout": 0.2})

            @event.listens_for(self.engine, "connect")
            def configure(connection, record):
                configure_sqlite(connection, record, busy_timeout=200)
        else:
            self.engine = create_engine(
                value.set(drivername="mysql+pymysql"),
                connect_args={"init_command": "SET time_zone = '+00:00'"},
            )
        self.profile_id, self.fence = profile_id, fence

    @contextmanager
    def transaction(self):
        with self.fence.operation() if self.fence else nullcontext():
            try:
                with self.engine.connect() as connection:
                    if connection.dialect.name == "sqlite":
                        connection.exec_driver_sql("BEGIN IMMEDIATE")
                    with Session(bind=connection) as session:
                        try:
                            yield session
                            session.flush()
                            connection.commit()
                        except BaseException:
                            connection.rollback()
                            raise
            except OperationalError:
                # Do not echo SQL parameters (payloads/tokens) into UI or logs.
                raise StorageBusy("storage_busy") from None

    def claim(
        self, job_type: str, worker_id: str, now: datetime, lease_seconds: int
    ) -> JobLease | None:
        if (
            now.tzinfo is None
            or not 1 <= lease_seconds <= 86400
            or not worker_id
            or len(worker_id) > 128
        ):
            raise ValueError("invalid_job_claim")
        now = utc(now)
        with self.transaction() as session:
            return self.claim_in_session(session, job_type, worker_id, now, lease_seconds)

    @classmethod
    def claim_in_session(cls, session, job_type, worker_id, now, lease_seconds):
        types = (job_type,) if isinstance(job_type, str) else job_type
        expired = list(
            session.scalars(
                select(BackgroundJob)
                .where(
                    BackgroundJob.job_type.in_(types),
                    BackgroundJob.status.in_(("running", "cancel_requested", "pause_requested")),
                    or_(
                        BackgroundJob.lease_expires_at <= now,
                        (BackgroundJob.lease_expires_at.is_(None))
                        & (BackgroundJob.locked_at < now - timedelta(minutes=15)),
                    ),
                )
                .order_by(BackgroundJob.id)
                .with_for_update()
            )
        )
        for job in expired:
            if (job.payload or {}).get("external_effect_started") and job.job_type not in {
                "history_backfill",
                "learn_group",
                "ollama_pull",
            }:
                job.status = "uncertain"
                job.payload = {
                    **(job.payload or {}),
                    "phase": "uncertain",
                    "requires_reconciliation": True,
                }
                job.run_after = None
                job.last_error = "external_effect_requires_reconciliation"
            else:
                job.status = {"cancel_requested": "cancelled", "pause_requested": "paused"}.get(
                    job.status, "queued" if job.attempts < job.max_attempts else "failed"
                )
            job.claim_token = job.lease_expires_at = job.locked_by = job.locked_at = None
        session.flush()
        job = session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.job_type.in_(types),
                BackgroundJob.status == "queued",
                or_(BackgroundJob.run_after.is_(None), BackgroundJob.run_after <= now),
                BackgroundJob.attempts < BackgroundJob.max_attempts,
            )
            .order_by(BackgroundJob.created_at, BackgroundJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if not job:
            return None
        if not cls.authorized(session, job):
            job.status = "cancelled"
            job.last_error = "source_authorization_revoked"
            return None
        token, expiry = token_hex(32), now + timedelta(seconds=lease_seconds)
        changed = session.execute(
            update(BackgroundJob)
            .where(
                BackgroundJob.id == job.id,
                BackgroundJob.status == "queued",
                BackgroundJob.attempts == job.attempts,
            )
            .values(
                status="running",
                attempts=job.attempts + 1,
                claim_token=token,
                lease_expires_at=expiry,
                locked_by=worker_id,
                locked_at=now,
            )
        )
        if changed.rowcount != 1:
            return None
        payload = dict(job.payload or {})
        epochs = payload.get("authorization_epochs", {})
        return JobLease(
            id=job.id,
            claim_token=token,
            payload=payload,
            expires_at=expiry,
            authorization_epoch=max(epochs.values(), default=payload.get("authorization_epoch", 0)),
        )

    @staticmethod
    def authorized(session, job):
        payload = job.payload or {}
        snapshots = payload.get("authorization_epochs", {})
        for chat_id in source_ids(payload):
            row = session.execute(
                select(TelegramChatPolicy.allowed, TelegramChatPolicy.authorization_epoch)
                .where(TelegramChatPolicy.chat_id == chat_id)
                .with_for_update()
            ).first()
            expected = snapshots.get(str(chat_id), payload.get("authorization_epoch"))
            if not row or not row.allowed or expected != row.authorization_epoch:
                return False
        return True

    def current(self, session, lease):
        job = session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.id == lease.id,
                BackgroundJob.status == "running",
                BackgroundJob.claim_token == lease.claim_token,
                BackgroundJob.lease_expires_at > datetime.now(UTC),
            )
            .with_for_update()
        )
        if job and not self.authorized(session, job):
            job.status = (
                "uncertain" if (job.payload or {}).get("external_effect_started") else "cancelled"
            )
            job.claim_token = job.lease_expires_at = job.locked_by = job.locked_at = None
            job.last_error = "source_authorization_revoked"
            return None
        return job

    def mark_external_started(self, lease: JobLease) -> bool:
        with self.transaction() as session:
            job = self.current(session, lease)
            if not job:
                return False
            job.payload = {**(job.payload or {}), "external_effect_started": True}
            return True

    def complete(self, lease: JobLease, result: OperationResult) -> bool:
        result = OperationResult.model_validate(result)
        if result.operation_id != lease.id or result.state.value not in {
            "completed",
            "failed",
            "cancelled",
        }:
            raise ValueError("invalid_job_result")
        with self.transaction() as session:
            job = self.current(session, lease)
            if not job:
                return False
            job.status = result.state.value
            job.payload = {
                **(job.payload or {}),
                "phase": result.state.value,
                "progress": result.progress,
            }
            job.claim_token = job.lease_expires_at = job.locked_by = job.locked_at = None
            return True

    def close(self):
        self.engine.dispose()
