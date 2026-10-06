"""Credential-free provider activation and native download behavioral evidence."""

import asyncio
import importlib
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

# Consume the existing migrated selected-backend fixture without duplicating DB ownership.
from test_provider_context import choose, open_context, selected  # noqa: F401

from tg_assistant.config import Settings, save_settings
from tg_assistant.services.ollama import OllamaError, OllamaPullCancelled, OllamaService
from tg_assistant.services.provider_connections import CredentialConnectionService


@pytest.mark.asyncio
async def test_pull_rejects_error_event_and_incomplete_stream():
    for content in (b'{"error":"synthetic-secret-error"}\n', b'{"status":"pulling"}\n'):
        service = OllamaService(
            "http://127.0.0.1:11434/v1",
            transport=httpx.MockTransport(
                lambda request, content=content: httpx.Response(200, content=content)
            ),
        )
        try:
            with pytest.raises(OllamaError) as error:
                await service.pull_model("synthetic:latest")
            assert "synthetic-secret" not in str(error.value)
        finally:
            await service.close()


@pytest.mark.asyncio
async def test_pull_cancel_before_request_does_not_download():
    requests = []
    service = OllamaService(
        "http://127.0.0.1:11434/v1",
        transport=httpx.MockTransport(
            lambda request: (
                requests.append(request) or httpx.Response(200, content=b'{"status":"success"}\n')
            )
        ),
    )

    async def cancelled():
        return True

    try:
        with pytest.raises(OllamaPullCancelled):
            await service.pull_model("synthetic:latest", should_cancel=cancelled)
        assert requests == []
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_manifest_size_preview_never_pulls_and_rejects_remote_cloud():
    requests = []

    def metadata(request):
        requests.append((request.method, str(request.url)))
        return httpx.Response(
            200,
            json={
                "config": {"size": 100},
                "layers": [
                    {"size": 4000, "mediaType": "application/vnd.ollama.image.model"},
                    {"size": 900, "mediaType": "application/vnd.ollama.image.template"},
                ],
            },
        )

    service = OllamaService("http://127.0.0.1:11434/v1", transport=httpx.MockTransport(metadata))
    try:
        assert hasattr(service, "preview_download"), "explicit size preview missing"
        preview = await service.preview_download("synthetic:latest")
        assert preview.model == "synthetic:latest" and preview.size_bytes == 5000
        assert requests == [
            ("GET", "https://registry.ollama.ai/v2/library/synthetic/manifests/latest")
        ]
        with pytest.raises(OllamaError):
            await service.preview_download("synthetic:cloud")
        with pytest.raises(OllamaError):
            await service.preview_download("../bad")
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_saved_chat_model_key_and_disconnect_apply_without_inference(tmp_path):
    from tg_assistant.ai.rag import RagService
    from tg_assistant.policy import PolicyEngine
    from tg_assistant.runtime import make_ai_engine, make_ai_router
    from tg_assistant.services.provider_connections import KEY_NAMES

    old = Settings(
        _env_file=None,
        data_dir=tmp_path,
        profile_id="activation",
        ai_provider="openai",
        cloud_consent=True,
        openai_primary_model="synthetic-old",
    )
    save_settings(old)
    store = SimpleNamespace(
        get=lambda name: "synthetic-new-key" if name in KEY_NAMES.values() else None
    )
    budget = SimpleNamespace()
    ai = make_ai_engine(old, store, budget)
    router = make_ai_router(old, store, budget, ai)
    runtime = SimpleNamespace(
        settings=old,
        store=store,
        budget=budget,
        ai=ai,
        ai_router=router,
        embedding_ai=object(),
        _knowledge_lock=asyncio.Lock(),
        rag=RagService(PolicyEngine(), ai, None, router, embedding_ai=object()),
        bot=SimpleNamespace(ai=ai),
    )
    saved = old.model_copy(update={"openai_primary_model": "synthetic-new"})
    save_settings(saved)
    module = importlib.import_module("tg_assistant.desktop.provider_activation")
    try:
        assert await module.reconcile_saved_provider_settings(runtime, current_sid=lambda: "sid")
        assert runtime.ai.model == "synthetic-new"
        assert runtime.rag.ai is runtime.ai and runtime.bot.ai is runtime.ai
        assert (
            runtime.rag.router is runtime.ai_router
            and runtime.rag.embedding_ai is runtime.embedding_ai
        )
        assert runtime.settings.embedding_profile == old.embedding_profile
        assert not await module.reconcile_saved_provider_settings(
            runtime, current_sid=lambda: "sid"
        )
        save_settings(saved.model_copy(update={"ai_provider": "off", "enable_embeddings": False}))
        assert await module.reconcile_saved_provider_settings(runtime, current_sid=lambda: "sid")
        assert runtime.ai_router.enabled is False and runtime.settings.enable_embeddings is False
    finally:
        await runtime.ai_router.close()
        await runtime.embedding_ai.close()


