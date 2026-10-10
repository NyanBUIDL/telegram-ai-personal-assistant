"""Persisted setup must not turn browser claims or stale probes into authority."""

from __future__ import annotations

import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select

from tg_assistant.contracts import ConnectionState, OnboardingStage
from tg_assistant.db.base import configure_sqlite
from tg_assistant.db.models import AppSetting
from tg_assistant.services.maintenance import MaintenanceBusy, MaintenanceService


@pytest.fixture
def modules():
    try:
        return SimpleNamespace(
            onboarding=importlib.import_module("tg_assistant.services.onboarding"),
            connections=importlib.import_module("tg_assistant.services.connections"),
        )
    except ModuleNotFoundError:
        pytest.fail("O01 persisted setup and measured health behavior is not implemented")


@pytest.fixture(params=["sqlite"])
def system(tmp_path, modules, request):
    clock = [datetime(2026, 10, 5, tzinfo=UTC)]
    engine = create_engine("sqlite:///" + str(tmp_path / "selected.db"))
    event.listen(engine, "connect", configure_sqlite)
    AppSetting.__table__.create(engine)
    fence = MaintenanceService(tmp_path / "config", profile_id="owner-profile")
    healthy = {
        "storage": True,
        "telegram_account": True,
        "control_bot": True,
        "chat_ai": True,
        "embeddings": True,
        "runtime": True,
    }
    owners = {"telegram_verified": 9007199254740993, "owner_paired": 9007199254740993}
    active = {stage.value: True for stage in OnboardingStage}
    probes = {
        name: (
            lambda name=name: modules.connections.HealthObservation(
                state="ready" if healthy[name] else "disconnected",
                capabilities=("measured",),
            )
        )
        for name in healthy
    }
    probes["storage"] = lambda: (
        modules.connections.storage_probe(engine)()
        if healthy["storage"]
        else modules.connections.HealthObservation(state="disconnected")
    )
    conns = modules.connections.ConnectionHealth(
        probes=probes, now=lambda: clock[0], ttl_seconds=60
    )
    conns.refresh()
    verifiers = {
        stage: (
            lambda stage=stage: (
                modules.onboarding.StageVerification(
                    owner_id=owners.get(stage.value),
                    fingerprint="a" * 64,
                )
                if active[stage.value]
                else None
            )
        )
        for stage in OnboardingStage
        if stage != OnboardingStage.READY
    }
    verifiers[OnboardingStage.STORAGE_READY] = lambda: (
        modules.onboarding.storage_verifier(engine)() if active["storage_ready"] else None
    )

    def make(profile_id="owner-profile", health=None, validators=None, ttl_seconds=300):
        profile_fence = (
            fence
            if profile_id == "owner-profile"
            else MaintenanceService(
                tmp_path / ("config-" + profile_id),
                profile_id=profile_id,
            )
        )
        return modules.onboarding.OnboardingCoordinator(
            engine=engine,
            profile_id=profile_id,
            storage_backend=request.param,
            fence=profile_fence,
            connections=health or conns,
            verifiers=verifiers if validators is None else validators,
            now=lambda: clock[0],
            evidence_ttl_seconds=ttl_seconds,
        )

    value = SimpleNamespace(
        engine=engine,
        fence=fence,
        clock=clock,
        healthy=healthy,
        owners=owners,
        active=active,
        connections=conns,
        coordinator=make(),
        make=make,
        modules=modules,
    )
    yield value
    fence.close()
    engine.dispose()


def complete(coordinator, stage):
    identifier = coordinator.verify_stage(stage)
    return coordinator.complete_stage(stage, identifier)


def advance(system, *, skip=False, through="source_selected"):
    system.coordinator.save_options({"ai_mode": "skip" if skip else "local", "provider": "ollama"})
    for stage in OnboardingStage:
        if stage == OnboardingStage.READY:
            break
        complete(system.coordinator, stage)
        if stage.value == through:
            break


