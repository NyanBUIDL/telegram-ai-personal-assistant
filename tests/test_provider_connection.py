"""O02: catch credential replacement, billing, endpoint and capability regressions.

Only synthetic keys, injected HTTP transports and a fake credential store.
"""

from __future__ import annotations

import importlib
import threading

import httpx
import pytest


class Store:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


@pytest.fixture
def api():
    try:
        return importlib.import_module("tg_assistant.services.provider_connections")
    except ModuleNotFoundError as exc:
        if exc.name == "tg_assistant.services.provider_connections":
            pytest.fail("O02 native provider service is not implemented")
        raise


def build(api, handler, *, store=None, save=None, sid=None):
    store = store or Store()
    writes = []
    service = api.CredentialConnectionService(
        store=store,
        persist_selection=save or writes.append,
        transport=httpx.MockTransport(handler),
        current_sid=sid or (lambda: "synthetic-owner-sid"),
    )
    return service, store, writes


def options(**extra):
    return {"service": "chat_ai", "model": "synthetic/chat", "cloud_consent": True, **extra}


def router(request):
    if request.url.path.endswith("/key"):
        return httpx.Response(200, json={"data": {"limit_remaining": 20}})
    if request.url.path.endswith("/embeddings/models"):
        return httpx.Response(200, json={"data": [{"id": "synthetic/embed"}]})
    return httpx.Response(
        200,
        json={
            "data": [
                {
                    "id": "synthetic/chat",
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                }
            ]
        },
    )


def test_invalid_key_actionable_preserves_existing(api):
    store = Store()
    store.values["openrouter_api_key"] = "synthetic-existing"
    service, _, writes = build(
        api,
        lambda _: httpx.Response(401, json={"error": "synthetic-replacement must never echo"}),
        store=store,
    )
    result = service.validate_and_save("openrouter", "synthetic-replacement", options())
    assert result.code == "authentication_failed"
    assert result.state == "disconnected" and result.next_action
    assert store.values["openrouter_api_key"] == "synthetic-existing"
    assert not writes
    assert "synthetic-replacement" not in result.model_dump_json()


def test_validation_no_bill_by_default(api):
    requests = []

    def handle(request):
        requests.append((request.method, request.url.path, request.content))
        return router(request)

    service, store, writes = build(api, handle)
    result = service.validate_and_save("openrouter", "synthetic-key", options())
    assert result.state == "ready"
    assert requests == [("GET", "/api/v1/key", b""), ("GET", "/api/v1/models", b"")]
    assert store.values == {"openrouter_api_key": "synthetic-key"}
    assert writes[0].model == "synthetic/chat"
    assert "chat_metadata" in result.capabilities
    assert "inference_verified" not in result.capabilities


def test_save_only_valid_or_explicit_unverified(api):
    def unavailable(request):
        raise httpx.ConnectError("synthetic-key", request=request)

    service, store, writes = build(api, unavailable)
    result = service.validate_and_save("openrouter", "synthetic-key", options())
    assert result.state == "disconnected" and not store.values and not writes
    result = service.validate_and_save(
        "openrouter", "synthetic-key", options(allow_unverified=True)
    )
    assert result.state == "unknown" and result.code == "saved_unverified"
    assert store.values and writes[0].verified is False
    assert service.health_probe("chat_ai")().state == "unknown"


def test_model_capability_not_guessed(api):
    service, store, _ = build(
        api,
        lambda _: httpx.Response(
            200, json={"data": [{"id": "gpt-synthetic-chat"}, {"id": "text-embedding-synthetic"}]}
        ),
    )
    result = service.validate_and_save(
        "openai", "synthetic-key", options(model="gpt-synthetic-chat")
    )
    assert result.state == "degraded" and result.code == "capability_unknown"
    assert not store.values
    result = service.validate_and_save(
        "openai", "synthetic-key", options(model="gpt-synthetic-chat", allow_unverified=True)
    )
    assert result.state == "unknown"