def test_native_download_requires_preview_confirmation_and_exposes_cancel(qt_application):
    from tg_assistant.desktop.dialogs.provider import ProviderDialog

    dialog = ProviderDialog(SimpleNamespace(), provider="ollama", model="synthetic:latest")
    try:
        assert hasattr(dialog, "preview_button"), "native size preview missing"
        assert dialog.preview_button.isEnabled()
        assert not dialog.pull_button.isEnabled()
        assert not dialog.cancel_pull_button.isEnabled()
        assert not dialog.download_consent.isChecked()
        dialog.model.setText("other:latest")
        assert not dialog.pull_button.isEnabled()
    finally:
        dialog.reject()


def test_native_busy_check_keeps_write_only_secret_disabled(qt_application):
    from tg_assistant.desktop.dialogs.provider import ProviderDialog

    finish = threading.Event()

    def validate(*args, **kwargs):
        finish.wait(1)

    dialog = ProviderDialog(
        SimpleNamespace(validate_and_save=validate),
        provider="openai",
        model="synthetic-chat",
        cloud_consent=True,
    )
    try:
        dialog.secret_input.setText("synthetic-key")
        dialog._start("connect")
        assert dialog.secret_input.text() == ""
        assert not dialog.secret_input.isEnabled()
        assert not dialog.preview_button.isEnabled()
    finally:
        finish.set()
        dialog.reject()


def test_native_cancel_targets_current_download_and_resume_requires_owner(qt_application):
    from tg_assistant.desktop.dialogs.provider import ProviderDialog

    started, finish = threading.Event(), threading.Event()
    seen = []

    def preview(model, **kwargs):
        return SimpleNamespace(model=model, size_bytes=5000)

    def pull(model, **kwargs):
        seen.append(kwargs["cancel"])
        started.set()
        finish.wait(2)
        return {
            "operation_id": "pull-synthetic",
            "state": "cancelled",
            "progress": None,
            "code": "pull_cancelled",
            "message": "Đã hủy tải.",
            "next_action": None,
        }

    dialog = ProviderDialog(
        SimpleNamespace(preview_local_download=preview, pull_local_model=pull),
        provider="ollama",
        model="synthetic:latest",
    )
    dialog.show()

    def drain():
        deadline = time.monotonic() + 2
        while dialog.busy and time.monotonic() < deadline:
            qt_application.processEvents()
            time.sleep(0.01)
        assert not dialog.busy

    try:
        dialog.preview_button.click()
        drain()
        assert "5000" in dialog.download_size.text().replace(",", "")
        dialog.download_consent.setChecked(True)
        dialog.pull_button.click()
        assert started.wait(1)
        dialog.cancel_pull_button.click()
        assert seen[0].is_set(), "cancel must affect active operation, not constructor event"
        finish.set()
        drain()
        assert not dialog.download_consent.isChecked() and not dialog.pull_button.isEnabled()
        dialog.pull_button.click()
        assert len(seen) == 1
    finally:
        finish.set()
        dialog.reject()