def test_resume_without_secrets(system):
    system.coordinator.save_options({"ai_mode": "local", "provider": "ollama", "model": "llama3"})
    complete(system.coordinator, "welcome")
    complete(system.coordinator, "storage_ready")
    restored = system.make()
    assert restored.resume().profile.setup_stage == "storage_ready"
    assert restored.options() == {
        "ai_mode": "local",
        "provider": "ollama",
        "model": "llama3",
        "source_id": None,
    }
    assert "storage_ready" in restored.status().stage_evidence_ids
    with system.engine.connect() as conn:
        stored = json.dumps(conn.execute(select(AppSetting.value)).scalar_one())
    assert "llama3" in stored
    assert all(
        secret not in stored for secret in ("otp", "password", "raw_ticket", "api_key", "session")
    )


@pytest.mark.parametrize(
    "values",
    [
        {"otp": "123456"},
        {"api_key": "sk-secret"},
        {"model": "https://alice:secret@example.com"},
        {"source_id": 0},
        {"unknown": "value"},
    ],
)
def test_options_reject_secret_and_unbounded_inputs(system, values):
    with pytest.raises(ValueError) as error:
        system.coordinator.save_options(values)
    assert not any(str(value) in str(error.value) for value in values.values())
    assert system.make().options()["model"] is None


def test_offline_save_resume(system):
    system.healthy["telegram_account"] = False
    system.connections.refresh()
    system.coordinator.save_options({"ai_mode": "cloud", "provider": "openai", "model": "gpt-4.1"})
    system.coordinator.record_error("connection_unavailable")
    restored = system.make()
    assert restored.options()["provider"] == "openai"
    assert restored.resume().profile.setup_stage == "welcome"
    assert restored.status().next_action
    assert "chat_ai" in restored.status().disabled_capabilities


def test_resume_refreshes_completed_history_only_from_actual_recheck(system):
    advance(system, skip=True)
    before = complete(system.coordinator, "ready")
    system.clock[0] += timedelta(days=2)
    restored = system.make()
    assert restored.status().profile.setup_stage != "ready"
    resumed = restored.resume()
    assert resumed.profile.setup_stage == "ready"
    assert resumed.stage_evidence_ids == before.stage_evidence_ids
    assert resumed.profile.owner_id == 9007199254740993


def test_completed_history_ttl_is_not_pending_authority(system):
    advance(system, skip=True)
    before = complete(system.coordinator, "ready")
    system.clock[0] += timedelta(seconds=301)
    system.connections.refresh()
    # Completed stages are live-rechecked; pending proof TTL alone cannot erase
    # already completed nonsecret history or replace the actual service checks.
    result = system.coordinator.status()
    assert result.profile.setup_stage == "ready"
    assert result.stage_evidence_ids == before.stage_evidence_ids


def test_resume_never_renews_expired_pending_evidence(system):
    complete(system.coordinator, "welcome")
    pending = system.coordinator.verify_stage("storage_ready")
    system.clock[0] += timedelta(seconds=301)
    system.make().resume()
    with pytest.raises(ValueError, match="evidence_expired"):
        system.coordinator.complete_stage("storage_ready", pending)


def test_resume_keeps_history_but_downgrades_when_issuer_is_unavailable(system):
    advance(system, skip=True)
    before = complete(system.coordinator, "ready")
    system.clock[0] += timedelta(days=2)
    resumed = system.make(validators={}).resume()
    assert resumed.profile.setup_stage == "welcome"
    assert resumed.profile.owner_id is None
    assert "management" in resumed.disabled_capabilities
    with system.engine.connect() as conn:
        stored = conn.execute(select(AppSetting.value)).scalar_one()
    assert stored["completed"] == {
        stage.value: identifier for stage, identifier in before.stage_evidence_ids.items()
    }