def test_chat_embedding_health_is_separate(api):
    service, _, _ = build(api, router)
    service.validate_and_save("openrouter", "synthetic-key", options())
    assert service.health_probe("chat_ai")().state == "ready"
    assert service.health_probe("embeddings")().state == "unknown"
    result = service.validate_and_save(
        "openrouter", "", options(service="embeddings", model="synthetic/embed")
    )
    assert result.state == "ready" and "embedding_metadata" in result.capabilities
    assert service.health_probe("embeddings")().state == "ready"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://evil.example/v1",
        "https://api.openai.com/v1?secret=x",
        "http://192.168.1.10:11434/v1",
        "http://127.0.0.1:11434/v1#secret",
        "http://user:pass@127.0.0.1:11434/v1",
    ],
)
def test_endpoint_boundaries_never_send_secret(api, endpoint):
    requests = []
    service, store, _ = build(api, lambda request: requests.append(request))
    result = service.validate_and_save(
        "ollama" if endpoint.startswith("http://") else "openai",
        "synthetic-key",
        options(endpoint=endpoint),
    )
    assert result.code == "selection_invalid" and not requests and not store.values


def test_cloud_consent_before_network(api):
    requests = []
    service, store, _ = build(api, lambda request: requests.append(request))
    result = service.validate_and_save("openrouter", "synthetic-key", options(cloud_consent=False))
    assert result.code == "cloud_consent_required" and not requests and not store.values


def test_secret_never_http_log_config(api, caplog):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(503, json={"error": "synthetic-sensitive-key"})

    service, _, writes = build(api, handle)
    result = service.validate_and_save("openrouter", "synthetic-sensitive-key", options())
    assert result.code == "provider_unavailable"
    assert all("synthetic-sensitive-key" not in str(r.url) and not r.content for r in requests)
    assert not writes
    assert "synthetic-sensitive-key" not in caplog.text + result.model_dump_json()


def test_local_metadata_no_embedding_or_download(api):
    requests = []

    def handle(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "synthetic:latest", "size": 100}]})
        return httpx.Response(200, json={"capabilities": ["completion"]})

    service, store, _ = build(api, handle)
    result = service.validate_and_save(
        "ollama", "", options(model="synthetic:latest", cloud_consent=False)
    )
    assert result.state == "ready" and not store.values
    assert requests == [("GET", "/api/tags"), ("POST", "/api/show")]
    result = service.test_connection(
        "ollama", options(service="embeddings", model="synthetic:latest")
    )
    assert result.state == "degraded" and result.code == "capability_unsupported"


def test_disconnect_removes_credential_and_both_health_states(api):
    service, store, writes = build(api, router)
    service.validate_and_save("openrouter", "synthetic-key", options())
    result = service.disconnect("openrouter")
    assert result.state == "disconnected" and not store.values
    assert service.health_probe("chat_ai")().state == "disconnected"
    assert writes[-1].provider == "openrouter" and writes[-1].connected is False


def test_sid_change_denies_secret_write_and_probe(api):
    identity = ["first"]
    requests = []
    service, store, _ = build(
        api, lambda request: requests.append(request), sid=lambda: identity[0]
    )
    identity[0] = "second"
    result = service.validate_and_save("openrouter", "synthetic-key", options())
    assert result.code == "sid_mismatch" and not requests and not store.values


def test_settings_failure_restores_existing_key(api):
    store = Store()
    store.values["openrouter_api_key"] = "synthetic-existing"

    def fail(selection):
        raise RuntimeError("synthetic-replacement")

    service, _, _ = build(api, router, store=store, save=fail)
    result = service.validate_and_save("openrouter", "synthetic-replacement", options())
    assert (
        result.code == "save_failed" and store.values["openrouter_api_key"] == "synthetic-existing"
    )
    assert service.health_probe("chat_ai")().state == "unknown"


def test_local_pull_cancel_requires_owner_model_size(api):
    service, _, _ = build(api, router)
    called = []
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ValueError, match="pull_consent_required"):
        service.pull_local_model(
            "synthetic:latest",
            size_bytes=100,
            owner_confirmed=False,
            cancel=cancel,
            pull=lambda *args: called.append(args),
        )
    result = service.pull_local_model(
        "synthetic:latest",
        size_bytes=100,
        owner_confirmed=True,
        cancel=cancel,
        pull=lambda *args: called.append(args),
    )
    assert result.state == "cancelled" and not called