def test_default_local_pull_preview_progress_and_owner_resume():
    calls = []
    cancel = threading.Event()

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.host == "registry.ollama.ai":
            return httpx.Response(200, json={"config": {"size": 1}, "layers": [{"size": 99}]})
        return httpx.Response(
            200, content=b'{"status":"pulling","total":99,"completed":49}\n{"status":"success"}\n'
        )

    service = CredentialConnectionService(
        persist_selection=lambda selection: None,
        store=SimpleNamespace(),
        transport=httpx.MockTransport(handler),
        current_sid=lambda: "sid",
    )
    with pytest.raises(ValueError, match="pull_preview_required"):
        service.pull_local_model(
            "synthetic:latest", size_bytes=100, owner_confirmed=True, cancel=cancel
        )
    preview = service.preview_local_download("synthetic:latest")
    events = []

    def interrupt(event):
        events.append(event)
        cancel.set()

    result = service.pull_local_model(
        preview.model, size_bytes=100, owner_confirmed=True, cancel=cancel, on_progress=interrupt
    )
    assert result.state == "cancelled" and events[0]["progress"] == 49
    assert set(events[0]) == {"total_bytes", "completed_bytes", "progress"}
    with pytest.raises(ValueError, match="pull_consent_required"):
        service.pull_local_model(
            preview.model, size_bytes=100, owner_confirmed=False, cancel=threading.Event()
        )
    result = service.pull_local_model(
        preview.model, size_bytes=100, owner_confirmed=True, cancel=threading.Event()
    )
    assert result.state == "completed"
    assert calls == [
        ("GET", "/v2/library/synthetic/manifests/latest"),
        ("POST", "/api/pull"),
        ("POST", "/api/pull"),
    ]


def test_stalled_local_stream_can_be_cancelled_before_next_provider_byte():
    class Stalled(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b""

    started, cancel = threading.Event(), threading.Event()

    def handler(request):
        if request.url.host == "registry.ollama.ai":
            return httpx.Response(200, json={"config": {"size": 1}, "layers": [{"size": 99}]})
        return httpx.Response(200, stream=Stalled())

    service = CredentialConnectionService(
        persist_selection=lambda selection: None,
        store=SimpleNamespace(),
        transport=httpx.MockTransport(handler),
        current_sid=lambda: "sid",
    )
    service.preview_local_download("synthetic:latest")
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            service.pull_local_model,
            "synthetic:latest",
            size_bytes=100,
            owner_confirmed=True,
            cancel=cancel,
        )
        assert started.wait(1)
        cancel.set()
        assert future.result(timeout=1).state == "cancelled"


def test_worker_measures_saved_choices_then_reports_disconnect(selected):  # noqa: F811
    with open_context(selected) as native, open_context(selected) as worker:
        assert (
            native.provider.service.validate_and_save("openrouter", "synthetic-key", choose()).state
            == "ready"
        )
        assert worker.provider.service.connection_status("chat_ai").state == "unknown"
        worker.provider.refresh_saved_health()
        measured = worker.provider.service.connection_status("chat_ai")
        assert measured.state == "ready" and "chat_metadata" in measured.capabilities
        native.provider.service.disconnect("openrouter")
        worker.provider.refresh_saved_health()
        assert worker.provider.service.connection_status("chat_ai").state == "disconnected"


def test_worker_pending_embedding_and_unapplied_runtime_are_degraded(selected):  # noqa: F811
    old = selected[0]
    with open_context(selected) as native, open_context(selected) as worker:
        native.provider.service.validate_and_save(
            "openrouter", "synthetic-key", choose(model="synthetic/newchat")
        )
        native.provider.service.validate_and_save(
            "openrouter", "", choose("embeddings", "synthetic/new")
        )
        worker.provider.refresh_saved_health(active_settings=old)
        chat = worker.provider.service.connection_status("chat_ai")
        embedding = worker.provider.service.connection_status("embeddings")
        assert chat.state == "degraded" and chat.code == "provider_activation_required"
        assert embedding.state == "degraded" and embedding.code == "provider_reindex_required"
        assert worker.provider.configuration_verification() is None


