"""Native-only current-SID metadata checks and explicit consented local pulls.

The runtime supplies a fenced settings writer. Metadata readiness never means a
paid inference succeeded. This service never switches an embedding corpus.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from threading import Event, RLock
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from ..contracts import ConnectionService, ConnectionState, ConnectionStatus, OperationResult
from ..paths import current_user_sid
from ..security import SecretStore, contains_secret
from .connections import HealthObservation
from .ollama import OllamaService

ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}
KEY_NAMES = {"openai": "openai_api_key", "openrouter": "openrouter_api_key"}
MODEL_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,191}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$"
)
_MESSAGES = {
    "metadata_verified": (
        "Đã kiểm tra quyền truy cập và metadata model; chưa chạy tạo nội dung.",
        None,
    ),
    "capability_unknown": (
        "Xác thực được nhưng metadata chưa chứng minh khả năng model đã chọn.",
        "Chọn model khác hoặc lưu chưa xác minh. Kiểm tra tạo nội dung cần xác nhận riêng.",
    ),
    "capability_unsupported": (
        "Model đã chọn không hỗ trợ chức năng này theo metadata.",
        "Chọn model phù hợp hoặc dùng tìm kiếm từ khóa có giới hạn.",
    ),
    "model_unavailable": (
        "Không tìm thấy model đã chọn trong danh sách khả dụng.",
        "Chọn model khả dụng; với Ollama, tải model sau khi xác nhận dung lượng.",
    ),
    "authentication_failed": (
        "Nhà cung cấp từ chối API key hoặc quyền truy cập.",
        "Nhập lại key, kiểm tra quyền của key; key đang lưu vẫn được giữ.",
    ),
    "credential_required": (
        "Chưa có API key để kiểm tra.",
        "Nhập key trong ô native của ứng dụng Windows.",
    ),
    "credential_invalid": (
        "API key không hợp lệ.",
        "Nhập một key không có khoảng trắng hoặc ký tự điều khiển.",
    ),
    "provider_unavailable": (
        "Không kết nối được nhà cung cấp trong thời gian giới hạn.",
        "Kiểm tra mạng hoặc ứng dụng Ollama, rồi thử lại.",
    ),
    "quota_limited": (
        "Nhà cung cấp báo hạn mức hoặc quota bị giới hạn.",
        "Kiểm tra quota tại nhà cung cấp; không tự gửi yêu cầu có phí.",
    ),
    "quota_unknown": (
        "Xác thực được nhưng chưa biết quota của key.",
        "Kiểm tra quota tại nhà cung cấp; có thể lưu chưa xác minh.",
    ),
    "selection_invalid": (
        "Lựa chọn provider, endpoint hoặc model không hợp lệ.",
        "Chọn endpoint cloud chính thức hoặc Ollama loopback và model hợp lệ.",
    ),
    "cloud_consent_required": (
        "Cần đồng ý sử dụng cloud trước khi kiểm tra kết nối cloud.",
        "Xác nhận lựa chọn cloud. Phạm vi nguồn và ngân sách vẫn phải xác nhận riêng.",
    ),
    "saved_unverified": (
        "Đã lưu lựa chọn chưa xác minh; chức năng AI chưa sẵn sàng.",
        "Kiểm tra lại kết nối trước khi sử dụng AI.",
    ),
    "save_failed": (
        "Không lưu được kết nối; không xác nhận sẵn sàng.",
        "Kiểm tra Credential Manager và cấu hình rồi thử lại.",
    ),
    "rollback_failed": (
        "Lưu bị lỗi và trạng thái credential chưa chắc chắn.",
        "Kiểm tra hoặc ngắt kết nối trong ứng dụng Windows trước khi tiếp tục.",
    ),
    "disconnected": ("Đã ngắt kết nối provider.", "Kết nối lại để sử dụng AI."),
    "disconnect_failed": (
        "Đã thu hồi trạng thái sẵn sàng nhưng chưa hoàn tất xóa credential hoặc cấu hình.",
        "Thử ngắt kết nối lại trong ứng dụng Windows.",
    ),
    "sid_mismatch": (
        "Không thể xác minh cùng tài khoản Windows.",
        "Mở lại ứng dụng bằng tài khoản Windows sở hữu hồ sơ.",
    ),
    "health_unknown": (
        "Chưa có kiểm tra kết nối còn hiệu lực.",
        "Kiểm tra lại kết nối trong ứng dụng Windows.",
    ),
    "check_cancelled": ("Đã hủy kiểm tra; không lưu kết nối.", "Mở lại kết nối khi muốn tiếp tục."),
    "local_model_required": (
        "Model Ollama này dùng máy chủ từ xa; không đáp ứng chế độ local.",
        "Chọn model đã tải và chạy hoàn toàn trên máy này.",
    ),
    "local_model_unknown": (
        "Chưa chứng minh model Ollama nằm trên máy này.",
        "Chọn model local có thông tin dung lượng đã tải rồi kiểm tra lại.",
    ),
}


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """Internal nonsecret settings-writer input; never browser authority."""

    provider: str
    service: ConnectionService
    model: str
    endpoint: str
    cloud_consent: bool
    verified: bool = False
    connected: bool = True

    @property
    def endpoint_id(self) -> str:
        return f"{self.provider}-{hashlib.sha256(self.endpoint.encode()).hexdigest()[:16]}"

    @classmethod
    def parse(cls, provider: str, options: dict) -> ModelSelection:
        allowed = {"service", "model", "endpoint", "cloud_consent", "allow_unverified"}
        if provider not in ENDPOINTS or not isinstance(options, dict) or set(options) - allowed:
            raise ValueError("selection_invalid")
        service = ConnectionService(options.get("service", "chat_ai"))
        if service not in {ConnectionService.CHAT_AI, ConnectionService.EMBEDDINGS}:
            raise ValueError("selection_invalid")
        model = options.get("model")
        if (
            not isinstance(model, str)
            or not MODEL_PATTERN.fullmatch(model)
            or ".." in model
            or contains_secret(model)
        ):
            raise ValueError("selection_invalid")
        endpoint = options.get("endpoint", ENDPOINTS[provider])
        if not isinstance(endpoint, str):
            raise ValueError("selection_invalid")
        endpoint = endpoint.rstrip("/")
        if provider in KEY_NAMES:
            if endpoint != ENDPOINTS[provider]:
                raise ValueError("selection_invalid")
        else:
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.path != "/v1"
                or not parsed.port
                or not 1 <= parsed.port <= 65535
            ):
                raise ValueError("selection_invalid")
            # Do not resolve a supplied host; localhost is pinned to a literal.
            host = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
            endpoint = f"http://{host}:{parsed.port}/v1"
        consent = options.get("cloud_consent", False)
        if type(consent) is not bool or type(options.get("allow_unverified", False)) is not bool:
            raise ValueError("selection_invalid")
        return cls(provider, service, model, endpoint, consent)


class _ProbeFailure(Exception):
    def __init__(self, code):
        self.code = code


class CredentialConnectionService:
    """Native service; settings writer must be transactional and maintenance-fenced.

    connected=False disconnects the selected service. The writer must retain the
    independent embedding profile and require confirmation before any reindex.
    The store is read only inside native operations, never for prefilling UI.
    """

    def __init__(
        self,
        *,
        persist_selection: Callable[[ModelSelection], None],
        store=None,
        transport: httpx.BaseTransport | None = None,
        current_sid: Callable[[], str] = current_user_sid,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        ttl_seconds: int = 60,
        timeout_seconds: float = 8,
        saved_selections: tuple[ModelSelection, ...] = (),
    ):
        if (
            not callable(persist_selection)
            or not 1 <= ttl_seconds <= 3600
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("provider_options_invalid")
        self._store = store if store is not None else SecretStore()
        self._persist = persist_selection
        self._sid = current_sid
        self._expected_sid = current_sid()
        if not self._expected_sid:
            raise ValueError("sid_unavailable")
        self._now, self._ttl = now, timedelta(seconds=ttl_seconds)
        self._transport, self._timeout = transport, timeout_seconds
        self._lock = RLock()
        self._selections: dict[ConnectionService, ModelSelection] = {}
        self._cache: dict[ConnectionService, ConnectionStatus] = {}
        self._download_previews = {}
        for saved in saved_selections:
            selected = ModelSelection.parse(
                saved.provider,
                {
                    "service": saved.service,
                    "model": saved.model,
                    "endpoint": saved.endpoint,
                    "cloud_consent": saved.cloud_consent,
                },
            )
            if saved.connected:
                self._selections[selected.service] = selected

    def _same_sid(self):
        try:
            return bool(self._expected_sid) and self._sid() == self._expected_sid
        except Exception:
            return False

    def _time(self):
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock_invalid")
        return value.astimezone(UTC)

    def _status(self, service, state, code, capabilities=()):
        message, action = _MESSAGES[code]
        return ConnectionStatus(
            service=service,
            state=state,
            checked_at=self._time(),
            code=code,
            message=message,
            next_action=action,
            capabilities=list(capabilities),
        )

    def _request(self, client, method, url, **kwargs):
        if not self._same_sid():
            raise _ProbeFailure("sid_mismatch")
        try:
            deadline = monotonic() + self._timeout
            with client.stream(method, url, **kwargs) as response:
                if response.status_code in {401, 403}:
                    raise _ProbeFailure("authentication_failed")
                if response.status_code in {402, 429}:
                    raise _ProbeFailure("quota_limited")
                if response.status_code == 404:
                    raise _ProbeFailure("model_unavailable")
                if response.status_code != 200:
                    raise _ProbeFailure("provider_unavailable")
                payload = bytearray()
                for chunk in response.iter_bytes():
                    if monotonic() > deadline:
                        raise _ProbeFailure("provider_unavailable")
                    payload.extend(chunk)
                    if len(payload) > 4 * 1024 * 1024:
                        raise _ProbeFailure("provider_unavailable")
                result = json.loads(payload)
                if not isinstance(result, dict):
                    raise _ProbeFailure("provider_unavailable")
                if not self._same_sid():
                    raise _ProbeFailure("sid_mismatch")
                return result
        except _ProbeFailure:
            raise
        except Exception:
            raise _ProbeFailure("provider_unavailable") from None

    def _measure(self, selection, secret):
        headers = {"Accept": "application/json"}
        if selection.provider in KEY_NAMES:
            headers["Authorization"] = "Bearer " + secret
        # Environment proxies and credential-bearing redirects are disabled.
        with httpx.Client(
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
            headers=headers,
        ) as client:
            if selection.provider == "ollama":
                base = selection.endpoint[:-3]
                listed = self._request(client, "GET", base + "/api/tags").get("models")
                if not isinstance(listed, list):
                    raise _ProbeFailure("provider_unavailable")
                local = next(
                    (
                        item
                        for item in listed
                        if isinstance(item, dict) and item.get("name") == selection.model
                    ),
                    None,
                )
                if local is None:
                    raise _ProbeFailure("model_unavailable")
                if local.get("remote_host") or local.get("remote_model"):
                    return self._status(selection.service, "degraded", "local_model_required")
                if type(local.get("size")) is not int or local["size"] <= 0:
                    return self._status(selection.service, "degraded", "local_model_unknown")
                shown = self._request(
                    client, "POST", base + "/api/show", json={"model": selection.model}
                )
                if shown.get("remote_host") or shown.get("remote_model"):
                    return self._status(selection.service, "degraded", "local_model_required")
                caps = shown.get("capabilities")
                wanted = (
                    "completion" if selection.service == ConnectionService.CHAT_AI else "embedding"
                )
                if not isinstance(caps, list):
                    return self._status(selection.service, "degraded", "capability_unknown")
                if wanted not in caps:
                    return self._status(selection.service, "degraded", "capability_unsupported")
            else:
                quota_known = False
                if selection.provider == "openrouter":
                    key = self._request(client, "GET", selection.endpoint + "/key").get("data")
                    if not isinstance(key, dict):
                        raise _ProbeFailure("provider_unavailable")
                    remaining = key.get("limit_remaining")
                    if type(remaining) in {float, int}:
                        if remaining <= 0:
                            return self._status(selection.service, "degraded", "quota_limited")
                        quota_known = True
                    # Null means no key cap; it does not prove account credit.
                    if (
                        key.get("is_management_key") is True
                        or key.get("is_provisioning_key") is True
                    ):
                        raise _ProbeFailure("authentication_failed")
                path = (
                    "/embeddings/models"
                    if selection.provider == "openrouter"
                    and selection.service == ConnectionService.EMBEDDINGS
                    else "/models"
                )
                listed = self._request(client, "GET", selection.endpoint + path).get("data")
                if not isinstance(listed, list):
                    raise _ProbeFailure("provider_unavailable")
                model = next(
                    (
                        item
                        for item in listed
                        if isinstance(item, dict) and item.get("id") == selection.model
                    ),
                    None,
                )
                if model is None:
                    raise _ProbeFailure("model_unavailable")
                if selection.provider == "openai":
                    # OpenAI /models defines no capability or quota fields.
                    return self._status(
                        selection.service,
                        "degraded",
                        "capability_unknown",
                        ("authenticated", "model_available"),
                    )
                if selection.service == ConnectionService.CHAT_AI:
                    architecture = model.get("architecture")
                    if not isinstance(architecture, dict) or not isinstance(
                        architecture.get("output_modalities"), list
                    ):
                        return self._status(selection.service, "degraded", "capability_unknown")
                    if "text" not in architecture[
                        "output_modalities"
                    ] or "text" not in architecture.get("input_modalities", []):
                        return self._status(selection.service, "degraded", "capability_unsupported")
                # Presence in /embeddings/models proves embedding metadata.
                if not quota_known:
                    return self._status(
                        selection.service,
                        "degraded",
                        "quota_unknown",
                        ("authenticated", "model_available"),
                    )
        capability = (
            "chat_metadata"
            if selection.service == ConnectionService.CHAT_AI
            else "embedding_metadata"
        )
        return self._status(
            selection.service, "ready", "metadata_verified", (capability, "model_available")
        )

    def _check(self, provider, secret_input, options):
        service = options.get("service", "chat_ai") if isinstance(options, dict) else "chat_ai"
        if service not in {"chat_ai", "embeddings"}:
            service = "chat_ai"
        if not self._same_sid():
            return None, None, self._status(service, "disconnected", "sid_mismatch")
        try:
            selection = ModelSelection.parse(provider, options)
        except (ValueError, TypeError):
            return None, None, self._status(service, "disconnected", "selection_invalid")
        if provider != "ollama" and not selection.cloud_consent:
            return selection, None, self._status(service, "disconnected", "cloud_consent_required")
        if (
            not isinstance(secret_input, str)
            or len(secret_input) > 4096
            or any(c.isspace() or ord(c) < 32 or ord(c) > 126 for c in secret_input)
        ):
            return selection, None, self._status(service, "disconnected", "credential_invalid")
        secret, saved_secret = None, None
        try:
            if not self._same_sid():
                return selection, None, self._status(service, "disconnected", "sid_mismatch")
            if provider in KEY_NAMES:
                saved_secret = self._store.get(KEY_NAMES[provider])
                secret = secret_input or saved_secret
                if not saved_secret:
                    if not self._same_sid():
                        return (
                            selection,
                            None,
                            self._status(service, "disconnected", "sid_mismatch"),
                        )
                    missing = self._status(service, "disconnected", "credential_required")
                    self._record_failed_measurement(selection, None, None, missing)
                    if not secret:
                        return selection, None, missing
            else:
                secret = ""
            result = self._measure(selection, secret)
        except _ProbeFailure as exc:
            state = "degraded" if exc.code == "quota_limited" else "disconnected"
            result = self._status(service, state, exc.code)
        except Exception:
            return selection, None, self._status(service, "disconnected", "save_failed")
        if not self._same_sid():
            return selection, None, self._status(service, "disconnected", "sid_mismatch")
        if result.state != ConnectionState.READY:
            self._record_failed_measurement(selection, secret, saved_secret, result)
        return selection, secret, result

    def _record_failed_measurement(self, selection, secret, saved_secret, result):
        """Contrary evidence applies only to the credential/model actually checked."""
        missing_credential = result.code == "credential_required" and not saved_secret
        if (
            selection.provider in KEY_NAMES
            and not missing_credential
            and (not saved_secret or secret != saved_secret)
        ):
            # A rejected replacement has not measured the configured credential.
            return
        for role, configured in self._selections.items():
            shared_credential_failure = (
                selection.provider in KEY_NAMES
                and result.code in {"authentication_failed", "credential_required"}
                and configured.provider == selection.provider
            )
            same_selection = selection == replace(configured, verified=False)
            if shared_credential_failure or same_selection:
                self._cache[role] = result.model_copy(deep=True, update={"service": role})
                self._selections[role] = replace(configured, verified=False)

    def validate_and_save(
        self, provider: str, secret_input: str, options: dict, *, cancel: Event | None = None
    ) -> ConnectionStatus:
        with self._lock:
            if cancel is not None and cancel.is_set():
                return self._status("chat_ai", "disconnected", "check_cancelled")
            selection, secret, result = self._check(provider, secret_input, options)
            if cancel is not None and cancel.is_set():
                return self._status(result.service, "disconnected", "check_cancelled")
            unverified_codes = {"provider_unavailable", "capability_unknown", "quota_unknown"}
            verified = result.state == ConnectionState.READY
            unverified = bool(
                selection
                and options.get("allow_unverified") is True
                and result.code in unverified_codes
            )
            if selection is None or secret is None or not (verified or unverified):
                return result
            selection = replace(selection, verified=verified)
            previous, changed = None, False
            try:
                if not self._same_sid():
                    return self._status(selection.service, "disconnected", "sid_mismatch")
                if provider in KEY_NAMES:
                    previous = self._store.get(KEY_NAMES[provider])
                    if secret_input:
                        if not self._same_sid():
                            raise _ProbeFailure("sid_mismatch")
                        changed = True
                        self._store.set(KEY_NAMES[provider], secret)
                if not self._same_sid():
                    raise _ProbeFailure("sid_mismatch")
                self._persist(selection)
                if not self._same_sid():
                    raise _ProbeFailure("sid_mismatch")
            except Exception:
                code = "save_failed"
                if changed:
                    try:
                        if not self._same_sid():
                            raise _ProbeFailure("sid_mismatch")
                        if previous is None:
                            self._store.delete(KEY_NAMES[provider])
                        else:
                            self._store.set(KEY_NAMES[provider], previous)
                    except Exception:
                        code = "rollback_failed"
                self._cache.pop(selection.service, None)
                if changed:
                    for other, configured in self._selections.items():
                        if configured.provider == provider:
                            self._cache.pop(other, None)
                return self._status(selection.service, "disconnected", code)
            if unverified:
                result = self._status(selection.service, "unknown", "saved_unverified")
            if changed and previous != secret:
                for other, configured in self._selections.items():
                    if configured.provider == provider and other != selection.service:
                        self._cache.pop(other, None)
            self._selections[selection.service] = selection
            self._cache[selection.service] = result.model_copy(deep=True)
            return result

    def test_connection(self, provider: str, options: dict) -> ConnectionStatus:
        """Probe saved credential without settings writes or inference."""
        with self._lock:
            selection, _, result = self._check(provider, "", options)
            current = self._selections.get(result.service)
            if (
                selection
                and current
                and selection == replace(current, verified=False)
                and self._same_sid()
            ):
                self._cache[result.service] = result.model_copy(deep=True)
                self._selections[result.service] = replace(
                    current, verified=result.state == ConnectionState.READY
                )
            return result

    def disconnect(self, provider: str) -> ConnectionStatus:
        with self._lock:
            if provider not in ENDPOINTS:
                return self._status("chat_ai", "disconnected", "selection_invalid")
            if not self._same_sid():
                return self._status("chat_ai", "disconnected", "sid_mismatch")
            targets = [item for item in self._selections.values() if item.provider == provider]
            for item in targets:
                self._cache[item.service] = self._status(
                    item.service, "disconnected", "disconnected"
                )
            failed = False
            for item in targets:
                try:
                    if not self._same_sid():
                        raise _ProbeFailure("sid_mismatch")
                    self._persist(replace(item, connected=False, verified=False))
                except Exception:
                    failed = True
            if provider in KEY_NAMES:
                try:
                    if not self._same_sid():
                        raise _ProbeFailure("sid_mismatch")
                    self._store.delete(KEY_NAMES[provider])
                except Exception:
                    failed = True
            for item in targets:
                if failed:
                    self._selections[item.service] = replace(item, connected=False, verified=False)
                else:
                    self._selections.pop(item.service, None)
            code = "disconnect_failed" if failed else "disconnected"
            return self._status(targets[0].service if targets else "chat_ai", "disconnected", code)

    def connection_status(self, service: str) -> ConnectionStatus:
        service = ConnectionService(service)
        with self._lock:
            cached = self._cache.get(service)
            if not self._same_sid():
                return self._status(service, "disconnected", "sid_mismatch")
            if (
                cached
                and cached.checked_at
                and timedelta(0) <= self._time() - cached.checked_at < self._ttl
            ):
                return ConnectionStatus.model_validate(cached.model_dump())
            return ConnectionStatus(
                service=service,
                state="unknown",
                checked_at=cached.checked_at if cached else None,
                code="health_unknown",
                message=_MESSAGES["health_unknown"][0],
                next_action=_MESSAGES["health_unknown"][1],
                capabilities=[],
            )

    def health_probe(self, service: str) -> Callable[[], HealthObservation]:
        def measure():
            status = self.connection_status(service)
            return HealthObservation(state=status.state, capabilities=tuple(status.capabilities))

        return measure

    def refresh_selections(self, selections):
        """Trusted owning context reread; persisted verification is never imported."""
        with self._lock:
            fresh = {}
            for item in selections:
                parsed = ModelSelection.parse(
                    item.provider,
                    {
                        "service": item.service,
                        "model": item.model,
                        "endpoint": item.endpoint,
                        "cloud_consent": item.cloud_consent,
                    },
                )
                if item.connected:
                    fresh[item.service] = parsed
            for role in set(self._selections) | set(fresh):
                old = self._selections.get(role)
                if old is None or replace(old, verified=False) != fresh.get(role):
                    self._cache.pop(role, None)
            for role in (ConnectionService.CHAT_AI, ConnectionService.EMBEDDINGS):
                if role not in fresh:
                    self._cache[role] = self._status(role, "disconnected", "disconnected")
            self._selections = fresh

    def preview_local_download(self, model, *, endpoint=ENDPOINTS["ollama"]):
        selected = ModelSelection.parse("ollama", {"model": model, "endpoint": endpoint})
        if not self._same_sid():
            raise ValueError("sid_mismatch")

        async def preview():
            service = OllamaService(selected.endpoint, transport=self._transport)
            try:
                return await service.preview_download(model)
            finally:
                await service.close()

        result = asyncio.run(preview())
        if not self._same_sid():
            raise ValueError("sid_mismatch")
        with self._lock:
            self._download_previews[(model, selected.endpoint)] = (result.size_bytes, monotonic())
        return result

    def verification_fingerprint(self, service: str) -> str | None:
        """O01 adapter input. Valid only while a FRESH READY measurement exists (None otherwise).

        The fingerprint identifies the configuration (provider, endpoint, model, service), not
        the instant of measurement: re-probing the same configuration must not change it, or the
        onboarding coordinator would re-complete ai_configured on every refresh and invalidate
        every later stage. Freshness is enforced by returning None when the cache is stale.
        """
        with self._lock:
            status = self.connection_status(service)
            selected = self._selections.get(ConnectionService(service))
            if selected is None or not selected.verified or status.state != ConnectionState.READY:
                return None
            material = (
                f"{selected.provider}:{selected.endpoint_id}:{selected.model}:{service}:metadata-v2"
            )
            return hashlib.sha256(material.encode()).hexdigest()

    def pull_local_model(
        self,
        model: str,
        *,
        size_bytes: int,
        owner_confirmed: bool,
        cancel: Event,
        pull: Callable[[str, Event], None] | None = None,
        endpoint: str = ENDPOINTS["ollama"],
        on_progress=None,
    ) -> OperationResult:
        """Explicit native download after an owning size preview and confirmation.

        Adapter must honor cancel while streaming and close the response. No
        download selects a model, changes a corpus or verifies AI health.
        """
        if (
            not isinstance(model, str)
            or not MODEL_PATTERN.fullmatch(model)
            or ".." in model
            or contains_secret(model)
        ):
            raise ValueError("pull_model_invalid")
        if owner_confirmed is not True or type(size_bytes) is not int or size_bytes <= 0:
            raise ValueError("pull_consent_required")
        selected = ModelSelection.parse("ollama", {"model": model, "endpoint": endpoint})
        if pull is None:
            with self._lock:
                preview = self._download_previews.get((model, selected.endpoint))
            if not preview or preview[0] != size_bytes or monotonic() - preview[1] > 300:
                raise ValueError("pull_preview_required")

            def pull(model, cancel):
                async def download():
                    service = OllamaService(selected.endpoint, transport=self._transport)

                    async def cancelled():
                        return cancel.is_set() or not self._same_sid()

                    async def progress(event):
                        if on_progress:
                            # No raw provider strings/digest enter Qt status or public DTOs.
                            on_progress(
                                {
                                    key: event.get(key)
                                    for key in ("total_bytes", "completed_bytes", "progress")
                                }
                            )

                    task = asyncio.create_task(
                        service.pull_model(model, on_progress=progress, should_cancel=cancelled)
                    )
                    try:
                        while not task.done():
                            await asyncio.wait({task}, timeout=0.1)
                            if await cancelled():
                                task.cancel()
                                await asyncio.gather(task, return_exceptions=True)
                                return
                        await task
                    finally:
                        await service.close()

                asyncio.run(download())

        operation_id = "pull-" + uuid4().hex
        state, code, message = "cancelled", "pull_cancelled", "Đã hủy tải model."
        if not self._same_sid():
            state, code, message = "failed", "sid_mismatch", _MESSAGES["sid_mismatch"][0]
        elif not cancel.is_set():
            if pull is None:
                state, code, message = (
                    "failed",
                    "pull_unavailable",
                    "Chưa có bộ tải model native được tích hợp.",
                )
            else:
                try:
                    pull(model, cancel)
                    if not cancel.is_set() and self._same_sid():
                        state, code, message = (
                            "completed",
                            "pull_completed",
                            "Bộ tải đã hoàn tất; kiểm tra lại model trước khi dùng.",
                        )
                except Exception:
                    if not cancel.is_set():
                        state, code, message = (
                            "failed",
                            "pull_failed",
                            "Không tải được model; kiểm tra lại Ollama.",
                        )
        return OperationResult(
            operation_id=operation_id,
            state=state,
            progress=100 if state == "completed" else None,
            code=code,
            message=message,
            next_action=None,
        )