def test_secret_write_partial_failure_rolls_back(api):
    class PartialStore(Store):
        def set(self, name, value):
            super().set(name, value)
            if value == "synthetic-replacement":
                raise RuntimeError("private provider error")

    store = PartialStore()
    store.values["openrouter_api_key"] = "synthetic-existing"
    service, _, _ = build(api, router, store=store)
    result = service.validate_and_save("openrouter", "synthetic-replacement", options())
    assert result.code == "save_failed"
    assert store.values["openrouter_api_key"] == "synthetic-existing"


def test_key_replacement_revokes_other_model_measurements(api):
    service, _, _ = build(api, router)
    service.validate_and_save("openrouter", "synthetic-first", options())
    service.validate_and_save(
        "openrouter", "", options(service="embeddings", model="synthetic/embed")
    )
    assert service.verification_fingerprint("embeddings")
    service.validate_and_save("openrouter", "synthetic-second", options())
    assert service.health_probe("embeddings")().state == "unknown"
    assert service.verification_fingerprint("embeddings") is None


def test_expired_evidence_never_ready(api):
    from datetime import UTC, datetime, timedelta

    current = [datetime(2026, 10, 5, tzinfo=UTC)]
    service = api.CredentialConnectionService(
        store=Store(),
        persist_selection=lambda _: None,
        transport=httpx.MockTransport(router),
        current_sid=lambda: "synthetic",
        now=lambda: current[0],
    )
    service.validate_and_save("openrouter", "synthetic-key", options())
    assert service.verification_fingerprint("chat_ai")
    current[0] += timedelta(seconds=61)
    assert service.health_probe("chat_ai")().state == "unknown"
    assert service.verification_fingerprint("chat_ai") is None


def test_sid_changes_during_network_never_save(api):
    identity = ["original"]

    def handle(request):
        identity[0] = "changed"
        return router(request)

    service, store, writes = build(api, handle, sid=lambda: identity[0])
    result = service.validate_and_save("openrouter", "synthetic-key", options())
    assert result.code == "sid_mismatch" and not writes and not store.values


def test_native_cancel_before_publication_prevents_save(api):
    cancelled = threading.Event()

    def handle(request):
        cancelled.set()
        return router(request)

    service, store, writes = build(api, handle)
    result = service.validate_and_save("openrouter", "synthetic-key", options(), cancel=cancelled)
    assert result.code == "check_cancelled" and not store.values and not writes


def test_persisted_settings_are_nonsecret_and_independent(api, tmp_path):
    import json

    path = tmp_path / "provider-settings.json"
    initial = {
        "embedding_provider": "ollama",
        "embedding_model": "old:latest",
        "store_id": "old-store",
    }
    path.write_text(json.dumps(initial))

    def commit(selection):
        saved = json.loads(path.read_text())
        saved.update(
            chat_provider=selection.provider,
            chat_model=selection.model,
            chat_endpoint_id=selection.endpoint_id,
            chat_verified=selection.verified,
        )
        path.write_text(json.dumps(saved))

    service, _, _ = build(api, router, save=commit)
    assert service.validate_and_save("openrouter", "synthetic-key", options()).state == "ready"
    saved = path.read_text()
    assert "synthetic-key" not in saved and "Bearer" not in saved
    assert json.loads(saved)["store_id"] == "old-store"


def test_dialog_edit_blank_connect_test_disconnect_escape(api, qt_application):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QWidget

    from tg_assistant.desktop.dialogs.provider import ProviderDialog

    app = qt_application
    service, store, writes = build(api, router)
    store.values["openrouter_api_key"] = "synthetic-existing"
    parent = QWidget()
    parent.show()
    dialog = ProviderDialog(service, provider="openrouter", model="synthetic/chat", parent=parent)
    dialog.show()
    app.processEvents()
    assert dialog.secret_input.text() == ""
    assert dialog.secret_input.echoMode() == dialog.secret_input.EchoMode.Password
    assert dialog.connect_button.minimumHeight() >= 44
    dialog.secret_input.setText("synthetic-key")
    dialog.cloud_consent.setChecked(True)
    QTest.mouseClick(dialog.connect_button, Qt.MouseButton.LeftButton)
    assert dialog.secret_input.text() == ""  # Cleared immediately, before probe completion.
    for _ in range(100):
        QTest.qWait(10)
        if not dialog.busy:
            break
    assert store.values == {"openrouter_api_key": "synthetic-key"}
    assert writes and "metadata_verified" in dialog.status_label.text()
    before = len(writes)
    QTest.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    for _ in range(100):
        QTest.qWait(10)
        if not dialog.busy:
            break
    assert len(writes) == before
    QTest.mouseClick(dialog.disconnect_button, Qt.MouseButton.LeftButton)
    for _ in range(100):
        QTest.qWait(10)
        if not dialog.busy:
            break
    assert not store.values
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    parent.close()


