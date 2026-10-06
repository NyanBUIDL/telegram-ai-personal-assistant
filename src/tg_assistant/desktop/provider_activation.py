"""Trusted worker activation of saved compatible provider settings.

No inference, dimension discovery, model pull or corpus switch occurs here.
The existing engine factories retain consent, source policy and budget gates.
"""

from __future__ import annotations

import hashlib

from ..config import Settings, validate_settings
from ..paths import current_user_sid
from ..services.maintenance import FileLock, MaintenanceService


def _snapshot(runtime):
    owned = runtime.settings
    saved = validate_settings(
        Settings(_env_file=None, data_dir=owned.data_dir, profile_id=owned.profile_id)
    )
    if saved.storage_backend != owned.storage_backend or any(
        getattr(saved, name) != getattr(owned, name)
        for name in ("database_host", "database_port", "database_user", "database_name")
    ):
        raise ValueError("provider_storage_changed")
    if saved.embedding_profile.store_id != owned.embedding_profile.store_id:
        raise ValueError("provider_reindex_required")
    # Private memory only; never DTO/config/log. This also detects same-model key replacement.
    credentials = tuple(
        hashlib.sha256((runtime.store.get(name) or "").encode()).digest()
        for name in ("openai_api_key", "openrouter_api_key")
    )
    fields = (
        "ai_provider",
        "openai_primary_model",
        "openrouter_primary_model",
        "ollama_primary_model",
        "openai_base_url",
        "openrouter_base_url",
        "ollama_base_url",
        "cloud_consent",
        "enable_embeddings",
    )
    return saved, (tuple(getattr(saved, name) for name in fields), credentials)


async def _close_resources(resources):
    failed = []
    seen = set()
    for resource in resources:
        if resource is None or id(resource) in seen or not hasattr(resource, "close"):
            continue
        seen.add(id(resource))
        try:
            await resource.close()
        except Exception:
            # Retain failed clients for owning-loop cleanup retry; no raw exception is exposed.
            failed.append(resource)
    return failed


def _require_running(runtime):
    stopping = getattr(runtime, "stopping", None)
    if (
        getattr(runtime, "_closed", False)
        or getattr(runtime, "_close_task", None) is not None
        or (stopping is not None and stopping.is_set())
    ):
        raise ValueError("provider_runtime_closing")


async def reconcile_saved_provider_settings(runtime, *, current_sid=current_user_sid, fence=None):
    """Return whether current engines were applied; reject incompatible identity.

    Call from the owning runtime event loop. Pending choices in AppSetting do not
    bypass this saved-config check. The caller reports pending reindex separately.
    """
    _require_running(runtime)
    sid = current_sid()
    expected = getattr(runtime, "_native_provider_sid", sid)
    if not sid or sid != expected:
        raise ValueError("provider_sid_mismatch")
    runtime._native_provider_sid = expected
    owned_fence = fence or MaintenanceService(
        runtime.settings.data_dir / "config", profile_id=runtime.settings.profile_id
    )
    try:
        return await _reconcile(runtime, expected, current_sid, owned_fence)
    finally:
        if fence is None:
            owned_fence.close()


async def _reconcile(runtime, expected, current_sid, owned_fence):
    from ..runtime import make_ai_engine, make_ai_router, make_local_embedding_engine

    async with runtime._knowledge_lock:
        _require_running(runtime)
        retired = await _close_resources(getattr(runtime, "_native_provider_retired", ()))
        runtime._native_provider_retired = retired
        if retired:
            raise RuntimeError("provider_retirement_failed")
        with (
            owned_fence.operation(),
            FileLock(owned_fence.root / "provider-settings.lock", exclusive=True, timeout=1),
        ):
            saved, signature = _snapshot(runtime)
            if getattr(runtime, "_native_provider_signature", None) == signature:
                return False
            new_ai = new_router = new_embedding = None
            try:
                new_ai = make_ai_engine(saved, runtime.store, runtime.budget)
                new_router = make_ai_router(saved, runtime.store, runtime.budget, new_ai)
                new_embedding = make_local_embedding_engine(saved, runtime.store, runtime.budget)
                _require_running(runtime)
                if current_sid() != expected or _snapshot(runtime)[1] != signature:
                    raise ValueError("provider_plan_stale")
            except BaseException:
                candidates = list(new_router.engines.values()) if new_router else [new_ai]
                runtime._native_provider_retired = await _close_resources(
                    [*candidates, new_embedding]
                )
                raise
            old_router, old_embedding = runtime.ai_router, runtime.embedding_ai
            runtime.settings = saved
            runtime.ai, runtime.ai_router, runtime.embedding_ai = new_ai, new_router, new_embedding
            runtime.rag.ai, runtime.rag.router = new_ai, new_router
            runtime.rag.embedding_ai = new_embedding
            if runtime.bot:
                runtime.bot.ai, runtime.bot.rag = new_ai, runtime.rag
            runtime._native_provider_signature = signature
            # Existing vectors/database/policy/fence and all owner/source epochs remain intact.
            old_router.enabled = False
            for engine in old_router.engines.values():
                engine.enabled_check = lambda: False
            if hasattr(old_embedding, "enabled_check"):
                old_embedding.enabled_check = lambda: False
            runtime._native_provider_retired = await _close_resources(
                [*old_router.engines.values(), old_embedding]
            )
            if runtime._native_provider_retired:
                raise RuntimeError("provider_retirement_failed")
            return True
