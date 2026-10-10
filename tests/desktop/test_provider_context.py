"""Native provider integration uses migrated selected storage and synthetic keys."""

from __future__ import annotations

import asyncio
import importlib
import os
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from tg_assistant.config import Settings, config_path, save_settings
from tg_assistant.contracts import PublicProfile
from tg_assistant.db.models import AppSetting
from tg_assistant.services.maintenance import MaintenanceBusy
from tg_assistant.services.storage import StorageService


class Store:
    def __init__(self, password=None):
        self.values = {} if password is None else {"database_password": password}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def metadata(request):
    if request.url.path.endswith("/key"):
        return httpx.Response(200, json={"data": {"limit_remaining": 20}})
    if request.url.path.endswith("/embeddings/models"):
        return httpx.Response(
            200, json={"data": [{"id": "synthetic/embed"}, {"id": "synthetic/new"}]}
        )
    return httpx.Response(
        200,
        json={
            "data": [
                {
                    "id": model,
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                }
                for model in ("synthetic/chat", "synthetic/newchat")
            ]
        },
    )


@pytest.fixture(params=["sqlite"])
def selected(tmp_path, request):
    values = dict(
        _env_file=None,
        data_dir=tmp_path / "profile",
        profile_id="provider_context",
        ai_provider="openrouter",
        embedding_provider="openrouter",
        cloud_consent=True,
        openrouter_primary_model="synthetic/chat",
        openrouter_embedding_model="synthetic/embed",
        embedding_version="existing-v1",
        cloud_embedding_dimension=1536,
        media_retention_hours=37,
    )
    store = Store()
    settings = Settings(**values)
    storage = StorageService(settings, store)
    database = storage.open(
        PublicProfile(
            profile_id=settings.profile_id,
            owner_id=None,
            storage_backend=request.param,
            setup_stage="welcome",
            version=1,
        )
    )
    asyncio.run(database.close())
    storage.migrate()
    save_settings(settings)
    try:
        yield settings, storage, store
    finally:
        storage.fence.close()


def open_context(selected, **extra):
    module = importlib.import_module("tg_assistant.desktop.setup_context")
    settings, _, store = selected
    return module.open_setup_context(
        settings,
        secret_store_factory=lambda: store,
        provider_options={"transport": httpx.MockTransport(metadata), **extra},
    )


def choose(service="chat_ai", model="synthetic/chat"):
    return {"service": service, "model": model, "cloud_consent": True}


def test_native_provider_save_is_fenced_and_preserves_settings(selected):
    settings, storage, store = selected
    with open_context(selected) as context:
        before = settings.embedding_profile
        result = context.provider.service.validate_and_save(
            "openrouter", "synthetic-key", choose(model="synthetic/newchat")
        )
        assert result.state == "ready"
        loaded = Settings(
            _env_file=None, data_dir=settings.data_dir, profile_id=settings.profile_id
        )
        assert loaded.media_retention_hours == 37 and loaded.embedding_profile == before
        assert loaded.openrouter_primary_model == "synthetic/newchat"
        assert "synthetic-key" not in config_path(settings.data_dir).read_text()
        with context.engine.connect() as connection:
            rows = connection.execute(select(AppSetting.value)).scalars().all()
        assert "synthetic-key" not in str(rows)
        assert "synthetic/newchat" in str(rows)
        lease = storage.fence.acquire("owned-maintenance", lease_seconds=30)
        try:
            with pytest.raises(MaintenanceBusy):
                context.provider.service.validate_and_save(
                    "openrouter", "synthetic-replacement", choose()
                )
            assert store.values["openrouter_api_key"] == "synthetic-key"
        finally:
            storage.fence.release(lease)
    with open_context(selected) as reopened:
        assert reopened.provider.dialog_options("chat_ai")["model"] == "synthetic/newchat"
        assert reopened.provider.service.connection_status("chat_ai").state == "unknown"
        assert reopened.provider.service.verification_fingerprint("chat_ai") is None


def test_embedding_change_is_pending_without_altering_active_corpus(selected):
    settings, _, _ = selected
    with open_context(selected) as context:
        before = settings.embedding_profile
        result = context.provider.service.validate_and_save(
            "openrouter", "synthetic-key", choose("embeddings", "synthetic/new")
        )
        assert result.state == "ready"
        loaded = Settings(
            _env_file=None, data_dir=settings.data_dir, profile_id=settings.profile_id
        )
        assert loaded.embedding_profile == before
        assert context.provider.dialog_options("embeddings")["model"] == "synthetic/new"
        assert context.provider.configuration_verification() is None
        assert "chỉ mục" in context.provider.activation_notice.lower()


