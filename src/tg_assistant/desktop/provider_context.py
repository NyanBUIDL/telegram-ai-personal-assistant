"""Native provider ownership, fenced selections and original-age metadata health.

Saved settings are not evidence that a running inference engine was rebuilt.
Incompatible embedding choices remain pending until a confirmed reindex producer.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import asdict, replace
from threading import Condition, Event

from sqlalchemy import insert, select, text, update

from ..config import Settings, save_settings, validate_settings
from ..contracts import ConnectionService
from ..db.models import AppSetting
from ..paths import current_user_sid
from ..services.connections import ConnectionHealth
from ..services.maintenance import FileLock
from ..services.onboarding import StageVerification
from ..services.provider_connections import CredentialConnectionService, ModelSelection

ROLES = (ConnectionService.CHAT_AI, ConnectionService.EMBEDDINGS)


class _LazyStore:
    def __init__(self, factory):
        self.factory, self.store = factory, None

    def _get(self):
        if self.store is None:
            self.store = self.factory()
        return self.store

    def get(self, name):
        return self._get().get(name)

    def set(self, name, value):
        self._get().set(name, value)

    def delete(self, name):
        self._get().delete(name)


class _NativeOperations:
    def __init__(self, owner, measured):
        self.owner, self.measured = owner, measured

    def validate_and_save(self, provider, secret_input, options, *, cancel=None):
        cancel = cancel if cancel is not None else Event()
        with self.owner.operation(cancel):
            blocked = self.owner.credential_required(provider, secret_input, options)
            if blocked is not None:
                return blocked
            return self.measured.validate_and_save(provider, secret_input, options, cancel=cancel)

    def test_connection(self, provider, options):
        with self.owner.operation():
            blocked = self.owner.credential_required(provider, "", options)
            if blocked is not None:
                return blocked
            return self.measured.test_connection(provider, options)

    def disconnect(self, provider):
        with self.owner.operation():
            # An unconfigured profile must not delete another profile's global key.
            if provider in {"openai", "openrouter"}:
                with self.owner.engine.connect() as connection:
                    choices = self.owner._read(connection)
                if not any(item.provider == provider and item.connected for item in choices.values()):
                    return self.measured._status("chat_ai", "disconnected", "disconnected")
            return self.measured.disconnect(provider)

    def connection_status(self, service):
        return self.measured.connection_status(service)

    def verification_fingerprint(self, service):
        return self.measured.verification_fingerprint(service)

    def preview_local_download(self, model, *, endpoint):
        with self.owner.operation():
            return self.measured.preview_local_download(model, endpoint=endpoint)

    def pull_local_model(self, model, **options):
        with self.owner.operation(options["cancel"]):
            return self.measured.pull_local_model(model, **options)


class ProviderHealth(ConnectionHealth):
    """Other owners keep their caches; AI is composed directly with original TTL."""

    def __init__(self, *, provider, probes):
        super().__init__(probes=probes)
        self.provider = provider

    def status(self):
        result = super().status()
        return [
            self.provider.service.connection_status(row.service).model_copy(deep=True)
            if row.service in ROLES
            else row
            for row in result
        ]


class ProviderContext:
    activation_notice = (
        "Kiểm tra ở đây chỉ chứng minh metadata; chưa chạy AI. Lựa chọn đã lưu cần được "
        "ứng dụng xác nhận áp dụng. Cấu hình chat tương thích được ứng dụng đang chạy "
        "kiểm tra và áp dụng ở lượt làm mới (khoảng 15 giây); hãy xem lại trạng thái. "
        "Thay đổi chỉ mục embedding chờ xem trước và xác nhận tạo lại chỉ mục; "
        "không tự đổi model, số chiều hoặc kho dữ liệu đang dùng."
    )

    def __init__(self, settings, engine, fence, store_factory, **options):
        self._owned_settings, self.engine, self.fence = validate_settings(settings), engine, fence
        if engine.dialect.name != "sqlite":
            raise ValueError("storage_sqlite_required")
        self._sid = options.get("current_sid", current_user_sid)
        self._expected_sid = self._sid()
        if not self._expected_sid:
            raise ValueError("provider_sid_unavailable")
        self.settings = self._current_settings()
        self._sid_hash = hashlib.sha256(self._expected_sid.encode()).hexdigest()
        self._key = "native.provider." + hashlib.sha256(settings.profile_id.encode()).hexdigest()
        self._condition, self._closing, self._active = Condition(), False, []
        self._choices = self._unconfigured_defaults(self.settings)
        with fence.operation(), engine.connect() as connection:
            self._choices.update(self._read(connection))
        measured = CredentialConnectionService(
            persist_selection=self._persist,
            store=_LazyStore(store_factory),
            saved_selections=tuple(self._choices.values()),
            **options,
        )
        self.service = _NativeOperations(self, measured)

    @staticmethod
    def _defaults(settings):
        result = {}
        for role, provider, model in (
            (ConnectionService.CHAT_AI, settings.ai_provider, settings.active_ai_model),
            (
                ConnectionService.EMBEDDINGS,
                settings.embedding_provider,
                settings.active_embedding_model,
            ),
        ):
            if provider == "off":
                continue
            selection = ModelSelection.parse(
                provider,
                {
                    "service": role,
                    "model": model,
                    "endpoint": getattr(settings, provider + "_base_url"),
                    "cloud_consent": settings.cloud_consent,
                },
            )
            result[role] = selection
        return result

    def _same_sid(self):
        if self._sid() != self._expected_sid:
            raise ValueError("provider_sid_mismatch")

    @classmethod
    def _unconfigured_defaults(cls, settings):
        """Config supplies dialog defaults, never ownership of saved credentials."""
        return {role: replace(item, connected=False) for role, item in cls._defaults(settings).items()}

    def credential_required(self, provider, secret_input, options):
        if provider not in {"openai", "openrouter"} or secret_input:
            return None
        try:
            selection = ModelSelection.parse(provider, options)
        except (ValueError, TypeError):
            return None  # The measured service returns its sanitized selection error.
        with self.engine.connect() as connection:
            owned = self._read(connection).values()
        # Chat and embeddings share one provider key. Enrollment belongs to the
        # bound profile/SID, while each role still needs its own measured check.
        if not any(item.connected and item.provider == provider for item in owned):
            return self.service.measured._status(selection.service, "disconnected", "credential_required")
        return None

    def _current_settings(self):
        owned = self._owned_settings
        current = Settings(_env_file=None, data_dir=owned.data_dir, profile_id=owned.profile_id)
        if current.storage_backend != owned.storage_backend:
            raise ValueError("provider_storage_changed")
        self._same_sid()
        return current

    @contextmanager
    def operation(self, cancel=None):
        with self._condition:
            if self._closing:
                raise ValueError("provider_closed")
            marker = cancel if cancel is not None else Event()
            self._active.append(marker)
        try:
            self._same_sid()
            with self.fence.operation():
                yield
        finally:
            with self._condition:
                self._active.remove(marker)
                self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closing = True
            for marker in self._active:
                marker.set()
            self._condition.wait_for(lambda: not self._active)

    def drain(self):
        with self._condition:
            self._condition.wait_for(lambda: not self._active)

    def _read(self, connection, *, lock=False):
        query = select(AppSetting.value).where(AppSetting.key == self._key)
        raw = connection.execute(query).scalar_one_or_none()
        if raw is None:
            return {}
        if (
            not isinstance(raw, dict)
            or set(raw) != {"version", "profile_id", "sid_hash", "selections"}
            or raw["version"] != 1
            or raw["profile_id"] != self.settings.profile_id
            or raw["sid_hash"] != self._sid_hash
            or not isinstance(raw["selections"], dict)
        ):
            raise ValueError("provider_selection_invalid")
        result = {}
        for role, saved in raw["selections"].items():
            if not isinstance(saved, dict) or set(saved) != {
                "provider",
                "service",
                "model",
                "endpoint",
                "cloud_consent",
                "connected",
                "verified",
            }:
                raise ValueError("provider_selection_invalid")
            checked = ModelSelection.parse(
                saved["provider"],
                {key: saved[key] for key in ("service", "model", "endpoint", "cloud_consent")},
            )
            if (
                role != checked.service.value
                or type(saved["connected"]) is not bool
                or saved["verified"] is not False
            ):
                raise ValueError("provider_selection_invalid")
            result[checked.service] = replace(checked, connected=saved["connected"])
        return result

    def _persist(self, selection):
        self._same_sid()
        with (
            self.fence.operation(),
            FileLock(self.fence.root / "provider-settings.lock", exclusive=True, timeout=1),
        ):
            current = self._current_settings()
            updates = {}
            if selection.service == ConnectionService.CHAT_AI:
                updates = {"ai_provider": selection.provider if selection.connected else "off"}
                if selection.connected:
                    updates.update(
                        {
                            selection.provider + "_primary_model": selection.model,
                            selection.provider + "_base_url": selection.endpoint,
                        }
                    )
                    if selection.provider != "ollama":
                        updates["cloud_consent"] = selection.cloud_consent
            elif not selection.connected and selection.provider == current.embedding_provider:
                # The existing runtime reads this gate before embedding work.
                # Disconnect closes it without changing corpus identity.
                updates = {"enable_embeddings": False}
            # Embedding identity is a pending choice until preview/confirmed reindex.
            candidate = validate_settings(current.model_copy(update=updates))
            if candidate.embedding_profile.store_id != current.embedding_profile.store_id:
                # Provider endpoints are shared config fields. A chat endpoint
                # change must also remain pending if it changes the active corpus.
                updates, candidate = {}, current
            wrote_config = False
            with self.engine.connect() as connection:
                connection.execute(text("BEGIN IMMEDIATE"))
                choices = self._unconfigured_defaults(current) | self._read(connection, lock=True)
                choices[selection.service] = replace(selection, verified=False)
                payload = {
                    "version": 1,
                    "profile_id": self.settings.profile_id,
                    "sid_hash": self._sid_hash,
                    "selections": {role.value: asdict(item) for role, item in choices.items()},
                }
                try:
                    self._same_sid()
                    if updates:
                        save_settings(candidate)
                        wrote_config = True
                    exists = connection.scalar(
                        select(AppSetting.key).where(AppSetting.key == self._key)
                    )
                    statement = (
                        update(AppSetting).where(AppSetting.key == self._key).values(value=payload)
                        if exists
                        else insert(AppSetting).values(key=self._key, value=payload)
                    )
                    connection.execute(statement)
                    self._same_sid()
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    if wrote_config:
                        self._same_sid()
                        save_settings(current)
                    raise
            self.settings, self._choices = candidate, choices

    def dialog_options(self, role):
        role = ConnectionService(role)
        selection = self._choices.get(role)
        if selection is None:
            defaults = self.settings.model_copy(
                update={"ai_provider": "openai", "embedding_provider": "ollama"}
            )
            selection = self._defaults(defaults)[role]
        return {
            "provider": selection.provider,
            "service_name": role.value,
            "model": selection.model,
            "endpoint": selection.endpoint,
            "cloud_consent": selection.cloud_consent,
        }

    def refresh_saved_health(self, *, active_settings=None):
        """Worker-owned metadata probes, never persisted READY or paid inference."""
        with self.operation():
            current = self._current_settings()
            with self.engine.connect() as connection:
                choices = self._unconfigured_defaults(current) | self._read(connection)
            self.settings, self._choices = current, choices
            self.service.measured.refresh_selections(tuple(choices.values()))
            running = active_settings if active_settings is not None else current
            actual = self._defaults(running)
            for role in ROLES:
                item = choices.get(role)
                if item is None or not item.connected:
                    continue
                result = self.service.measured.test_connection(
                    item.provider,
                    {
                        "service": role,
                        "model": item.model,
                        "endpoint": item.endpoint,
                        "cloud_consent": item.cloud_consent,
                    },
                )
                if actual.get(role) != replace(item, verified=False):
                    # The requested identity was checked, but is not active. No store switch.
                    embedding = role == ConnectionService.EMBEDDINGS
                    result = result.model_copy(
                        update={
                            "state": "degraded",
                            "code": "provider_reindex_required"
                            if embedding
                            else "provider_activation_required",
                            "message": "Lựa chọn đã lưu chưa áp dụng vào chức năng đang chạy.",
                            "next_action": "Xem trước và xác nhận tạo lại chỉ mục trước khi đổi kho."
                            if embedding
                            else "Đợi ứng dụng áp dụng lựa chọn hoặc dừng an toàn rồi mở lại.",
                            "capabilities": [],
                        }
                    )
                    self.service.measured._cache[role] = result
                elif role == ConnectionService.EMBEDDINGS and (
                    not running.enable_embeddings or running.ai_provider == "off"
                ):
                    # Metadata availability cannot override the runtime's feature gate.
                    self.service.measured._cache[role] = result.model_copy(
                        update={
                            "state": "degraded",
                            "code": "embedding_disabled",
                            "message": "Embedding đang bị tắt trong cấu hình đang chạy.",
                            "next_action": "Bật AI và embedding trong cấu hình, rồi xác nhận áp dụng.",
                            "capabilities": [],
                        }
                    )

    def configuration_verification(self):
        """Metadata configuration only; never inference, runtime activation or dimension proof."""
        self._same_sid()
        current = self._current_settings()
        actual = self._defaults(current)
        if any(self._choices.get(role) != actual.get(role) for role in ROLES):
            return None
        fingerprints = [self.service.verification_fingerprint(role) for role in ROLES]
        if not all(fingerprints):
            return None
        return StageVerification(
            fingerprint=hashlib.sha256(":".join(fingerprints).encode()).hexdigest()
        )