def test_dialog_reject_cancels_pending_save(api, qt_application):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from tg_assistant.desktop.dialogs.provider import ProviderDialog

    app = qt_application
    entered, release = threading.Event(), threading.Event()

    def handle(request):
        entered.set()
        release.wait(2)
        return router(request)

    service, store, writes = build(api, handle)
    dialog = ProviderDialog(service, provider="openrouter", model="synthetic/chat")
    dialog.show()
    dialog.secret_input.setText("synthetic-key")
    dialog.cloud_consent.setChecked(True)
    QTest.mouseClick(dialog.connect_button, Qt.MouseButton.LeftButton)
    assert entered.wait(1)
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    release.set()
    for _ in range(100):
        QTest.qWait(10)
        if not dialog.busy:
            break
    app.processEvents()
    assert not store.values and not writes


def test_dialog_art_targets_keyboard_focus_and_escape(api, qt_application, tmp_path):
    import os
    from pathlib import Path

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

    from tg_assistant.desktop.dialogs.provider import ProviderDialog
    from tg_assistant.desktop.theme import ArtPanel

    app = qt_application
    service, _, _ = build(api, router)
    parent = QWidget()
    opener = QPushButton("Mở kết nối")
    QVBoxLayout(parent).addWidget(opener)
    parent.show()
    parent.activateWindow()
    opener.setFocus()
    dialog = ProviderDialog(service, provider="openrouter", model="synthetic/chat", parent=parent)
    dialog.open()
    dialog.activateWindow()
    dialog.secret_input.setFocus()
    QTest.qWait(50)
    assert app.font().family().startswith("Darley")
    assert dialog.secret_input.height() >= 44
    assert dialog.findChild(QScrollArea).horizontalScrollBar().maximum() == 0
    for button in dialog.findChildren(QPushButton):
        assert button.height() >= 44 and button.width() >= 44
    title = next(
        label for label in dialog.findChildren(QLabel) if label.property("artRole") == "title"
    )
    assert title.font().family() == "LNTH-Peter Obscure"
    pixels = dialog.findChild(ArtPanel).grab().toImage()
    assert pixels.pixelColor(0, 0).name() == "#090909"
    assert dialog.palette().window().color().name() == "#f3efdf"
    output = Path(os.environ.get("ART_EVIDENCE_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    assert dialog.grab().save(
        str(output / f"provider-scale-{os.environ.get('QT_SCALE_FACTOR', '1')}.png")
    )
    for _ in range(14):
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
        assert dialog.isAncestorOf(app.focusWidget())
    QTest.keyClick(app.focusWidget(), Qt.Key.Key_Escape)
    QTest.qWait(50)
    assert not dialog.isVisible()
    parent.activateWindow()
    QTest.qWait(50)
    assert app.focusWidget() is opener
    parent.close()


def test_ollama_cloud_model_never_claims_local_ready(api):
    def handle(request):
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "synthetic:latest",
                            "size": 100,
                            "remote_host": "https://remote.invalid",
                            "remote_model": "synthetic",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"capabilities": ["completion"]})

    service, store, writes = build(api, handle)
    result = service.validate_and_save("ollama", "", options(model="synthetic:latest"))
    assert result.code == "local_model_required" and result.state == "degraded"
    assert not store.values and not writes


