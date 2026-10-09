"""Native bot resources borrow the verified account owner's loop and guard."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from time import monotonic

from ..config import validate_settings
from ..paths import current_user_sid
from ..services.bot_credentials import KeyringCredentialBackend, ScopedBotCredentials
from ..services.bot_pairing import AccountProof, PairingRepository
from .telegram_context import TelegramContext, _selected_storage


class BotContext:
    def __init__(self, *, account_context, binding, service):
        self._account, self._binding, self.service = account_context, binding, service
        # Retain the borrowed loop for cleanup even after proof expiry. It stays
        # owned by TelegramContext; this borrower never stops or releases it.
        self._runner = binding.runner
        self._closing, self._released = False, False
        self._close_lock = Lock()

    @property
    def runner(self):
        if self._closing:
            raise ValueError("bot_context_unavailable")
        self._binding.check_ownership()
        return self._runner

    async def refresh_async(self):
        if self._closing:
            raise ValueError("bot_context_unavailable")
        # The existing account owner remeasures its own SDK candidate. A bot's
        # getMe response can never renew account evidence by itself.
        await self._account._refresh()
        self._binding.check_ownership()
        return await self.service.resume()

    def refresh(self):
        return self._runner.submit(self.refresh_async()).result(75)

    def bot_verification(self):
        return None if self._closing else self.service.verification()

    def pairing_verification(self):
        return None if self._closing else self.service.pairing_verification()

    def connection_status(self):
        return self.service.connection_status()

    def close(self):
        with self._close_lock:
            if self._released:
                return
            self._closing = True
            failed = False
            try:
                self._runner.submit(self.service.close()).result(10)
            except Exception:
                failed = True
            if failed:
                # The parent retains this context and the account guard. A
                # later successful drain, not a timeout, permits worker resume.
                raise ValueError("bot_shutdown_pending") from None
            self._released = True


def open_bot_context(
    settings, *, engine, fence, account_context,
    _credential_backend=None, _service_factory=None,
    now=lambda: datetime.now(UTC), monotonic_clock=monotonic,
):
    """Compose only after native worker handoff and owning account verification.

    Underscored injection is for disposable tests, never browser/native input.
    This creates no second account guard, runner, database or Telegram session.
    """
    failed = False
    result = None
    try:
        settings = validate_settings(settings)
        _selected_storage(settings, engine, fence)
        if (
            type(account_context) is not TelegramContext
            or account_context.engine is not engine
            or account_context.fence is not fence
            or account_context.profile_id != settings.profile_id
            or account_context._root.resolve() != settings.data_dir.resolve()
        ):
            raise ValueError("bot_context_unavailable")
        binding = account_context.borrow_bot_binding()

        def account_proof():
            proof = binding.account_verifier()
            return AccountProof(int(proof.owner_id), proof.fingerprint) if proof else None

        repository = PairingRepository(
            engine=engine, profile_id=settings.profile_id,
            profile_root=settings.data_dir, fence=fence,
            check_ownership=binding.check_ownership,
            account_verifier=account_proof, now=now,
        )
        backend = (
            _credential_backend if _credential_backend is not None else KeyringCredentialBackend()
        )
        credentials = ScopedBotCredentials(
            settings.profile_id, backend, binding.check_ownership,
            repository.current_reference, current_user_sid,
            publication_guard=repository.publication_guard,
            candidate_unpublished=repository.candidate_unpublished,
        )
        if _service_factory is None:
            from ..services.bot_connection import BotConnectionService

            _service_factory = BotConnectionService
        service = _service_factory(repository, credentials, now=now, monotonic=monotonic_clock)
        result = BotContext(account_context=account_context, binding=binding, service=service)
    except Exception:
        failed = True
    if failed:
        raise ValueError("bot_context_unavailable") from None
    return result
