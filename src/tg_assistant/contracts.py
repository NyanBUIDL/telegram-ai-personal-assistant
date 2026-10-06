"""Versioned contracts for Windows bootstrap services and the local dashboard.

PublicDTOs contain no credential fields. Python stores Telegram IDs as ints;
their JSON/schema representation is always a canonical decimal string. Internal
session, ticket and lease objects are intentionally absent from the public schema.
This module defines interfaces, not authority checks or service implementations.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Protocol, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    SerializerFunctionWrapHandler,
    StrictBool,
    StrictInt,
    ValidationError,
    ValidationInfo,
    WithJsonSchema,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic_core import PydanticSerializationError

CONTRACT_VERSION = 2
NATIVE_PAYLOAD_MAX_BYTES = 8192
NATIVE_PAYLOAD_MAX_DEPTH = 16
IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
TELEGRAM_ID_PATTERN = r"^-?[1-9][0-9]*$"
OWNER_ID_PATTERN = r"^[1-9][0-9]*$"
# Normalized keys: case and punctuation cannot bypass the bridge guard.
SENSITIVE_KEY_PARTS = (
    "secret", "password", "token", "apikey", "apihash", "credential", "authorization",
    "otp", "twofactor", "2fa", "rawticket", "claimtoken", "session", "cookie",
)
NATIVE_TARGET_KEYS = ("path", "url", "uri", "executable", "command", "shell", "args", "argv")


def _telegram_id(value: object, info: ValidationInfo) -> int:
    if info.mode != "json" and type(value) is int and value != 0:
        return value
    if isinstance(value, str) and re.fullmatch(TELEGRAM_ID_PATTERN, value):
        return int(value)
    raise ValueError("JSON Telegram IDs must be decimal strings; Python IDs must be nonzero ints")


def _owner_id(value: int) -> int:
    if value <= 0:
        raise ValueError("Verified owner ID must be positive")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    return value.astimezone(UTC)


TelegramId = Annotated[
    int, BeforeValidator(_telegram_id), PlainSerializer(str, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": TELEGRAM_ID_PATTERN}, mode="serialization"),
]
OwnerId = Annotated[
    TelegramId, AfterValidator(_owner_id),
    WithJsonSchema({"type": "string", "pattern": OWNER_ID_PATTERN}, mode="serialization"),
]
Timestamp = Annotated[datetime, AfterValidator(_utc)]
Identifier = Annotated[str, Field(strict=True, pattern=IDENTIFIER_PATTERN)]
Text = Annotated[str, Field(strict=True, max_length=2048)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
Progress = Annotated[StrictInt, Field(ge=0, le=100)]


class ConnectionState(StrEnum):
    CHECKING = "checking"
    READY = "ready"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"
    UNKNOWN = "unknown"


class ConnectionService(StrEnum):
    TELEGRAM_ACCOUNT = "telegram_account"
    CONTROL_BOT = "control_bot"
    CHAT_AI = "chat_ai"
    EMBEDDINGS = "embeddings"
    STORAGE = "storage"
    RUNTIME = "runtime"


class StorageBackend(StrEnum):
    SQLITE = "sqlite"


class OnboardingStage(StrEnum):
    WELCOME = "welcome"
    STORAGE_READY = "storage_ready"
    AI_CONFIGURED = "ai_configured"
    TELEGRAM_VERIFIED = "telegram_verified"
    BOT_VERIFIED = "bot_verified"
    OWNER_PAIRED = "owner_paired"
    SOURCE_SELECTED = "source_selected"
    FIRST_ANSWER = "first_answer"
    READY = "ready"


class OperationState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNING = "completed_with_warning"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


class NativeCommandName(StrEnum):
    OPEN_CONNECTION_DIALOG = "open_connection_dialog"
    OPEN_TELEGRAM_LOGIN = "open_telegram_login"
    OPEN_BOT_DIALOG = "open_bot_dialog"
    ISSUE_DASHBOARD_TICKET = "issue_dashboard_ticket"
    RUNTIME_START = "runtime_start"
    RUNTIME_STOP = "runtime_stop"


class SessionAuthority(StrEnum):
    SETUP_ONLY = "setup_only"
    MANAGEMENT = "management"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True)


class PublicDTO(ContractModel):
    """Only subclasses explicitly registered below may cross the public boundary."""

    model_config = ConfigDict(revalidate_instances="always")

    @model_serializer(mode="wrap")
    def serialize_validated_public_dto(self, handler: SerializerFunctionWrapHandler):
        # frozen=True does not freeze lists/maps, including those in nested DTOs.
        # Revalidation creates a checked snapshot before any serializer can warn
        # about or return an unexpected value. Always revalidate nested instances.
        try:
            validated = type(self).model_validate(self)
        except ValidationError:
            # Validation paths and input-bearing diagnostics must not cross this
            # boundary. Suppress the original error's traceback context as well.
            raise PydanticSerializationError("Public DTO failed validation before serialization") from None
        return handler(validated)


class ConnectionStatus(PublicDTO):
    service: ConnectionService
    state: ConnectionState
    checked_at: Timestamp | None
    code: Identifier
    message: Text
    next_action: Text | None
    capabilities: list[Identifier]


class PublicProfile(PublicDTO):
    profile_id: Identifier
    owner_id: OwnerId | None
    storage_backend: StorageBackend
    setup_stage: OnboardingStage
    version: PositiveInt

    @model_validator(mode="after")
    def verified_stage_has_owner(self) -> Self:
        unverified = {OnboardingStage.WELCOME, OnboardingStage.STORAGE_READY, OnboardingStage.AI_CONFIGURED}
        if self.setup_stage not in unverified and self.owner_id is None:
            raise ValueError("Verified Telegram stages require a verified owner ID")
        return self


class EmbeddingProfile(PublicDTO):
    provider: Identifier
    endpoint_id: Identifier | None
    model: Annotated[str, Field(strict=True, min_length=1, max_length=256)]
    embedding_version: Identifier
    dimension: PositiveInt
    store_id: Identifier
    cloud_consent: StrictBool


class OperationResult(PublicDTO):
    operation_id: Identifier
    state: OperationState
    progress: Progress | None
    code: Identifier
    message: Text
    next_action: Text | None


def _guard_native_payload(value: object, depth: int = 0) -> None:
    if depth > NATIVE_PAYLOAD_MAX_DEPTH:
        raise ValueError("Native payload nesting exceeds the limit")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("Native payload keys must be strings")
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if any(part in normalized for part in (*SENSITIVE_KEY_PARTS, *NATIVE_TARGET_KEYS)):
                raise ValueError("Native payload contains a prohibited field")
            _guard_native_payload(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _guard_native_payload(child, depth + 1)
    elif isinstance(value, str):
        # The bridge requests a native dialog; endpoints are configured natively.
        # Reject URL targets altogether, including userinfo/query/fragment secrets.
        if re.search(r"[A-Za-z][A-Za-z0-9+.-]*://", value):
            raise ValueError("Native payload cannot contain URL targets")
    elif value is not None and type(value) not in {bool, int, float}:
        raise ValueError("Native payload must be JSON data")


class NativeCommand(PublicDTO):
    name: NativeCommandName
    request_id: Identifier
    profile_id: Identifier
    payload_nonsecret: dict[str, JsonValue] = Field(json_schema_extra={"x-native-payload": True})

    @field_validator("payload_nonsecret", mode="before")
    @classmethod
    def validate_nonsecret_payload(cls, value: object) -> object:
        _guard_native_payload(value)
        try:
            size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Native payload must be finite JSON data") from exc
        if size > NATIVE_PAYLOAD_MAX_BYTES:
            raise ValueError("Native payload exceeds the byte limit")
        return value

    @field_serializer("payload_nonsecret")
    def serialize_nonsecret_payload(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        # Frozen BaseModels still contain mutable dicts. Recheck the wire boundary.
        self.validate_nonsecret_payload(value)
        return value


class OnboardingStatus(PublicDTO):
    profile: PublicProfile
    connections: list[ConnectionStatus]
    stage_evidence_ids: dict[OnboardingStage, Identifier]
    next_action: Text | None
    disabled_capabilities: list[Identifier]


class RecoveryPlan(PublicDTO):
    plan_id: Identifier
    chat_id: TelegramId
    store_id: Identifier
    authorization_epoch: NonNegativeInt
    index_generation: NonNegativeInt
    expected_count: NonNegativeInt
    expires_at: Timestamp


class RevocationReport(PublicDTO):
    chat_id: TelegramId
    authorization_epoch: NonNegativeInt
    cancelled_jobs: NonNegativeInt
    memory_action: Identifier
    operation_id: Identifier | None


class MigrationReport(PublicDTO):
    previous_revision: Identifier | None
    current_revision: Identifier
    changed: StrictBool
    code: Identifier
    next_action: Text | None


class BackupManifest(PublicDTO):
    format_version: PositiveInt
    schema_revision: Identifier
    backend: StorageBackend
    profile_id: Identifier
    checksums: dict[str, Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]]
    vector_state: Identifier


# Internal process/IPC contracts: never include these in a status endpoint or JS.
class LaunchTicket(ContractModel):
    raw_ticket: str = Field(repr=False)
    expires_at: Timestamp


class AdminSession(ContractModel):
    session_id: str = Field(repr=False)
    profile_id: Identifier
    owner_id: OwnerId | None
    authority: SessionAuthority
    expires_at: Timestamp

    @model_validator(mode="after")
    def management_requires_owner(self) -> Self:
        if self.authority == SessionAuthority.MANAGEMENT and self.owner_id is None:
            raise ValueError("Management authority requires verified owner and pairing evidence")
        return self


class JobLease(ContractModel):
    id: Identifier
    claim_token: str = Field(repr=False)
    payload: dict[str, JsonValue] = Field(repr=False)
    expires_at: Timestamp
    authorization_epoch: NonNegativeInt


class MaintenanceLease(ContractModel):
    profile_id: Identifier
    generation: NonNegativeInt
    holder: Identifier
    expires_at: Timestamp
    state: Annotated[str, Field(pattern=r"^writers_fenced$")] = "writers_fenced"


PUBLIC_CONTRACTS: tuple[type[PublicDTO], ...] = (
    ConnectionStatus, PublicProfile, EmbeddingProfile, OperationResult, NativeCommand,
    OnboardingStatus, RecoveryPlan, RevocationReport, MigrationReport, BackupManifest,
)


def public_contract_schema() -> dict[str, object]:
    """Serialization schemas are the browser contract; Python validation accepts int IDs."""
    return {
        "version": CONTRACT_VERSION,
        "native_payload_max_bytes": NATIVE_PAYLOAD_MAX_BYTES,
        "native_payload_max_depth": NATIVE_PAYLOAD_MAX_DEPTH,
        "sensitive_key_parts": list(SENSITIVE_KEY_PARTS),
        "native_target_keys": list(NATIVE_TARGET_KEYS),
        "unverified_stages": ["welcome", "storage_ready", "ai_configured"],
        "contracts": {model.__name__: model.model_json_schema(mode="serialization") for model in PUBLIC_CONTRACTS},
    }


# Synchronous interface signatures only; implementations own authentication,
# evidence, compare-and-set, expiry, consent and side-effect handling.
class Database(Protocol):
    """Opaque storage handle supplied by the storage implementation."""


class RestoreReport(Protocol):
    """Restore result is internal until its owning task proposes a public contract."""


class OnboardingCoordinator(Protocol):
    def status(self) -> OnboardingStatus: ...
    def resume(self) -> OnboardingStatus: ...
    def complete_stage(self, stage: OnboardingStage, verified_evidence_id: str) -> OnboardingStatus: ...


class CredentialConnectionService(Protocol):
    def validate_and_save(self, provider: str, secret_input: str, options: dict[str, JsonValue]) -> ConnectionStatus: ...


class DashboardTicketService(Protocol):
    def issue(self, profile_id: str, windows_sid: str, now: datetime) -> LaunchTicket: ...
    def redeem(self, ticket: str, origin: str, now: datetime) -> AdminSession: ...


class StorageService(Protocol):
    def open(self, profile: PublicProfile) -> Database: ...
    def migrate(self) -> MigrationReport: ...
    def backup(self, destination: str) -> BackupManifest: ...
    def restore(self, source: str, maintenance_lease: MaintenanceLease) -> RestoreReport: ...


class JobRepository(Protocol):
    def claim(self, job_type: str, worker_id: str, now: datetime, lease_seconds: int) -> JobLease | None: ...
    def complete(self, lease: JobLease, result: OperationResult) -> bool: ...


class RevocationService(Protocol):
    def revoke_source(self, chat_id: int, actor_id: int, memory_action: str) -> RevocationReport: ...


class VectorRecoveryService(Protocol):
    def preview(self, chat_id: int, store_id: str) -> RecoveryPlan: ...
    def enqueue(self, plan_id: str, owner_id: int) -> str: ...