def test_resume_rejects_changed_owner_and_completed_replay(system):
    advance(system, skip=True)
    before = complete(system.coordinator, "ready")
    system.clock[0] += timedelta(days=2)
    system.owners["telegram_verified"] = 42
    resumed = system.make().resume()
    assert resumed.profile.setup_stage == "ai_configured"
    assert resumed.profile.owner_id is None
    with pytest.raises(ValueError, match="evidence_consumed"):
        system.coordinator.complete_stage(
            "welcome", before.stage_evidence_ids[OnboardingStage.WELCOME]
        )


def test_ready_requires_verified_capabilities(system):
    advance(system)
    with pytest.raises(ValueError, match="first_answer_required"):
        complete(system.coordinator, "ready")
    complete(system.coordinator, "first_answer")
    result = complete(system.coordinator, "ready")
    assert result.profile.setup_stage == "ready"
    assert result.profile.owner_id == 9007199254740993
    assert json.loads(result.model_dump_json())["profile"]["owner_id"] == "9007199254740993"
    system.healthy["control_bot"] = False
    system.connections.refresh()
    assert system.coordinator.status().profile.setup_stage != "ready"
    assert "control_bot" in system.coordinator.status().disabled_capabilities


def test_skip_ai_is_limited_not_broken(system):
    advance(system, skip=True)
    result = complete(system.coordinator, "ready")
    assert result.profile.setup_stage == "ready"
    assert {"chat_ai", "embeddings", "first_answer"} <= set(result.disabled_capabilities)
    assert "first_answer" not in result.stage_evidence_ids


def test_skip_ai_cannot_issue_first_answer_success(system):
    advance(system, skip=True)
    with pytest.raises(ValueError, match="first_answer_disabled"):
        system.coordinator.verify_stage("first_answer")
    assert "first_answer" not in system.coordinator.status().stage_evidence_ids


def test_skip_still_requires_verified_source(system):
    advance(system, skip=True, through="owner_paired")
    with pytest.raises(ValueError, match="source_selected_required"):
        complete(system.coordinator, "ready")


def test_unpaired_status_keeps_nullable_owner(system):
    assert system.coordinator.status().profile.owner_id is None
    assert system.coordinator.status().profile.setup_stage == "welcome"
    assert "management" in system.coordinator.status().disabled_capabilities


def test_unknown_or_forged_evidence_never_advances(system):
    with pytest.raises(ValueError, match="evidence_invalid"):
        system.coordinator.complete_stage("telegram_verified", "browser-claim")
    assert system.coordinator.status().profile.owner_id is None


def test_wrong_stage_expired_cross_profile_and_consumed_evidence(system):
    identifier = system.coordinator.verify_stage("welcome")
    with pytest.raises(ValueError, match="evidence_invalid"):
        system.coordinator.complete_stage("storage_ready", identifier)
    with pytest.raises(ValueError, match="evidence_invalid"):
        system.make(profile_id="other-profile").complete_stage("welcome", identifier)
    system.coordinator.complete_stage("welcome", identifier)
    with pytest.raises(ValueError, match="evidence_consumed"):
        system.coordinator.complete_stage("welcome", identifier)
    expired = system.coordinator.verify_stage("storage_ready")
    system.clock[0] += timedelta(seconds=301)
    with pytest.raises(ValueError, match="evidence_expired"):
        system.coordinator.complete_stage("storage_ready", expired)


@pytest.mark.parametrize("ttl_seconds", [301, 600])
def test_pending_evidence_ttl_cannot_exceed_300_seconds(system, ttl_seconds):
    with pytest.raises(ValueError, match="evidence_ttl_invalid"):
        system.make(ttl_seconds=ttl_seconds)


