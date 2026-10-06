"""Worker-owned refresh renews actual proof without making HTTP reads writers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from threading import Event

import httpx
import pytest
from desktop.test_setup_context import migrated_settings as migrated_settings
from sqlalchemy import select

from tg_assistant.db.models import AppSetting
from tg_assistant.desktop import worker
from tg_assistant.desktop.setup_context import open_setup_context


@pytest.mark.asyncio
async def test_worker_refreshes_actual_expired_health_and_keeps_http_readonly(migrated_settings):
    settings, storage = migrated_settings
    with open_setup_context(settings, secret_store_factory=lambda: storage.store) as context:
        clock = [datetime.now(UTC)]
        context.coordinator._now = lambda: clock[0]
        context.coordinator.connections._now = lambda: clock[0]
        context.begin()
        clock[0] += timedelta(seconds=301)
        gateway = worker.RuntimeGateway(19777, "a" * 32, setup_context=context)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway), base_url="http://127.0.0.1:19777"
        ) as client:
            with context.engine.connect() as connection:
                before = connection.execute(select(AppSetting.key, AppSetting.value)).all()
            expired = await client.get("/api/v1/connections")
            assert expired.status_code == 200
            assert {row["service"]: row["state"] for row in expired.json()}["storage"] == "unknown"
            with context.engine.connect() as connection:
                assert connection.execute(select(AppSetting.key, AppSetting.value)).all() == before
            refresh = getattr(worker, "maintain_setup_health", None)
            assert callable(refresh), "Worker has no ongoing measured setup refresh"
            task = asyncio.create_task(refresh(context.coordinator, interval=0.01))
            try:
                deadline = asyncio.get_running_loop().time() + 3
                while asyncio.get_running_loop().time() < deadline:
                    response = await client.get("/api/v1/setup/status")
                    status = response.json()
                    if (
                        status["profile"]["setup_stage"] == "storage_ready"
                        and next(
                            row for row in status["connections"] if row["service"] == "storage"
                        )["state"]
                        == "ready"
                    ):
                        break
                    await asyncio.sleep(0.01)
                assert status["profile"]["setup_stage"] == "storage_ready"
                assert status["profile"]["owner_id"] is None
                assert "management" in status["disabled_capabilities"]
                measured = next(row for row in status["connections"] if row["service"] == "storage")
                assert measured["state"] == "ready"
                assert (
                    datetime.fromisoformat(measured["checked_at"].replace("Z", "+00:00"))
                    == clock[0]
                )
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task


@pytest.mark.asyncio
async def test_worker_refresh_shutdown_drains_its_actual_running_probe(
    migrated_settings, monkeypatch
):
    settings, storage = migrated_settings
    with open_setup_context(settings, secret_store_factory=lambda: storage.store) as context:
        refresh = getattr(worker, "maintain_setup_health", None)
        assert callable(refresh), "Worker has no owned setup refresh lifecycle"
        entered, release, finished = Event(), Event(), Event()
        resume = context.coordinator.resume

        def slow_resume():
            entered.set()
            assert release.wait(3), "Owned refresh was not released"
            result = resume()
            finished.set()
            return result

        monkeypatch.setattr(context.coordinator, "resume", slow_resume)
        task = asyncio.create_task(refresh(context.coordinator, interval=0.01))
        try:
            deadline = asyncio.get_running_loop().time() + 2
            while not entered.is_set() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
            assert entered.is_set()
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done(), "Context can be disposed while its probe still runs"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set()
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_worker_refresh_recovers_after_error_without_logging_private_text(
    migrated_settings, monkeypatch, caplog
):
    settings, storage = migrated_settings
    with open_setup_context(settings, secret_store_factory=lambda: storage.store) as context:
        resume = context.coordinator.resume
        calls = []

        def transient():
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("SYNTHETIC_PRIVATE_REFRESH_FAILURE")
            return resume()

        monkeypatch.setattr(context.coordinator, "resume", transient)
        task = asyncio.create_task(worker.maintain_setup_health(context.coordinator, interval=0.01))
        try:
            deadline = asyncio.get_running_loop().time() + 3
            while len(calls) < 2 and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
            assert len(calls) >= 2
            assert "setup_health_refresh_unavailable" in caplog.text
            assert "SYNTHETIC_PRIVATE" not in caplog.text
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