def test_provider_health_keeps_original_ttl_and_invalidates_setup_chain(selected):
    clock = [datetime(2026, 10, 5, tzinfo=UTC)]
    requests = []

    def handle(request):
        requests.append(request.url.path)
        return metadata(request)

    with open_context(
        selected, now=lambda: clock[0], transport=httpx.MockTransport(handle)
    ) as context:
        context.begin()
        context.provider.service.validate_and_save("openrouter", "synthetic-key", choose())
        context.provider.service.validate_and_save(
            "openrouter", "", choose("embeddings", "synthetic/embed")
        )
        coordinator = context.coordinator
        proof = coordinator.verify_stage("ai_configured")
        coordinator.complete_stage("ai_configured", proof)
        first = {row.service.value: row for row in coordinator.status().connections}["chat_ai"]
        clock[0] += timedelta(seconds=59)
        coordinator.resume()
        refreshed = {row.service.value: row for row in coordinator.status().connections}["chat_ai"]
        assert refreshed.checked_at == first.checked_at
        assert refreshed.capabilities == ["chat_metadata", "model_available"]
        refreshed.capabilities.append("inference_verified")
        assert (
            "inference_verified"
            not in {row.service.value: row for row in coordinator.status().connections}[
                "chat_ai"
            ].capabilities
        )
        request_count = len(requests)
        clock[0] += timedelta(seconds=2)
        stale = coordinator.status()
        assert {row.service.value: row.state.value for row in stale.connections}[
            "chat_ai"
        ] == "unknown"
        assert "ai_configured" not in stale.stage_evidence_ids
        assert "first_answer" in stale.disabled_capabilities
        assert len(requests) == request_count


def test_missing_key_withdraws_composed_setup_health(selected):
    with open_context(selected) as context:
        context.provider.service.validate_and_save("openrouter", "synthetic-key", choose())
        context.provider.service.validate_and_save(
            "openrouter", "", choose("embeddings", "synthetic/embed")
        )
        selected[2].delete("openrouter_api_key")
        context.provider.service.test_connection("openrouter", choose())
        measured = {
            row.service.value: row.state.value for row in context.coordinator.status().connections
        }
        assert measured["chat_ai"] == measured["embeddings"] == "disconnected"


def test_context_close_drains_provider_before_engine_disposal(selected):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    def slow(request):
        entered.set()
        assert release.wait(5)
        return metadata(request)

    context = open_context(selected, transport=httpx.MockTransport(slow))
    result = []
    job = threading.Thread(
        target=lambda: result.append(
            context.provider.service.validate_and_save("openrouter", "synthetic-key", choose())
        )
    )
    job.start()
    assert entered.wait(2)
    closer = threading.Thread(target=lambda: (context.close(), closed.set()))
    closer.start()
    try:
        assert not closed.wait(0.1)
    finally:
        release.set()
        job.join(5)
        closer.join(5)
    assert closed.is_set()
    assert result[0].code == "check_cancelled"
    assert "openrouter_api_key" not in selected[2].values


def test_settings_save_failure_rolls_back_candidate_key(selected, monkeypatch):
    provider = importlib.import_module("tg_assistant.desktop.provider_context")
    settings, _, store = selected
    original = config_path(settings.data_dir).read_bytes()
    with open_context(selected) as context:

        def fail(_settings):
            raise OSError("PRIVATE_SYNTHETIC_SAVE_ERROR")

        monkeypatch.setattr(provider, "save_settings", fail)
        result = context.provider.service.validate_and_save("openrouter", "synthetic-key", choose())
        assert result.code == "save_failed"
        assert "openrouter_api_key" not in store.values
        assert config_path(settings.data_dir).read_bytes() == original
        assert context.provider.service.verification_fingerprint("chat_ai") is None


def test_actual_setup_controller_registers_reachable_provider_handler(selected):
    from tg_assistant.desktop.setup_ui import NativeSetupController

    controller = NativeSetupController.for_settings(selected[0])
    try:
        assert callable(controller.dialog_handlers.get("open_connection_dialog"))
    finally:
        controller.close().result(timeout=5)