@pytest.mark.parametrize("gate", ["embedding_disabled", "all_ai_off"])
def test_worker_embedding_metadata_cannot_override_actual_disabled_gate(selected, gate):  # noqa: F811
    settings, _, _ = selected
    with open_context(selected) as native:
        assert native.provider.service.validate_and_save(
            "openrouter", "synthetic-key", choose("embeddings", "synthetic/embed")
        ).state == "ready"
    active = settings.model_copy(
        update={"enable_embeddings": False}
        if gate == "embedding_disabled"
        else {"ai_provider": "off"}
    )
    save_settings(active)
    with open_context(selected) as worker:
        worker.provider.refresh_saved_health(active_settings=active)
        measured = worker.provider.service.connection_status("embeddings")
        assert measured.state == "degraded" and measured.code == "embedding_disabled"
        assert measured.next_action and "embedding_metadata" not in measured.capabilities


@pytest.mark.asyncio
async def test_activation_same_model_key_change_withdraws_old_references_and_keeps_policy(tmp_path):
    from tg_assistant.ai.engine import AiPolicyError
    from tg_assistant.runtime import make_ai_engine, make_ai_router

    module = importlib.import_module("tg_assistant.desktop.provider_activation")
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        profile_id="keychange",
        ai_provider="openai",
        cloud_consent=True,
    )
    save_settings(settings)
    keys = {"openai_api_key": "synthetic-key"}
    store = SimpleNamespace(get=keys.get)
    ai = make_ai_engine(settings, store, SimpleNamespace())
    runtime = SimpleNamespace(
        settings=settings,
        store=store,
        budget=SimpleNamespace(),
        ai=ai,
        ai_router=make_ai_router(settings, store, SimpleNamespace(), ai),
        embedding_ai=object(),
        _knowledge_lock=asyncio.Lock(),
        rag=SimpleNamespace(ai=ai, router=None),
        bot=None,
    )
    try:
        assert await module.reconcile_saved_provider_settings(runtime, current_sid=lambda: "sid")
        retired = runtime.ai
        keys["openai_api_key"] = "synthetic-replacement"
        assert await module.reconcile_saved_provider_settings(runtime, current_sid=lambda: "sid")
        assert runtime.ai is not retired
        with pytest.raises(AiPolicyError):
            retired._current_consent()
        with pytest.raises(AiPolicyError):
            runtime.ai._validate_content(["synthetic source"], ["local_only"])
        with pytest.raises(ValueError, match="provider_sid_mismatch"):
            await module.reconcile_saved_provider_settings(runtime, current_sid=lambda: "other-sid")
        runtime._closed = True
        with pytest.raises(ValueError, match="provider_runtime_closing"):
            await module.reconcile_saved_provider_settings(runtime, current_sid=lambda: "sid")
    finally:
        await runtime.ai_router.close()
        await runtime.embedding_ai.close()


@pytest.mark.asyncio
async def test_activation_failed_embedding_construction_closes_candidate_router(
    tmp_path, monkeypatch
):
    from tg_assistant import runtime as runtime_module

    module = importlib.import_module("tg_assistant.desktop.provider_activation")
    settings = Settings(
        _env_file=None, data_dir=tmp_path, profile_id="failure", ai_provider="ollama"
    )
    save_settings(settings)
    store = SimpleNamespace(get=lambda name: None)
    ai = runtime_module.make_ai_engine(settings, store, SimpleNamespace())
    old_router = runtime_module.make_ai_router(settings, store, SimpleNamespace(), ai)
    runtime = SimpleNamespace(
        settings=settings,
        store=store,
        budget=SimpleNamespace(),
        ai=ai,
        ai_router=old_router,
        embedding_ai=object(),
        _knowledge_lock=asyncio.Lock(),
        rag=SimpleNamespace(ai=ai, router=old_router),
        bot=None,
    )
    created = []
    original = runtime_module.make_ai_router

    def capture(*args):
        router = original(*args)
        created.extend(router.engines.values())
        return router

    monkeypatch.setattr(runtime_module, "make_ai_router", capture)

    def fail(*args):
        raise RuntimeError("synthetic factory error")

    monkeypatch.setattr(runtime_module, "make_local_embedding_engine", fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic factory"):
            await module.reconcile_saved_provider_settings(runtime, current_sid=lambda: "sid")
        assert runtime.ai_router is old_router and runtime.ai is ai
        assert all(engine.client is None or engine.client.is_closed() for engine in created)
    finally:
        await old_router.close()
