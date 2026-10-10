"""Independent-review probe for O01/O02 (Claude version): does the 15 s worker refresh
cycle keep already completed downstream setup history?

The worker calls prepare_health -> coordinator.resume -> advance_verified every ~15 s
(desktop/worker.py::refresh_setup). prepare_health re-probes the saved providers, which
re-stamps ``checked_at``; the AI verification fingerprint embeds that timestamp
(provider_connections.verification_fingerprint). If a re-stamped fingerprint makes the
recorded AI evidence mismatch, advance_verified re-completes ai_configured and
complete_stage() invalidates every LATER stage - including stages that advance_verified
cannot restore (bot_verified, owner_paired, source_selected ...).
"""

from __future__ import annotations

import importlib

import httpx
from test_provider_context import choose, metadata, selected  # noqa: F401  (fixture import)

from tg_assistant.services.onboarding import StageVerification

OWNER = 7_000_000_001


def verifier(owner=None, seed="a"):
    fingerprint = seed * 64
    return lambda: StageVerification(owner_id=owner, fingerprint=fingerprint)


def open_with_stage_verifiers(selected_case):
    module = importlib.import_module("tg_assistant.desktop.setup_context")
    settings, _, store = selected_case
    return module.open_setup_context(
        settings,
        secret_store_factory=lambda: store,
        provider_options={"transport": httpx.MockTransport(metadata)},
        verifiers={
            # Stable, trusted owning-service answers for the stages after the AI step.
            "telegram_verified": verifier(OWNER, "b"),
            "bot_verified": verifier(None, "c"),
            "owner_paired": verifier(OWNER, "d"),
            "source_selected": verifier(None, "e"),
        },
    )


def complete(coordinator, stage):
    return coordinator.complete_stage(stage, coordinator.verify_stage(stage))


def test_worker_refresh_cycle_keeps_completed_downstream_history(selected):  # noqa: F811
    with open_with_stage_verifiers(selected) as context:
        context.begin()
        context.provider.service.validate_and_save("openrouter", "synthetic-key", choose())
        context.provider.service.validate_and_save(
            "openrouter", "", choose("embeddings", "synthetic/embed")
        )
        coordinator = context.coordinator
        for stage in (
            "ai_configured",
            "telegram_verified",
            "bot_verified",
            "owner_paired",
            "source_selected",
        ):
            complete(coordinator, stage)
        before = coordinator.status().stage_evidence_ids
        assert {"bot_verified", "owner_paired", "source_selected"} <= set(before), before

        history = []
        for _cycle in range(3):
            # Exactly what desktop/worker.py::refresh_setup does each ~15 s tick.
            context.prepare_health()
            coordinator.resume()
            context.advance_verified()
            after = coordinator.status().stage_evidence_ids
            history.append(sorted(after))
        assert history[-1] == sorted(before), (
            "completed downstream stages were lost across refresh cycles "
            f"(before={sorted(before)}, after each cycle={history})"
        )


def test_stale_provider_measurement_pauses_readiness_but_keeps_history(selected):  # noqa: F811
    from datetime import UTC, datetime, timedelta

    clock = [datetime(2026, 10, 5, tzinfo=UTC)]
    module = importlib.import_module("tg_assistant.desktop.setup_context")
    settings, _, store = selected
    with module.open_setup_context(
        settings,
        secret_store_factory=lambda: store,
        provider_options={"transport": httpx.MockTransport(metadata), "now": lambda: clock[0]},
        verifiers={
            "telegram_verified": verifier(OWNER, "b"),
            "bot_verified": verifier(None, "c"),
            "owner_paired": verifier(OWNER, "d"),
        },
    ) as context:
        context.begin()
        context.provider.service.validate_and_save("openrouter", "synthetic-key", choose())
        context.provider.service.validate_and_save(
            "openrouter", "", choose("embeddings", "synthetic/embed")
        )
        coordinator = context.coordinator
        for stage in ("ai_configured", "telegram_verified", "bot_verified", "owner_paired"):
            complete(coordinator, stage)
        before = dict(coordinator.status().stage_evidence_ids)

        clock[0] += timedelta(seconds=61)  # the provider measurement is now stale
        stale = coordinator.status().stage_evidence_ids
        assert "ai_configured" not in stale, "a stale measurement must not count as configured"
        assert "owner_paired" in coordinator._load().completed, "history must survive staleness"

        context.prepare_health()  # a fresh measurement of the SAME configuration
        context.advance_verified()
        assert dict(coordinator.status().stage_evidence_ids) == before