def test_changed_persisted_selection_withdraws_setup_configuration(selected):
    with open_context(selected) as context:
        context.begin()
        context.provider.service.validate_and_save("openrouter", "synthetic-key", choose())
        context.provider.service.validate_and_save(
            "openrouter", "", choose("embeddings", "synthetic/embed")
        )
        coordinator = context.coordinator
        coordinator.complete_stage("ai_configured", coordinator.verify_stage("ai_configured"))
        current = Settings(
            _env_file=None, data_dir=selected[0].data_dir, profile_id=selected[0].profile_id
        )
        save_settings(current.model_copy(update={"openrouter_primary_model": "synthetic/newchat"}))
        assert "ai_configured" not in coordinator.status().stage_evidence_ids


def wait_qt(predicate, timeout=5000):
    from PySide6.QtCore import QEventLoop, QTimer

    loop, poll, limit = QEventLoop(), QTimer(), QTimer()
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    poll.start(10)
    limit.setSingleShot(True)
    limit.timeout.connect(loop.quit)
    limit.start(timeout)
    if not predicate():
        loop.exec()
    poll.stop()
    limit.stop()
    assert predicate(), "Bounded native operation did not finish"


def test_actual_setup_opens_write_only_provider_and_restores_focus(selected, qt_application):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest

    from tg_assistant.desktop.dialogs.provider import ProviderDialog
    from tg_assistant.desktop.setup_ui import NativeSetupController, SetupDialog

    main_thread = threading.get_ident()
    provider_threads, checks, errors = [], [], []

    def handle(request):
        provider_threads.append(threading.get_ident())
        return metadata(request)

    control = NativeSetupController.for_settings(
        selected[0],
        context_options={
            "secret_store_factory": lambda: selected[2],
            "provider_options": {"transport": httpx.MockTransport(handle)},
        },
    )
    setup = SetupDialog(control)
    try:
        setup.show()
        wait_qt(lambda: setup.pending is None)
        opener = setup.connection_buttons["open_connection_dialog"]
        assert opener.isEnabled()
        assert not setup.connection_buttons["open_telegram_login"].isEnabled()

        def interact():
            dialog = qt_application.activeModalWidget()
            try:
                assert isinstance(dialog, ProviderDialog)
                assert dialog.secret_input.text() == ""
                assert dialog.model.text() == "synthetic/chat"
                dialog.secret_input.setText("synthetic-never-prefill")
                dialog.role.setCurrentIndex(dialog.role.findData("embeddings"))
                assert dialog.model.text() == "synthetic/embed" and dialog.secret_input.text() == ""
                dialog.role.setCurrentIndex(dialog.role.findData("chat_ai"))
                dialog.secret_input.setText("synthetic-key")
                QTest.mouseClick(dialog.connect_button, Qt.LeftButton)
                wait_qt(lambda: not dialog.busy)
                assert "metadata_verified" in dialog.status_label.text()
                checks.append(True)
            except BaseException as exc:
                errors.append(exc)
            finally:
                if isinstance(dialog, ProviderDialog):
                    dialog.reject()

        QTimer.singleShot(10, interact)
        QTest.mouseClick(opener, Qt.LeftButton)
        wait_qt(lambda: setup.pending is None)
        assert not errors and checks == [True]
        assert provider_threads and all(value != main_thread for value in provider_threads)
        assert qt_application.focusWidget() is opener
        assert "Metadata" in setup.connections["chat_ai"].text()
        assert "first_answer" in control.coordinator_getter().status().disabled_capabilities
    finally:
        setup.close()
        control.close().result(timeout=5)


def test_chat_endpoint_change_cannot_silently_change_embedding_identity(selected):
    settings, storage, store = selected
    local = Settings(
        _env_file=None,
        **(
            settings.model_dump()
            | {
                "ai_provider": "ollama",
                "embedding_provider": "ollama",
                "ollama_primary_model": "synthetic:latest",
                "ollama_embedding_model": "synthetic:latest",
                "cloud_consent": False,
            }
        ),
    )
    save_settings(local)

    def handle(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "synthetic:latest", "size": 100}]})
        return httpx.Response(200, json={"capabilities": ["completion", "embedding"]})

    with open_context((local, storage, store), transport=httpx.MockTransport(handle)) as context:
        result = context.provider.service.validate_and_save(
            "ollama",
            "",
            {
                "model": "synthetic:latest",
                "endpoint": "http://127.0.0.1:11435/v1",
                "cloud_consent": False,
            },
        )
        assert result.state == "ready"
        loaded = Settings(_env_file=None, data_dir=local.data_dir, profile_id=local.profile_id)
        assert loaded.embedding_profile == local.embedding_profile
        assert loaded.ollama_base_url == local.ollama_base_url
        assert context.provider.dialog_options("chat_ai")["endpoint"] == "http://127.0.0.1:11435/v1"
        assert context.provider.configuration_verification() is None