def test_disconnect_failure_revokes_ready_and_retries_settings(api):
    failure = [False]
    writes = []

    def commit(selection):
        if failure[0]:
            raise RuntimeError("synthetic failure")
        writes.append(selection)

    service, store, _ = build(api, router, save=commit)
    service.validate_and_save("openrouter", "synthetic-key", options())
    failure[0] = True
    assert service.disconnect("openrouter").code == "disconnect_failed"
    assert service.health_probe("chat_ai")().state == "disconnected"
    assert not store.values  # Settings failure must not skip deleting the key.
    failure[0] = False
    assert service.disconnect("openrouter").code == "disconnected"
    assert writes[-1].connected is False


def test_loaded_selection_has_unknown_health_and_real_disconnect(api):
    selection = api.ModelSelection.parse("openrouter", options())
    writes = []
    store = Store()
    store.values["openrouter_api_key"] = "synthetic-key"
    service = api.CredentialConnectionService(
        store=store,
        persist_selection=writes.append,
        saved_selections=(selection,),
        current_sid=lambda: "synthetic",
    )
    assert service.health_probe("chat_ai")().state == "unknown"
    assert service.verification_fingerprint("chat_ai") is None
    service.disconnect("openrouter")
    assert not store.values and writes[-1].connected is False


def test_owned_loopback_server_metadata_e2e(api):
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.command, self.path, self.headers.get("Authorization")))
            payload = {"models": [{"name": "synthetic:latest", "size": 100}]}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def do_POST(self):
            requests.append((self.command, self.path, self.headers.get("Authorization")))
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert payload == {"model": "synthetic:latest"}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"capabilities":["completion"]}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        writes = []
        service = api.CredentialConnectionService(
            store=Store(),
            persist_selection=writes.append,
            current_sid=lambda: "synthetic",
            timeout_seconds=1,
        )
        result = service.validate_and_save(
            "ollama",
            "",
            options(model="synthetic:latest", endpoint=f"http://127.0.0.1:{server.server_port}/v1"),
        )
        assert result.state == "ready" and writes[0].verified is True
        assert requests == [("GET", "/api/tags", None), ("POST", "/api/show", None)]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (402, "quota_limited"),
        (429, "quota_limited"),
        (302, "provider_unavailable"),
        (500, "provider_unavailable"),
    ],
)
def test_auth_and_quota_errors_never_ready(api, status, code):
    requests = []

    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(
            status,
            headers={"Location": "https://evil.invalid/secret"},
            json={"error": "synthetic-key"},
        )

    service, store, writes = build(api, handle)
    result = service.validate_and_save("openrouter", "synthetic-key", options())
    assert result.code == code and result.state != "ready"
    assert not store.values and not writes
    assert len(requests) == 1 and "evil.invalid" not in requests[0]


def test_invalid_auth_never_saved_even_explicit_unverified(api):
    service, store, writes = build(
        api, lambda _: httpx.Response(401, json={"error": "synthetic-key"})
    )
    result = service.validate_and_save(
        "openrouter", "synthetic-key", options(allow_unverified=True)
    )
    assert result.code == "authentication_failed" and not store.values and not writes


def test_oversized_metadata_rejected(api):
    service, store, writes = build(
        api, lambda _: httpx.Response(200, content=b" " * (4 * 1024 * 1024 + 1))
    )
    result = service.validate_and_save("openrouter", "synthetic-key", options())
    assert result.code == "provider_unavailable" and not store.values and not writes


def test_pull_midstream_cancel_and_missing_size(api):
    service, _, _ = build(api, router)
    cancel = threading.Event()
    called = []

    def pull(model, cancelled):
        called.append(model)
        cancelled.set()

    with pytest.raises(ValueError, match="pull_consent_required"):
        service.pull_local_model(
            "synthetic:latest", size_bytes=0, owner_confirmed=True, cancel=cancel, pull=pull
        )
    result = service.pull_local_model(
        "synthetic:latest", size_bytes=100, owner_confirmed=True, cancel=cancel, pull=pull
    )
    assert result.state == "cancelled" and called == ["synthetic:latest"]
    assert service.health_probe("chat_ai")().state == "unknown"


