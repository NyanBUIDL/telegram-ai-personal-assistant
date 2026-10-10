"""Setup context over already migrated, SID-owned selected storage.

This factory never creates or migrates a database and never supplies placeholder
account, bot or AI verification. Worker startup prepares storage first.
"""

from __future__ import annotations

import hashlib

from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event

from ..config import validate_settings
from ..contracts import ConnectionService, OnboardingStage
from ..db.base import configure_sqlite
from ..db.migrations import actual_revision
from ..paths import current_user_sid, ensure_runtime_dirs, resource_path
from ..security import SecretStore
from ..services.connections import storage_probe
from ..services.maintenance import MaintenanceService
from ..services.onboarding import OnboardingCoordinator, StageVerification, storage_verifier
from ..services.storage import StorageService


class SetupContext:
    def __init__(self, coordinator, engine, fence, provider):
        self.coordinator, self.engine, self.fence = coordinator, engine, fence
        self.provider = provider
        self.telegram = None
        self.bot = None

    def install_telegram(self, context):
        if self.telegram is not None:
            raise ValueError("telegram_context_already_owned")
        self.telegram = context
        self.coordinator._verifiers[OnboardingStage.TELEGRAM_VERIFIED] = context.verification
        self.coordinator.connections.telegram_reader = context.connection_status

    def detach_telegram(self):
        self.detach_bot()
        if self.telegram is not None:
            # A timeout must retain resources and the actual ownership fence.
            self.telegram.close()
            self.telegram = None
        self.coordinator._verifiers.pop(OnboardingStage.TELEGRAM_VERIFIED, None)
        self.coordinator.connections.telegram_reader = None

    def install_bot(self, context):
        if self.bot is not None or self.telegram is None or context._account is not self.telegram:
            raise ValueError("bot_context_already_owned")
        self.bot = context
        self.coordinator._verifiers[OnboardingStage.BOT_VERIFIED] = context.bot_verification
        self.coordinator._verifiers[OnboardingStage.OWNER_PAIRED] = context.pairing_verification
        self.coordinator.connections.bot_reader = context.connection_status

    def detach_bot(self):
        if self.bot is not None:
            # Failure retains both borrowers and account guard; never resume a
            # second worker while Bot API work is still being drained.
            self.bot.close()
            self.bot = None
        self.coordinator._verifiers.pop(OnboardingStage.BOT_VERIFIED, None)
        self.coordinator._verifiers.pop(OnboardingStage.OWNER_PAIRED, None)
        self.coordinator.connections.bot_reader = None

    def prepare_health(self, *, active_settings=None, refresh_telegram=True):
        """Owning probes run before the coordinator starts any SQL mutation."""
        self.provider.drain()
        self.provider.refresh_saved_health(active_settings=active_settings)
        if refresh_telegram:
            if self.bot is not None:
                self.bot.refresh()
            elif self.telegram is not None:
                self.telegram.refresh()

    def advance_verified(self):
        status = self.coordinator.status()
        with self.engine.connect() as connection:
            skip_ai = self.coordinator._read(connection)[0].options.ai_mode == "skip"
        for stage in (
            OnboardingStage.AI_CONFIGURED, OnboardingStage.TELEGRAM_VERIFIED,
            OnboardingStage.BOT_VERIFIED, OnboardingStage.OWNER_PAIRED,
            OnboardingStage.SOURCE_SELECTED, OnboardingStage.FIRST_ANSWER, OnboardingStage.READY,
        ):
            previous = list(OnboardingStage)[: list(OnboardingStage).index(stage)]
            if stage == OnboardingStage.READY and skip_ai:
                previous.remove(OnboardingStage.FIRST_ANSWER)
            if stage in status.stage_evidence_ids or any(
                item not in status.stage_evidence_ids for item in previous
            ):
                continue
            try:
                evidence = self.coordinator.verify_stage(stage)
            except ValueError:
                continue
            status = self.coordinator.complete_stage(stage, evidence)
        return status

    def refresh(self):
        self.prepare_health()
        self.coordinator.resume()
        return self.advance_verified()

    def skip_ai(self):
        self.coordinator.save_options({"ai_mode": "skip"})
        return self.advance_verified()

    def begin(self):
        """Native user's start action acknowledges welcome and verifies storage."""
        status = self.coordinator.resume()
        for stage in (OnboardingStage.WELCOME, OnboardingStage.STORAGE_READY):
            if stage not in status.stage_evidence_ids:
                status = self.coordinator.complete_stage(
                    stage, self.coordinator.verify_stage(stage)
                )
        return status

    def close(self):
        self.detach_telegram()
        self.provider.close()
        self.engine.dispose()
        self.fence.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def open_setup_context(
    settings,
    *,
    secret_store_factory=SecretStore,
    probes=None,
    verifiers=None,
    provider_options=None,
):
    from .provider_context import ProviderContext, ProviderHealth

    class SetupHealth(ProviderHealth):
        telegram_reader = None
        bot_reader = None

        def status(self):
            result = super().status()
            readers = {
                ConnectionService.TELEGRAM_ACCOUNT: self.telegram_reader,
                ConnectionService.CONTROL_BOT: self.bot_reader,
            }
            return [
                readers[row.service]().model_copy(deep=True)
                if readers.get(row.service) else row for row in result
            ]

    settings = validate_settings(settings)
    paths = ensure_runtime_dirs(settings.data_dir, profile_id=settings.profile_id)
    if not (paths["db"] / "assistant.sqlite3").is_file():
        raise ValueError("setup_storage_unavailable")
    storage = StorageService(settings)
    engine = create_engine(storage._url(async_driver=False), pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", configure_sqlite)
    fence = MaintenanceService(paths["config"], profile_id=settings.profile_id)
    try:
        with fence.operation(), engine.connect() as connection:
            head = ScriptDirectory(str(resource_path("alembic"))).get_current_head()
            if actual_revision(connection) != head:
                raise ValueError("setup_storage_unavailable")
        sid = current_user_sid()
        welcome_fingerprint = hashlib.sha256(
            f"welcome:{sid}:{settings.profile_id}".encode()
        ).hexdigest()

        def welcome():
            if current_user_sid() != sid:
                return None
            return StageVerification(fingerprint=welcome_fingerprint)

        selected_probes = dict(probes or {})
        selected_probes["storage"] = storage_probe(engine)
        selected_verifiers = dict(verifiers or {})
        selected_verifiers["welcome"] = welcome
        selected_verifiers["storage_ready"] = storage_verifier(engine)
        provider = ProviderContext(
            settings, engine, fence, secret_store_factory, **(provider_options or {})
        )
        selected_verifiers["ai_configured"] = provider.configuration_verification
        coordinator = OnboardingCoordinator(
            engine=engine,
            profile_id=settings.profile_id,
            storage_backend=settings.storage_backend,
            fence=fence,
            connections=SetupHealth(provider=provider, probes=selected_probes),
            verifiers=selected_verifiers,
        )
        return SetupContext(coordinator, engine, fence, provider)
    except Exception:
        engine.dispose()
        fence.close()
        raise