@pytest.mark.asyncio
async def test_readonly_gateway_uses_original_provider_expiry_without_probes(selected):
    from tg_assistant.desktop.worker import RuntimeGateway

    clock = [datetime.now(UTC)]
    requests = []

    def handle(request):
        requests.append(request.url.path)
        return metadata(request)

    with open_context(
        selected, now=lambda: clock[0], transport=httpx.MockTransport(handle)
    ) as context:
        context.begin()
        context.provider.service.validate_and_save("openrouter", "synthetic-key", choose())
        gateway = RuntimeGateway(19777, "a" * 32, setup_context=context)
        with context.engine.connect() as connection:
            before = connection.execute(select(AppSetting.key, AppSetting.value)).all()
        count = len(requests)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway), base_url="http://127.0.0.1:19777"
        ) as client:
            fresh = await client.get("/api/v1/connections")
            assert fresh.status_code == 200
            first = next(row for row in fresh.json() if row["service"] == "chat_ai")
            assert first["state"] == "ready" and first["capabilities"] == [
                "chat_metadata",
                "model_available",
            ]
            clock[0] += timedelta(seconds=61)
            expired = await client.get("/api/v1/setup/status")
            assert expired.status_code == 200
            ai = next(row for row in expired.json()["connections"] if row["service"] == "chat_ai")
            assert ai["state"] == "unknown" and ai["checked_at"] == first["checked_at"]
            assert "first_answer" in expired.json()["disabled_capabilities"]
        assert len(requests) == count
        with context.engine.connect() as connection:
            assert connection.execute(select(AppSetting.key, AppSetting.value)).all() == before


def test_integrated_provider_art_roles_targets_and_escape(selected, qt_application, tmp_path):
    from pathlib import Path

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QScrollArea, QWidget

    from tg_assistant.desktop.dialogs.provider import ProviderDialog

    with open_context(selected) as context:
        parent = QWidget()
        parent.show()
        dialog = ProviderDialog(
            context.provider.service,
            parent=parent,
            **context.provider.dialog_options("chat_ai"),
            selection_getter=context.provider.dialog_options,
            activation_notice=context.provider.activation_notice,
        )
        try:
            dialog.open()
            dialog.activateWindow()
            qt_application.processEvents()
            assert dialog.secret_input.text() == ""
            assert qt_application.font().family().startswith("Darley")
            title = next(
                label
                for label in dialog.findChildren(QLabel)
                if label.property("artRole") == "title"
            )
            assert title.font().family() == "LNTH-Peter Obscure"
            assert dialog.palette().window().color().name() == "#f3efdf"
            assert dialog.findChild(QScrollArea).horizontalScrollBar().maximum() == 0
            assert any(
                "chỉ mục embedding chờ" in label.text() for label in dialog.findChildren(QLabel)
            )
            for control in dialog.findChildren(QPushButton) + dialog.findChildren(QLineEdit):
                assert control.height() >= 44 and control.width() >= 44
            dialog.secret_input.setFocus()
            for _ in range(8):
                QTest.keyClick(qt_application.focusWidget(), Qt.Key_Tab)
                assert dialog.isAncestorOf(qt_application.focusWidget())
            output = Path(os.environ.get("ART_EVIDENCE_DIR", str(tmp_path)))
            output.mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(
                str(
                    output
                    / f"provider-native-{selected[0].storage_backend}-scale-{os.environ.get('QT_SCALE_FACTOR', '1')}.png"
                )
            )
            QTest.keyClick(qt_application.focusWidget(), Qt.Key_Escape)
            assert not dialog.isVisible()
        finally:
            dialog.close()
            parent.close()