def test_rollback_failure_invalidates_all_shared_key_evidence(api):
    class RollbackStore(Store):
        fail_restore = False

        def set(self, name, value):
            if self.fail_restore and value == "synthetic-original":
                raise RuntimeError("private storage error")
            super().set(name, value)

    store = RollbackStore()
    failure = [False]

    def commit(selection):
        if failure[0]:
            raise RuntimeError("private commit error")

    service, _, _ = build(api, router, store=store, save=commit)
    service.validate_and_save("openrouter", "synthetic-original", options())
    service.validate_and_save(
        "openrouter", "", options(service="embeddings", model="synthetic/embed")
    )
    store.fail_restore = failure[0] = True
    result = service.validate_and_save("openrouter", "synthetic-replacement", options())
    assert result.code == "rollback_failed"
    assert service.health_probe("embeddings")().state == "unknown"
    assert service.verification_fingerprint("embeddings") is None


@pytest.mark.parametrize(
    "operation", ["save_blank", "save_same_key", "save_proposed_model", "test"]
)
def test_saved_key_auth_failure_revokes_both_role_fingerprints(api, operation):
    rejected = [False]

    def handle(request):
        if rejected[0]:
            return httpx.Response(401, json={"error": "private synthetic error"})
        return router(request)

    service, store, writes = build(api, handle)
    service.validate_and_save("openrouter", "synthetic-original", options())
    service.validate_and_save(
        "openrouter", "", options(service="embeddings", model="synthetic/embed")
    )
    assert all(service.verification_fingerprint(role) for role in ("chat_ai", "embeddings"))
    rejected[0] = True
    if operation == "test":
        result = service.test_connection("openrouter", options())
    else:
        secret = "synthetic-original" if operation == "save_same_key" else ""
        selected = (
            options(model="synthetic/proposed") if operation == "save_proposed_model" else options()
        )
        result = service.validate_and_save("openrouter", secret, selected)
    assert result.code == "authentication_failed"
    for role in ("chat_ai", "embeddings"):
        assert service.connection_status(role).state != "ready"
        assert service.health_probe(role)().state != "ready"
        assert service.verification_fingerprint(role) is None
    assert store.values == {"openrouter_api_key": "synthetic-original"}
    assert len(writes) == 2


def test_rejected_distinct_replacement_preserves_old_measurements(api):
    from datetime import UTC, datetime, timedelta

    def handle(request):
        if request.headers["Authorization"] == "Bearer synthetic-replacement":
            return httpx.Response(401)
        return router(request)

    clock = [datetime(2026, 10, 5, tzinfo=UTC)]
    store, writes = Store(), []
    service = api.CredentialConnectionService(
        store=store,
        persist_selection=writes.append,
        transport=httpx.MockTransport(handle),
        current_sid=lambda: "synthetic-owner-sid",
        now=lambda: clock[0],
    )
    service.validate_and_save("openrouter", "synthetic-original", options())
    service.validate_and_save(
        "openrouter", "", options(service="embeddings", model="synthetic/embed")
    )
    before = {role: service.connection_status(role) for role in ("chat_ai", "embeddings")}
    fingerprints = {role: service.verification_fingerprint(role) for role in before}
    clock[0] += timedelta(seconds=59)
    result = service.validate_and_save("openrouter", "synthetic-replacement", options())
    assert result.code == "authentication_failed"
    assert {role: service.connection_status(role) for role in before} == before
    assert {role: service.verification_fingerprint(role) for role in before} == fingerprints
    assert store.values == {"openrouter_api_key": "synthetic-original"}
    assert len(writes) == 2
    clock[0] += timedelta(seconds=2)
    for role in before:
        assert service.connection_status(role).state == "unknown"
        assert service.verification_fingerprint(role) is None


@pytest.mark.parametrize("operation", ["save", "test"])
def test_current_model_failure_replaces_ready_without_affecting_other_role(api, operation):
    missing = [False]

    def handle(request):
        if missing[0] and request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": []})
        return router(request)

    service, _, writes = build(api, handle)
    service.validate_and_save("openrouter", "synthetic-original", options())
    service.validate_and_save(
        "openrouter", "", options(service="embeddings", model="synthetic/embed")
    )
    embedding_fingerprint = service.verification_fingerprint("embeddings")
    missing[0] = True
    result = (
        service.validate_and_save("openrouter", "", options())
        if operation == "save"
        else service.test_connection("openrouter", options())
    )
    assert result.code == "model_unavailable"
    assert service.connection_status("chat_ai").code == "model_unavailable"
    assert service.verification_fingerprint("chat_ai") is None
    assert service.verification_fingerprint("embeddings") == embedding_fingerprint
    assert len(writes) == 2


