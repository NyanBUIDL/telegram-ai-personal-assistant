"""Bot runtime borrowing one Application account client and retained worker guard."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ..config import validate_settings
from ..paths import current_user_sid
from ..services.bot_connection import BotConnectionService
from ..services.bot_credentials import KeyringCredentialBackend, ScopedBotCredentials
from ..services.bot_pairing import AccountProof, PairingRepository
from ..telegram.bot_api import BotApiTransport
from .telegram_context import RuntimeTelegramObservation, _selected_storage


def _require(value):
    if not value:
        raise ValueError("runtime_bot_unavailable")


class _SetupObservation:
    """Synchronous view only; Application owns asynchronous SDK cleanup."""

    def __init__(self, context):
        self._context = context
        self._account = context.account_observation

    def bot_verification(self):
        return self._context.bot_verification()

    def pairing_verification(self):
        return self._context.pairing_verification()

    def connection_status(self):
        return self._context.connection_status()

    def refresh(self):
        raise ValueError("runtime_refresh_required")

    def close(self):
        if not self._context._released:
            raise ValueError("bot_shutdown_pending")
        self._account.close()


class RuntimeBotContext:
    def __init__(
        self, settings, *, engine, fence, guard, runtime,
        _credential_backend=None, _client_factory=BotApiTransport,
    ):
        failed = False
        try:
            settings = validate_settings(settings)
            _selected_storage(settings, engine, fence)
            self._settings, self.engine, self.fence = settings, engine, fence
            self._runtime, self._user, self._store = runtime, runtime.user, runtime.store
            self._account_client = runtime.user.client
            self._guard = guard
            self._loop = self._poll_task = self._close_task = None
            self._health_task = self._dispatch_task = None
            self._health_interval = 20
            self._health_failed = False
            self._prepared = self._closing = self._released = False
            self.account_observation = RuntimeTelegramObservation(
                settings, engine=engine, fence=fence, guard=guard, store=self._store,
            )
            self._account = self.account_observation
            self._check_binding()
            self.repository = PairingRepository(
                engine=engine, profile_id=settings.profile_id, profile_root=settings.data_dir,
                fence=fence, check_ownership=self._check_binding,
                account_verifier=self._account_proof, now=lambda: datetime.now(UTC),
            )
            backend = _credential_backend if _credential_backend is not None else KeyringCredentialBackend()
            credentials = ScopedBotCredentials(
                settings.profile_id, backend, self._check_binding,
                self.repository.current_reference, current_user_sid,
                publication_guard=self.repository.publication_guard,
                candidate_unpublished=self.repository.candidate_unpublished,
            )
            self.service = BotConnectionService(
                self.repository, credentials, _client_factory=_client_factory,
                _management_mode=True,
            )
            self.setup_observation = _SetupObservation(self)
        except Exception:
            failed = True
        if failed:
            raise ValueError("runtime_bot_unavailable") from None

    def _bind_loop(self):
        current = asyncio.get_running_loop()
        _require(self._loop is None or current is self._loop)
        self._loop = current

    def _check_binding(self):
        runtime = self._runtime
        _require(not self._closing and not self._released)
        _require(not getattr(runtime, "_closed", False) and getattr(runtime, "_close_task", None) is None)
        _require(not runtime.stopping.is_set())
        _require(runtime.user is self._user and runtime.user.client is self._account_client)
        _require(runtime.store is self._store)
        _require(runtime.settings.profile_id == self._settings.profile_id)
        _require(runtime.settings.data_dir.resolve() == self._settings.data_dir.resolve())
        _require(self.account_observation._guard is self._guard)
        _require(self.account_observation.engine is self.engine and self.account_observation.fence is self.fence)
        _require(self._account_client.is_connected() is True)
        self.account_observation._check()

    def _account_proof(self):
        try:
            self._check_binding()
            proof = self.account_observation.verification()
            return AccountProof(int(proof.owner_id), proof.fingerprint) if proof else None
        except Exception:
            return None

    def _verified_pair(self):
        try:
            self._check_binding()
            account = self._account_proof()
            bot = self.service.verification()
            pair = self.service.pairing_verification()
            _require(account is not None and bot is not None and pair is not None)
            _require(type(bot.owner_id) is int and type(pair.owner_id) is int)
            _require(bot.owner_id == pair.owner_id == account.owner_id)
            _require(type(self._user.owner_id) is int and self._user.owner_id == account.owner_id)
            self._check_binding()
            return True
        except Exception:
            return False

    async def prepare(self):
        self._bind_loop()
        failed = False
        try:
            self._check_binding()
            await self.account_observation.refresh(self._runtime)
            _require(self._account_proof() is not None)
            document = self.repository.read_admission()
            _require(document["enrollment"] is not None and document["pairing"] is not None)
            await self.service.resume()
            _require(self._verified_pair())
            self._prepared = True
        except asyncio.CancelledError:
            self._prepared = False
            raise
        except Exception:
            self._prepared = False
            failed = True
        if failed:
            raise ValueError("runtime_bot_unavailable") from None

    async def refresh(self, runtime=None):
        self._bind_loop()
        failed = False
        try:
            _require(runtime is None or runtime is self._runtime)
            _require(self._prepared)
            self._check_binding()
            await self.account_observation.refresh(self._runtime)
            _require(self._account_proof() is not None)
            await self.service.resume()
            _require(self._verified_pair())
        except asyncio.CancelledError:
            self._prepared = False
            raise
        except Exception:
            self._prepared = False
            failed = True
        if failed:
            raise ValueError("runtime_bot_unavailable") from None

    def management_admitted(self):
        return self._prepared is True and self._verified_pair() is True

    def bot_verification(self):
        return self.service.verification() if self.management_admitted() else None

    def pairing_verification(self):
        return self.service.pairing_verification() if self.management_admitted() else None

    def connection_status(self):
        return self.service.connection_status()

    @property
    def bot(self):
        self._bind_loop()
        _require(self.management_admitted())
        return self.service.bot

    async def run_updates(self, dispatcher, bot):
        self._bind_loop()
        failed = False
        current = asyncio.current_task()
        try:
            _require(self._poll_task is None and bot is self.bot)
            self._poll_task = current
            self._health_failed = False
            self._health_task = asyncio.create_task(self._maintain_health(current))
            while not self._closing and not self._runtime.stopping.is_set():
                _require(self.management_admitted() and bot is self.service.bot)
                updates = await self.service.poll_management_once()
                for value in updates:
                    with self.fence.operation():
                        _require(self.management_admitted() and bot is self.service.bot)
                        # Parent handler policies still fence external effects.
                        # Cancellation-resistant work retains this admitted
                        # scope until actual completion; close cannot release it.
                        self._dispatch_task = asyncio.create_task(dispatcher.feed_update(bot, value))
                        try:
                            await self._dispatch_task
                        finally:
                            if self._dispatch_task.done():
                                self._dispatch_task = None
                        _require(self.management_admitted())
                        self.service.acknowledge_update(value.update_id)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            if not self._health_failed:
                raise
            failed = True
        except Exception:
            self._prepared = False
            failed = True
        finally:
            if self._poll_task is current:
                health = self._health_task
                if health is not None:
                    health.cancel()
                    await asyncio.gather(health, return_exceptions=True)
                    self._health_task = None
                self._poll_task = None
        if failed:
            raise ValueError("runtime_bot_unavailable") from None

    async def _maintain_health(self, polling):
        try:
            while not self._closing and not self._runtime.stopping.is_set():
                await asyncio.sleep(self._health_interval)
                self._check_binding()
                await self.account_observation.refresh(self._runtime)
                _require(self._account_proof() is not None)
                await self.service.resume()
                # This measures the original unadvanced offset. The service
                # retains issued updates and bounds/deduplicates new updates.
                await self.service.refresh_pending_poll()
                _require(self._verified_pair())
        except asyncio.CancelledError:
            raise
        except Exception:
            self._prepared = False
            self._health_failed = True
            polling.cancel()

    async def _drain(self):
        health = self._health_task
        if health is not None:
            health.cancel()
        polling = self._poll_task
        if polling is not None and polling is not asyncio.current_task():
            polling.cancel()
            await asyncio.gather(polling, return_exceptions=True)
        if health is not None:
            await asyncio.gather(health, return_exceptions=True)
        dispatch = self._dispatch_task
        if dispatch is not None:
            dispatch.cancel()
            await asyncio.gather(dispatch, return_exceptions=True)
            self._dispatch_task = None
        await self.service.close()
        self.account_observation.close()
        self._released = True

    async def close(self):
        self._bind_loop()
        self._closing = True
        self._prepared = False
        if self._released:
            return
        if self._close_task is None or (
            self._close_task.done() and self._close_task.exception() is not None
        ):
            self._close_task = asyncio.create_task(self._drain())
        failed = False
        try:
            await asyncio.wait_for(asyncio.shield(self._close_task), 10)
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
        if failed:
            raise ValueError("bot_shutdown_pending") from None