def test_actual_launcher_reaches_real_provider_dialog(qt_application, tmp_path, monkeypatch):
    from desktop.test_launcher import controller, ready, stop
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest

    from tg_assistant.desktop.app import LauncherWindow
    from tg_assistant.desktop.dialogs.provider import ProviderDialog
    from tg_assistant.desktop.setup_ui import NativeSetupController

    for name in list(os.environ):
        if name.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(name)
    settings = Settings(_env_file=None, data_dir=tmp_path / "profile")
    runtime = controller(settings, tmp_path)
    window = LauncherWindow(runtime)
    original = NativeSetupController.for_settings
    store = Store()

    def injected(current, **kwargs):
        return original(
            current,
            **kwargs,
            context_options={
                "secret_store_factory": lambda: store,
                "provider_options": {"transport": httpx.MockTransport(metadata)},
            },
        )

    monkeypatch.setattr(NativeSetupController, "for_settings", injected)
    observations, errors = [], []
    poll, bailout = QTimer(), QTimer()
    try:
        window.show()
        QTest.qWait(50)
        ready(runtime)
        wait_qt(window.setup_button.isEnabled)

        def inspect():
            active = qt_application.activeModalWidget()
            if isinstance(active, ProviderDialog):
                observations.append(
                    (active.secret_input.text(), active.parent() is window.setup_dialog)
                )
                active.reject()
                return
            setup = window.setup_dialog
            if setup is not None and setup.pending is None:
                if not observations:
                    opener = setup.connection_buttons["open_connection_dialog"]
                    try:
                        assert opener.isEnabled()
                        # The parent's timer callback blocks in exec(); inspect
                        # the child using a separate single-shot timer event.
                        QTimer.singleShot(10, inspect)
                        QTest.mouseClick(opener, Qt.LeftButton)
                    except BaseException as error:
                        errors.append(error)
                        setup.reject()
                else:
                    setup.reject()

        poll.timeout.connect(inspect)
        poll.start(10)
        bailout.setSingleShot(True)

        def abandon():
            active = qt_application.activeModalWidget()
            if isinstance(active, ProviderDialog):
                active.reject()
            window.finish_setup()

        bailout.timeout.connect(abandon)
        bailout.start(7000)
        QTest.mouseClick(window.setup_button, Qt.LeftButton)
        assert observations == [("", True)] and not errors
        wait_qt(window.setup_button.hasFocus)
    finally:
        poll.stop()
        bailout.stop()
        window.close()
        stop(runtime)


def test_embedding_disconnect_closes_existing_runtime_config_gate(selected):
    from tg_assistant.config import current_model_enabled

    settings, storage, store = selected
    independent = Settings(_env_file=None, **(settings.model_dump() | {"ai_provider": "ollama"}))
    save_settings(independent)
    with open_context((independent, storage, store)) as context:
        context.provider.service.validate_and_save(
            "openrouter", "synthetic-key", choose("embeddings", "synthetic/embed")
        )
        result = context.provider.service.disconnect("openrouter")
        assert result.code == "disconnected"
        loaded = Settings(
            _env_file=None, data_dir=independent.data_dir, profile_id=independent.profile_id
        )
        assert loaded.ai_provider == "ollama"
        assert loaded.embedding_profile == independent.embedding_profile
        assert not loaded.enable_embeddings
        assert not current_model_enabled(independent, embedding=True)
        assert "openrouter_api_key" not in store.values


def test_provider_save_preserves_fresh_unrelated_settings(selected):
    settings = selected[0]
    with open_context(selected) as context:
        save_settings(settings.model_copy(update={"media_retention_hours": 59}))
        context.provider.service.validate_and_save(
            "openrouter", "synthetic-key", choose(model="synthetic/newchat")
        )
        loaded = Settings(
            _env_file=None, data_dir=settings.data_dir, profile_id=settings.profile_id
        )
        assert loaded.media_retention_hours == 59
        assert loaded.openrouter_primary_model == "synthetic/newchat"


def test_native_defaults_use_current_saved_choices_instead_of_launcher_snapshot(selected):
    settings = selected[0]
    save_settings(settings.model_copy(update={"openrouter_primary_model": "synthetic/newchat"}))
    with open_context(selected) as context:
        assert context.provider.dialog_options("chat_ai")["model"] == "synthetic/newchat"
        assert context.provider.service.connection_status("chat_ai").state == "unknown"


def test_changed_selected_backend_is_refused_without_silently_switching(selected):
    settings = selected[0]
    path = config_path(settings.data_dir)
    original = path.read_bytes()
    with pytest.raises(ValueError, match="non-secret"):
        save_settings(settings.model_copy(update={"storage_backend": "mysql"}))
    assert path.read_bytes() == original
    legacy = path.read_text(encoding="utf-8").replace(
        '"storage_backend": "sqlite"', '"storage_backend": "mysql"'
    )
    path.write_text(legacy, encoding="utf-8")
    with pytest.raises(ValueError, match="config"):
        with open_context(selected):
            pass
    assert path.read_text(encoding="utf-8") == legacy