def test_failed_proposed_model_keeps_configured_model_measurement(api):
    service, _, writes = build(api, router)
    service.validate_and_save("openrouter", "synthetic-original", options())
    before = service.connection_status("chat_ai")
    fingerprint = service.verification_fingerprint("chat_ai")
    result = service.validate_and_save("openrouter", "", options(model="synthetic/missing"))
    assert result.code == "model_unavailable"
    assert service.connection_status("chat_ai") == before
    assert service.verification_fingerprint("chat_ai") == fingerprint
    assert len(writes) == 1


@pytest.mark.parametrize("operation", ["save", "test"])
def test_returned_capabilities_cannot_change_measured_evidence(api, operation):
    service, _, _ = build(api, router)
    result = service.validate_and_save("openrouter", "synthetic-key", options())
    if operation == "test":
        result = service.test_connection("openrouter", options())
    fingerprint = service.verification_fingerprint("chat_ai")
    measured_at = result.checked_at
    result.capabilities.clear()
    result.capabilities.append("inference_verified")
    published = service.connection_status("chat_ai")
    assert published.capabilities == ["chat_metadata", "model_available"]
    assert service.health_probe("chat_ai")().capabilities == ("chat_metadata", "model_available")
    assert published.checked_at == measured_at
    assert service.verification_fingerprint("chat_ai") == fingerprint
    published.capabilities.clear()
    assert service.connection_status("chat_ai").capabilities == ["chat_metadata", "model_available"]


@pytest.mark.parametrize("operation", ["save", "test", "save_candidate"])
def test_missing_saved_key_revokes_shared_evidence_without_writes(api, operation):
    requests = []

    def handle(request):
        requests.append(request.url.path)
        if request.headers["Authorization"] == "Bearer synthetic-replacement":
            return httpx.Response(401)
        return router(request)

    service, store, writes = build(api, handle)
    service.validate_and_save("openrouter", "synthetic-original", options())
    service.validate_and_save(
        "openrouter", "", options(service="embeddings", model="synthetic/embed")
    )
    for role in ("chat_ai", "embeddings"):
        assert service.connection_status(role).state == "ready"
        assert service.verification_fingerprint(role)
    store.delete("openrouter_api_key")
    requests.clear()
    if operation == "test":
        result = service.test_connection("openrouter", options())
    else:
        secret = "synthetic-replacement" if operation == "save_candidate" else ""
        result = service.validate_and_save("openrouter", secret, options())
    assert result.code == (
        "authentication_failed" if operation == "save_candidate" else "credential_required"
    )
    for role in ("chat_ai", "embeddings"):
        assert service.connection_status(role).code == "credential_required"
        assert service.health_probe(role)().state == "disconnected"
        assert service.verification_fingerprint(role) is None
    assert store.values == {}
    assert len(writes) == 2
    assert requests == (["/api/v1/key"] if operation == "save_candidate" else [])


def test_missing_saved_key_read_rechecks_sid_before_revoking_evidence(api):
    identity = ["synthetic-owner-sid"]

    class SidChangingStore(Store):
        change_sid = False

        def get(self, name):
            if self.change_sid:
                identity[0] = "different-synthetic-sid"
            return super().get(name)

    store = SidChangingStore()
    service, _, writes = build(api, router, store=store, sid=lambda: identity[0])
    service.validate_and_save("openrouter", "synthetic-original", options())
    before = service.connection_status("chat_ai")
    fingerprint = service.verification_fingerprint("chat_ai")
    store.delete("openrouter_api_key")
    store.change_sid = True
    result = service.validate_and_save("openrouter", "", options())
    assert result.code == "sid_mismatch"
    identity[0] = "synthetic-owner-sid"
    assert service.connection_status("chat_ai") == before
    assert service.verification_fingerprint("chat_ai") == fingerprint
    assert len(writes) == 1 and store.values == {}
