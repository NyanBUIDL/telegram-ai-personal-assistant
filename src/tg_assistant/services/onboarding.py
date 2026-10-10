"""Nonsecret, profile-bound persisted onboarding with service-issued evidence.

The sync interface is the frozen contract. Trusted native/owning services supply
verifiers in process; no browser-provided claim or evidence identifier alone is
proof. The selected storage AppSetting row is the durable source of truth.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, ValidationError, field_validator
from sqlalchemy import insert, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, OperationalError

from ..contracts import (
    ContractModel,
    Identifier,
    OnboardingStage,
    OnboardingStatus,
    OwnerId,
    PublicProfile,
    StorageBackend,
    TelegramId,
    Timestamp,
)
from ..db.models import AppSetting
from ..security import contains_secret
from .connections import ConnectionHealth, storage_probe
from .maintenance import MaintenanceService

Fingerprint = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]
ErrorCode = Literal["connection_unavailable", "verification_unavailable", "setup_interrupted"]


class StageVerification(ContractModel):
    """Owning service result after its actual checks, never a public DTO."""

    owner_id: OwnerId | None = None
    fingerprint: Fingerprint


class SetupOptions(ContractModel):
    ai_mode: Literal["skip", "local", "cloud"] | None = None
    provider: Literal["ollama", "openai", "openrouter"] | None = None
    model: Identifier | None = None
    source_id: TelegramId | None = None

    @field_validator("model")
    @classmethod
    def model_is_not_credential(cls, value):
        if value is not None and contains_secret(value):
            raise ValueError("setup_option_invalid")
        return value


class _Evidence(ContractModel):
    evidence_id: Identifier
    profile_id: Identifier
    stage: OnboardingStage
    issuer: Identifier
    checked_at: Timestamp
    expires_at: Timestamp
    verification: StageVerification


class _State(ContractModel):
    schema_version: Literal[1] = 1
    profile_id: Identifier
    storage_backend: StorageBackend
    revision: Annotated[int, Field(strict=True, ge=0)] = 0
    options: SetupOptions = Field(default_factory=SetupOptions)
    evidence: dict[Identifier, _Evidence] = Field(default_factory=dict)
    completed: dict[OnboardingStage, Identifier] = Field(default_factory=dict)
    error: ErrorCode | None = None


class OnboardingCoordinator:
    def __init__(
        self,
        *,
        engine: Engine,
        profile_id: str,
        storage_backend: StorageBackend | str,
        fence: MaintenanceService,
        connections: ConnectionHealth,
        verifiers: Mapping[OnboardingStage | str, Callable[[], StageVerification | None]],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        evidence_ttl_seconds: int = 300,
    ):
        self.profile_id = PublicProfile(
            profile_id=profile_id,
            owner_id=None,
            storage_backend=storage_backend,
            setup_stage="welcome",
            version=1,
        ).profile_id
        self.storage_backend = StorageBackend(storage_backend)
        if engine.dialect.name != self.storage_backend.value:
            raise ValueError("onboarding_backend_mismatch")
        if fence.profile_id != profile_id:
            raise ValueError("onboarding_fence_mismatch")
        if type(evidence_ttl_seconds) is not int or not 1 <= evidence_ttl_seconds <= 300:
            raise ValueError("evidence_ttl_invalid")
        self.engine, self.fence, self.connections = engine, fence, connections
        self._verifiers = {
            OnboardingStage(stage): verifier for stage, verifier in verifiers.items()
        }
        self._now = now
        self._ttl = timedelta(seconds=evidence_ttl_seconds)
        self._key = "onboarding." + hashlib.sha256(profile_id.encode()).hexdigest()

    def _time(self):
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("onboarding_clock_invalid")
        return value.astimezone(UTC)

    def _read(self, connection, *, lock=False):
        query = select(AppSetting.value).where(AppSetting.key == self._key)
        if lock and self.engine.dialect.name != "sqlite":
            query = query.with_for_update()
        raw = connection.execute(query).scalar_one_or_none()
        if raw is None:
            return _State(profile_id=self.profile_id, storage_backend=self.storage_backend), False
        try:
            state = _State.model_validate(raw)
            if state.profile_id != self.profile_id or state.storage_backend != self.storage_backend:
                raise ValueError
            if any(
                key != evidence.evidence_id or evidence.profile_id != self.profile_id
                for key, evidence in state.evidence.items()
            ):
                raise ValueError
        except (ValidationError, ValueError, TypeError):
            raise ValueError("onboarding_state_invalid") from None
        return state, True

    def _load(self):
        with self.engine.connect() as connection:
            return self._read(connection)[0]

    def _mutate(self, change, *, transaction_update=None):
        # SQLite serializes before the read; MySQL locks the selected row. The
        # first-row insert race retries from a fresh locked transaction. No stale
        # in-memory snapshot ever replaces another coordinator's evidence.
        for attempt in range(3):
            # Each retry reacquires admission and a fresh selected-backend
            # transaction, then rechecks the owning service within that fence.
            with self.fence.operation():
                try:
                    with self.engine.connect() as connection:
                        if self.engine.dialect.name == "sqlite":
                            connection.execute(text("BEGIN IMMEDIATE"))
                        else:
                            connection.begin()
                        try:
                            state, exists = self._read(connection, lock=True)
                            values = state.model_dump(mode="json")
                            result = change(values, state)
                            values["revision"] = state.revision + 1
                            checked = _State.model_validate(values).model_dump(mode="json")
                            if transaction_update is not None:
                                transaction_update(connection)
                            if exists:
                                updated = connection.execute(
                                    update(AppSetting)
                                    .where(
                                        AppSetting.key == self._key,
                                        AppSetting.value["revision"].as_integer() == state.revision,
                                    )
                                    .values(value=checked)
                                )
                                if updated.rowcount != 1:
                                    raise ValueError("onboarding_write_conflict")
                            else:
                                connection.execute(
                                    insert(AppSetting).values(key=self._key, value=checked)
                                )
                            connection.commit()
                            return result
                        except BaseException:
                            connection.rollback()
                            raise
                except IntegrityError:
                    if attempt == 2:
                        raise ValueError("onboarding_write_conflict") from None
                except OperationalError as error:
                    mysql_code = error.orig.args[0] if getattr(error.orig, "args", ()) else None
                    if self.engine.dialect.name != "mysql" or mysql_code not in {1213, 1205}:
                        raise ValueError("onboarding_storage_unavailable") from None
                    if attempt == 2:
                        raise ValueError("onboarding_write_conflict") from None
        raise ValueError("onboarding_write_conflict")

    def options(self):
        return self._load().options.model_dump(mode="json")

    def save_source_selection(
        self, source_id: int, *, transaction_update: Callable[[Connection], None]
    ) -> OnboardingStatus:
        if type(source_id) is not int or source_id == 0 or not callable(transaction_update):
            raise ValueError("source_selection_invalid")

        def change(values, state):
            values["options"]["source_id"] = str(source_id)
            if state.options.source_id != source_id:
                self._invalidate(values, OnboardingStage.SOURCE_SELECTED)
            values["error"] = None

        self._mutate(change, transaction_update=transaction_update)
        return self.status()

    def save_options(self, options: dict) -> OnboardingStatus:
        try:
            options = SetupOptions.model_validate(options).model_dump(
                mode="json", exclude_unset=True
            )
        except (ValidationError, ValueError, TypeError):
            raise ValueError("setup_options_invalid") from None

        def change(values, state):
            merged = {**values["options"], **options}
            updated_options = SetupOptions.model_validate(merged)
            values["options"] = updated_options.model_dump(mode="json")
            if any(
                values["options"][name] != getattr(state.options, name)
                for name in ("ai_mode", "provider", "model")
            ):
                self._invalidate(values, OnboardingStage.AI_CONFIGURED)
            elif updated_options.source_id != state.options.source_id:
                self._invalidate(values, OnboardingStage.SOURCE_SELECTED)
            values["error"] = None

        self._mutate(change)
        return self.status()

    @staticmethod
    def _invalidate(values, start):
        stages = list(OnboardingStage)
        invalid = set(stages[stages.index(start) :])
        values["completed"] = {
            stage: identifier
            for stage, identifier in values["completed"].items()
            if OnboardingStage(stage) not in invalid
        }
        values["evidence"] = {
            identifier: record
            for identifier, record in values["evidence"].items()
            if OnboardingStage(record["stage"]) not in invalid
        }

    def record_error(self, code: ErrorCode) -> OnboardingStatus:
        if code not in {"connection_unavailable", "verification_unavailable", "setup_interrupted"}:
            raise ValueError("setup_error_invalid")
        self._mutate(lambda values, state: values.update(error=code))
        return self.status()

    def _verify(self, stage, state):
        if stage == OnboardingStage.FIRST_ANSWER and state.options.ai_mode == "skip":
            raise ValueError("first_answer_disabled")
        if stage == OnboardingStage.READY:
            owner = self._require_ready(state)
            return StageVerification(owner_id=owner, fingerprint=self._ready_fingerprint(state))
        if stage == OnboardingStage.AI_CONFIGURED and state.options.ai_mode == "skip":
            return StageVerification(fingerprint=hashlib.sha256(b"explicit-ai-skip").hexdigest())
        verifier = self._verifiers.get(stage)
        if verifier is None:
            raise ValueError("verification_unavailable")
        try:
            result = verifier()
            if not isinstance(result, StageVerification):
                raise ValueError
            result = StageVerification.model_validate(result)
            if (
                stage in {OnboardingStage.TELEGRAM_VERIFIED, OnboardingStage.OWNER_PAIRED}
                and result.owner_id is None
            ):
                raise ValueError
            return result
        except Exception:
            raise ValueError("verification_unavailable") from None

    def _valid_completed(self, state):
        valid = {}
        now = self._time()
        for stage in OnboardingStage:
            identifier = state.completed.get(stage)
            record = state.evidence.get(identifier) if identifier else None
            if (
                record is None
                or record.stage != stage
                or record.issuer != self._issuer(stage)
                or record.checked_at > now
            ):
                continue
            if stage == OnboardingStage.READY:
                account = valid.get(OnboardingStage.TELEGRAM_VERIFIED)
                if account is not None and record.verification == StageVerification(
                    owner_id=account.verification.owner_id,
                    fingerprint=self._ready_fingerprint(state),
                ):
                    # Actual prerequisites and health are checked again before
                    # readiness is returned. Do not recurse through _verify().
                    valid[stage] = record
                continue
            try:
                # These records are already consumed completion history, not
                # pending bearer proof. Their authority comes from this fresh
                # owning-service recheck; pending IDs expire in complete_stage.
                if self._verify(stage, state) == record.verification:
                    valid[stage] = record
            except ValueError:
                pass
        return valid

    @staticmethod
    def _issuer(stage):
        return "coordinator" if stage == OnboardingStage.READY else "service." + stage.value

    @staticmethod
    def _ready_fingerprint(state):
        return hashlib.sha256(
            json.dumps(
                {
                    key: value
                    for key, value in state.completed.items()
                    if key != OnboardingStage.READY
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    @staticmethod
    def _stage(value):
        try:
            return OnboardingStage(value)
        except (ValueError, TypeError):
            raise ValueError("onboarding_stage_invalid") from None

    def verify_stage(self, stage: OnboardingStage | str) -> str:
        """Called by a trusted native/service adapter after its actual verification."""
        stage = self._stage(stage)
        identifier = uuid4().hex

        def change(values, state):
            verified = self._verify(stage, state)
            checked_at = self._time()
            record = _Evidence(
                evidence_id=identifier,
                stage=stage,
                issuer=self._issuer(stage),
                profile_id=self.profile_id,
                checked_at=checked_at,
                expires_at=checked_at + self._ttl,
                verification=verified,
            )
            # Bound pending evidence growth; completed records are preserved.
            completed_ids = set(values["completed"].values())
            values["evidence"] = {
                key: item
                for key, item in values["evidence"].items()
                if key in completed_ids
                or datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00")) > checked_at
            }
            if len(values["evidence"]) >= 128:
                raise ValueError("evidence_capacity")
            values["evidence"][identifier] = record.model_dump(mode="json")
            return identifier

        return self._mutate(change)

    def _require_ready(self, state):
        valid = self._valid_completed(state)
        required = [
            OnboardingStage.WELCOME,
            OnboardingStage.STORAGE_READY,
            OnboardingStage.AI_CONFIGURED,
            OnboardingStage.TELEGRAM_VERIFIED,
            OnboardingStage.BOT_VERIFIED,
            OnboardingStage.OWNER_PAIRED,
            OnboardingStage.SOURCE_SELECTED,
        ]
        if state.options.ai_mode != "skip":
            required.append(OnboardingStage.FIRST_ANSWER)
        for stage in required:
            if stage not in valid:
                raise ValueError(stage.value + "_required")
        owner = valid[OnboardingStage.TELEGRAM_VERIFIED].verification.owner_id
        if valid[OnboardingStage.OWNER_PAIRED].verification.owner_id != owner:
            raise ValueError("owner_mismatch")
        services = {item.service.value: item for item in self.connections.status()}
        required_services = ["storage", "telegram_account", "control_bot"]
        if state.options.ai_mode != "skip":
            required_services.append("chat_ai")
        if any(services[name].state != "ready" for name in required_services):
            raise ValueError("capabilities_unavailable")
        return owner

    def complete_stage(
        self, stage: OnboardingStage | str, verified_evidence_id: str
    ) -> OnboardingStatus:
        stage = self._stage(stage)
        if not isinstance(verified_evidence_id, str):
            raise ValueError("evidence_invalid")

        def change(values, state):
            record = state.evidence.get(verified_evidence_id)
            if (
                record is None
                or record.stage != stage
                or record.profile_id != self.profile_id
                or record.issuer != self._issuer(stage)
            ):
                raise ValueError("evidence_invalid")
            if verified_evidence_id in state.completed.values():
                raise ValueError("evidence_consumed")
            if not record.checked_at <= self._time() < record.expires_at:
                raise ValueError("evidence_expired")
            if self._verify(stage, state) != record.verification:
                raise ValueError("verification_changed")
            valid = self._valid_completed(state)
            previous = list(OnboardingStage)[: list(OnboardingStage).index(stage)]
            if stage == OnboardingStage.READY and state.options.ai_mode == "skip":
                previous.remove(OnboardingStage.FIRST_ANSWER)
            for prerequisite in previous:
                if prerequisite not in valid:
                    raise ValueError(prerequisite.value + "_required")
            if (
                stage == OnboardingStage.OWNER_PAIRED
                and record.verification.owner_id
                != valid[OnboardingStage.TELEGRAM_VERIFIED].verification.owner_id
            ):
                raise ValueError("owner_mismatch")
            # Actual owning-service and prerequisite checks can take time. A
            # pending ID must still be unexpired at its consumption point; an
            # early check cannot promote expired proof into durable history.
            if not record.checked_at <= self._time() < record.expires_at:
                raise ValueError("evidence_expired")
            self._invalidate(values, stage)
            values["evidence"][verified_evidence_id] = record.model_dump(mode="json")
            values["completed"][stage.value] = verified_evidence_id
            values["error"] = None

        self._mutate(change)
        return self.status()

    def status(self) -> OnboardingStatus:
        state = self._load()
        valid = self._valid_completed(state)
        current = OnboardingStage.WELCOME
        owner = None
        chain = {}
        for stage in OnboardingStage:
            if stage == OnboardingStage.FIRST_ANSWER and state.options.ai_mode == "skip":
                continue
            record = valid.get(stage)
            if record is None:
                break
            if stage == OnboardingStage.TELEGRAM_VERIFIED:
                owner = record.verification.owner_id
            if stage == OnboardingStage.OWNER_PAIRED and record.verification.owner_id != owner:
                break
            if stage == OnboardingStage.READY:
                try:
                    self._require_ready(state)
                except ValueError:
                    break
            current = stage
            chain[stage] = record.evidence_id
        health = self.connections.status()
        disabled = {item.service.value for item in health if item.state != "ready"}
        if OnboardingStage.OWNER_PAIRED not in chain or any(
            name in disabled for name in ("telegram_account", "control_bot", "storage")
        ):
            disabled.add("management")
        if current != OnboardingStage.READY:
            disabled.add("first_answer")
        if state.options.ai_mode == "skip" or OnboardingStage.AI_CONFIGURED not in chain:
            disabled.update(("chat_ai", "embeddings", "first_answer"))
        next_action = (
            None
            if current == OnboardingStage.READY
            else "Mở ứng dụng Windows để tiếp tục bước thiết lập còn thiếu."
        )
        if state.error:
            next_action = "Thiết lập đã lưu. Mở ứng dụng Windows để kiểm tra kết nối rồi tiếp tục."
        return OnboardingStatus(
            profile=PublicProfile(
                profile_id=self.profile_id,
                owner_id=owner,
                storage_backend=self.storage_backend,
                setup_stage=current,
                version=1,
            ),
            connections=health,
            stage_evidence_ids=chain,
            next_action=next_action,
            disabled_capabilities=sorted(disabled),
        )

    def resume(self) -> OnboardingStatus:
        """Reconcile completed history through actual owning-service checks.

        Pending IDs are never renewed. Missing/changed issuers keep durable
        history but cannot confer readiness or management after expiration.
        Native startup invokes this method; read-only GET routes use status().
        """
        self.connections.refresh()

        def reconcile(values, state):
            now = self._time()
            for stage in OnboardingStage:
                if stage == OnboardingStage.FIRST_ANSWER and state.options.ai_mode == "skip":
                    continue
                identifier = state.completed.get(stage)
                record = state.evidence.get(identifier) if identifier else None
                if (
                    record is None
                    or record.stage != stage
                    or record.issuer != self._issuer(stage)
                    or record.checked_at > now
                ):
                    break
                try:
                    actual = self._verify(stage, _State.model_validate(values))
                except ValueError:
                    values["error"] = "verification_unavailable"
                    break
                if actual != record.verification:
                    values["error"] = "verification_unavailable"
                    break
                values["evidence"][identifier] = record.model_copy(
                    update={
                        "checked_at": now,
                        "expires_at": now + self._ttl,
                    }
                ).model_dump(mode="json")
            else:
                values["error"] = None

        self._mutate(reconcile)
        return self.status()


def storage_verifier(engine: Engine) -> Callable[[], StageVerification | None]:
    """Selected-backend actual SELECT 1 verifier for trusted post-migration use."""
    probe = storage_probe(engine)
    fingerprint = hashlib.sha256(
        f"{engine.dialect.name}:{engine.url.database}".encode()
    ).hexdigest()

    def verify():
        if probe().state != "ready":
            return None
        return StageVerification(fingerprint=fingerprint)

    return verify