@pytest.mark.parametrize("ttl_seconds", [1, 300])
def test_pending_evidence_ttl_accepts_positive_bounded_values(system, ttl_seconds):
    identifier = system.make(ttl_seconds=ttl_seconds).verify_stage("welcome")
    with system.engine.connect() as connection:
        stored = connection.execute(select(AppSetting.value)).scalar_one()
    record = stored["evidence"][identifier]
    checked = datetime.fromisoformat(record["checked_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
    assert (expires - checked).total_seconds() == ttl_seconds


@pytest.mark.parametrize("elapsed, allowed", [(299, True), (300, False)])
def test_pending_evidence_deadline_is_exclusive_at_exact_300(system, elapsed, allowed):
    complete(system.coordinator, "welcome")
    identifier = system.coordinator.verify_stage("storage_ready")
    system.clock[0] += timedelta(seconds=elapsed)
    with system.engine.connect() as connection:
        before = connection.execute(select(AppSetting.value)).scalar_one()
    if allowed:
        system.coordinator.complete_stage("storage_ready", identifier)
        with system.engine.connect() as connection:
            stored = connection.execute(select(AppSetting.value)).scalar_one()
        assert stored["completed"]["storage_ready"] == identifier
    else:
        with pytest.raises(ValueError, match="evidence_expired"):
            system.coordinator.complete_stage("storage_ready", identifier)
        with system.engine.connect() as connection:
            assert connection.execute(select(AppSetting.value)).scalar_one() == before


@pytest.mark.parametrize("slow_check", ["owning", "prerequisite"])
@pytest.mark.parametrize("delay_seconds", [1, 2])
def test_pending_expiry_after_actual_rechecks_rolls_back(system, slow_check, delay_seconds):
    complete(system.coordinator, "welcome")
    slow = [False]
    actual_storage = system.modules.onboarding.storage_verifier(system.engine)

    def storage():
        result = actual_storage()
        if slow[0] and slow_check == "owning":
            system.clock[0] += timedelta(seconds=delay_seconds)
        return result

    def welcome():
        if slow[0] and slow_check == "prerequisite":
            system.clock[0] += timedelta(seconds=delay_seconds)
        return system.modules.onboarding.StageVerification(fingerprint="a" * 64)

    coordinator = system.make(validators={"welcome": welcome, "storage_ready": storage})
    identifier = coordinator.verify_stage("storage_ready")
    system.clock[0] += timedelta(seconds=299)
    slow[0] = True
    with system.engine.connect() as connection:
        before = connection.execute(select(AppSetting.value)).scalar_one()
    with pytest.raises(ValueError, match="evidence_expired"):
        coordinator.complete_stage("storage_ready", identifier)
    with system.engine.connect() as connection:
        after = connection.execute(select(AppSetting.value)).scalar_one()
    assert after == before  # No consumption, invalidation, revision update or renewal.
    assert "storage_ready" not in after["completed"]
    assert identifier in after["evidence"]


def test_recheck_prevents_stale_verification_commit(system):
    complete(system.coordinator, "welcome")
    identifier = system.coordinator.verify_stage("storage_ready")
    system.active["storage_ready"] = False
    with pytest.raises(ValueError, match="verification_unavailable"):
        system.coordinator.complete_stage("storage_ready", identifier)
    assert system.coordinator.status().profile.setup_stage == "welcome"


def test_pairing_must_match_actual_positive_owner(system):
    advance(system, through="bot_verified")
    system.owners["owner_paired"] = 42
    with pytest.raises(ValueError, match="owner_mismatch"):
        complete(system.coordinator, "owner_paired")
    assert "management" in system.coordinator.status().disabled_capabilities


def test_owner_change_invalidates_downstream_evidence(system):
    advance(system, skip=True)
    complete(system.coordinator, "ready")
    system.owners["telegram_verified"] = 42
    result = system.coordinator.status()
    assert result.profile.setup_stage == "ai_configured"
    assert result.profile.owner_id is None
    assert "management" in result.disabled_capabilities


def test_missing_verifiers_do_not_fabricate_stages(system):
    coordinator = system.make(validators={})
    with pytest.raises(ValueError, match="verification_unavailable"):
        coordinator.verify_stage("telegram_verified")
    assert coordinator.status().profile.owner_id is None


def test_write_fence_covers_options_errors_evidence_and_completion(system):
    identifier = system.coordinator.verify_stage("welcome")
    lease = system.fence.acquire("test-restore", timeout=0)
    try:
        for operation in (
            lambda: system.coordinator.save_options({"ai_mode": "skip"}),
            lambda: system.coordinator.record_error("connection_unavailable"),
            lambda: system.coordinator.verify_stage("storage_ready"),
            lambda: system.coordinator.complete_stage("welcome", identifier),
            lambda: system.coordinator.resume(),
        ):
            with pytest.raises(MaintenanceBusy):
                operation()
    finally:
        system.fence.release(lease)


def test_concurrent_evidence_issuance_does_not_lose_updates(system):
    def issue(_):
        return system.make().verify_stage("welcome")

    with ThreadPoolExecutor(max_workers=4) as executor:
        identifiers = list(executor.map(issue, range(12)))
    assert len(set(identifiers)) == 12
    with system.engine.connect() as conn:
        stored = conn.execute(select(AppSetting.value)).scalar_one()
    assert set(identifiers) <= set(stored["evidence"])


def test_stale_health_is_unknown(system):
    system.clock[0] += timedelta(seconds=61)
    status = system.connections.status()
    assert all(item.state == "unknown" and item.capabilities == [] for item in status)
    assert system.coordinator.status().profile.setup_stage != "ready"


def test_missing_health_does_not_claim_online(modules):
    health = modules.connections.ConnectionHealth(probes={})
    assert len(health.status()) == 6
    assert all(item.checked_at is None and item.state == "unknown" for item in health.status())


def test_health_failure_sanitizes_errors_and_keeps_capabilities_separate(modules):
    def failed():
        raise RuntimeError("sk-secret OTP 123456 https://alice:password@example.com")

    health = modules.connections.ConnectionHealth(
        probes={
            "chat_ai": failed,
            "embeddings": lambda: modules.connections.HealthObservation(
                state="degraded", capabilities=("keyword_only",)
            ),
        }
    )
    health.refresh()
    status = {item.service.value: item for item in health.status()}
    assert status["chat_ai"].state == ConnectionState.DISCONNECTED
    assert status["embeddings"].state == ConnectionState.DEGRADED
    assert status["embeddings"].capabilities == ["keyword_only"]
    assert "sk-secret" not in json.dumps([item.model_dump(mode="json") for item in health.status()])


def test_storage_health_runs_real_selected_database_query(system):
    probe = system.modules.connections.storage_probe(system.engine)
    health = system.modules.connections.ConnectionHealth(probes={"storage": probe})
    health.refresh()
    storage = next(item for item in health.status() if item.service == "storage")
    assert storage.state == "ready"
    assert storage.checked_at is not None


def test_future_and_exact_expired_health_cannot_claim_ready(system):
    system.clock[0] -= timedelta(seconds=1)
    assert all(item.state == "unknown" for item in system.connections.status())
    system.clock[0] += timedelta(seconds=61)
    assert all(item.state == "unknown" for item in system.connections.status())


def test_live_checking_state_cannot_retain_ready_capabilities(modules):
    from threading import Event, Thread

    entered, release = Event(), Event()

    def probe():
        entered.set()
        assert release.wait(5)
        return modules.connections.HealthObservation(state="ready", capabilities=("chat",))

    health = modules.connections.ConnectionHealth(probes={"chat_ai": probe})
    worker = Thread(target=lambda: health.refresh("chat_ai"))
    worker.start()
    try:
        assert entered.wait(5)
        measured = next(item for item in health.status() if item.service == "chat_ai")
        assert measured.state == "checking"
        assert measured.capabilities == []
    finally:
        release.set()
        worker.join(5)


def test_older_probe_completion_cannot_overwrite_newer_disconnection(modules):
    from threading import Event, Lock, Thread

    entered, release, lock = Event(), Event(), Lock()
    calls = [0]

    def probe():
        with lock:
            calls[0] += 1
            first = calls[0] == 1
        if first:
            entered.set()
            assert release.wait(5)
            return modules.connections.HealthObservation(state="ready", capabilities=("chat",))
        return modules.connections.HealthObservation(state="disconnected")

    health = modules.connections.ConnectionHealth(probes={"chat_ai": probe})
    worker = Thread(target=lambda: health.refresh("chat_ai"))
    worker.start()
    try:
        assert entered.wait(5)
        health.refresh("chat_ai")
    finally:
        release.set()
        worker.join(5)
    measured = next(item for item in health.status() if item.service == "chat_ai")
    assert measured.state == "disconnected"
    assert measured.capabilities == []


def test_source_option_updates_preserve_completed_evidence_when_unchanged(system):
    advance(system, skip=True)
    complete(system.coordinator, "ready")
    system.coordinator.save_options({"source_id": "-100123"})
    complete(system.coordinator, "source_selected")
    complete(system.coordinator, "ready")
    result = system.coordinator.save_options({"source_id": "-100123"})
    assert result.profile.setup_stage == "ready"


def test_invalid_stage_or_evidence_errors_do_not_echo_inputs(system):
    for operation in (
        lambda: system.coordinator.verify_stage("sk-do-not-echo"),
        lambda: system.coordinator.complete_stage("welcome", {"otp": "123456"}),
    ):
        with pytest.raises(ValueError) as error:
            operation()
        assert "sk-do-not-echo" not in str(error.value)
        assert "123456" not in str(error.value)


def test_stored_foreign_issuer_is_not_authority(system):
    complete(system.coordinator, "welcome")
    with system.engine.begin() as connection:
        stored = connection.execute(select(AppSetting.value)).scalar_one()
        for record in stored["evidence"].values():
            record["issuer"] = "browser"
        from sqlalchemy import update

        connection.execute(update(AppSetting).values(value=stored))
    assert system.coordinator.status().stage_evidence_ids == {}


def test_corrupted_ready_proof_cannot_claim_readiness(system):
    advance(system, skip=True)
    complete(system.coordinator, "ready")
    with system.engine.begin() as connection:
        stored = connection.execute(select(AppSetting.value)).scalar_one()
        identifier = stored["completed"]["ready"]
        stored["evidence"][identifier]["verification"]["fingerprint"] = "f" * 64
        from sqlalchemy import update

        connection.execute(update(AppSetting).values(value=stored))
    assert system.coordinator.status().profile.setup_stage != "ready"


def test_stale_or_disconnected_identity_services_disable_management(system):
    advance(system, skip=True)
    complete(system.coordinator, "ready")
    system.healthy["telegram_account"] = False
    system.connections.refresh("telegram_account")
    assert "management" in system.coordinator.status().disabled_capabilities


async def test_setup_routes_are_sanitized_read_only_live_contracts(system):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from tg_assistant.admin_api.setup import install_setup_routes

    app = FastAPI()
    install_setup_routes(app, lambda: system.coordinator)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        status = await client.get("/api/v1/setup/status")
        assert status.status_code == 200
        assert status.json()["profile"]["owner_id"] is None
        assert status.headers["cache-control"] == "no-store"
        connections = await client.get("/api/v1/connections")
        assert len(connections.json()) == 6
        write = await client.post(
            "/api/v1/setup/status", json={"stage": "ready", "evidence_id": "browser"}
        )
        assert write.status_code == 405
        assert system.coordinator.status().profile.setup_stage == "welcome"
    assert all(
        key not in status.text
        for key in ("otp", "session_id", "raw_ticket", "api_key", "fingerprint")
    )


async def test_setup_routes_hide_storage_errors(system):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from tg_assistant.admin_api.setup import install_setup_routes

    app = FastAPI()

    def unavailable():
        raise RuntimeError("mysql://alice:do-not-echo@host?token=private")

    install_setup_routes(app, unavailable)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for path in ("/api/v1/setup/status", "/api/v1/connections"):
            result = await client.get(path)
            assert result.status_code == 503
            assert result.json()["code"] == "setup_status_unavailable"
            assert "do-not-echo" not in result.text
