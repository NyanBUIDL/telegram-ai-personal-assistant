"""Boundary tests for the shared Windows public-beta contracts.

These catch unsafe ID coercion, secret-bearing bridge requests, accidental
management authority, and drift between the Python and browser wire formats.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

ROOT = Path(__file__).resolve().parents[1]
NODE = Path(os.environ.get("CONTRACTS_NODE") or shutil.which("node") or "C:/Users/brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe")


@pytest.fixture
def contracts():
    # Keep the initial missing-contract red run an assertion, not a collection error.
    try:
        return importlib.import_module("tg_assistant.contracts")
    except ModuleNotFoundError:
        class MissingContracts:
            def __getattr__(self, name):
                pytest.fail("B00 shared contracts have not been implemented")
        return MissingContracts()


def profile(**changes):
    return {
        "profile_id": "local-profile", "owner_id": None, "storage_backend": "sqlite",
        "setup_stage": "welcome", "version": 1, **changes,
    }


def command(**changes):
    return {
        "name": "open_connection_dialog", "request_id": "request-1",
        "profile_id": "local-profile", "payload_nonsecret": {"provider": "ollama"},
        **changes,
    }


def test_owner_id_retains_precision_as_decimal_json_string(contracts):
    value = contracts.PublicProfile(**profile(owner_id=9007199254740993))
    assert value.owner_id == 9007199254740993
    assert json.loads(value.model_dump_json())["owner_id"] == "9007199254740993"
    assert contracts.PublicProfile.model_validate_json(value.model_dump_json()).owner_id == value.owner_id


@pytest.mark.parametrize("owner_id", [True, 1.0, 0, -1, "01", "+1", "1e3", " 1 "])
def test_owner_id_rejects_lossy_or_noncanonical_values(contracts, owner_id):
    with pytest.raises(ValidationError):
        contracts.PublicProfile(**profile(owner_id=owner_id))


def test_signed_chat_ids_use_decimal_strings(contracts):
    value = contracts.RevocationReport(chat_id=-1009007199254740993, authorization_epoch=2,
                                       cancelled_jobs=0, memory_action="retain", operation_id=None)
    assert json.loads(value.model_dump_json())["chat_id"] == "-1009007199254740993"


@pytest.mark.parametrize("owner_id", [1, 9007199254740993])
def test_python_json_boundary_requires_string_ids(contracts, owner_id):
    with pytest.raises(ValidationError):
        contracts.PublicProfile.model_validate_json(json.dumps(profile(owner_id=owner_id)))


def test_setup_has_no_fabricated_owner_or_management_authority(contracts):
    value = contracts.PublicProfile(**profile())
    assert json.loads(value.model_dump_json())["owner_id"] is None
    session = contracts.AdminSession(session_id="opaque-session", profile_id="local-profile",
                                     owner_id=None, authority="setup_only",
                                     expires_at=datetime.now(UTC) + timedelta(seconds=30))
    assert session.owner_id is None
    with pytest.raises(ValidationError):
        contracts.AdminSession(**{**session.model_dump(), "authority": "management"})
    with pytest.raises(ValidationError):
        contracts.PublicProfile(**profile(setup_stage="owner_paired"))


@pytest.mark.parametrize("extra", [{"api_key": "hidden"}, {"bot_token": "hidden"}, {"unexpected": 1}])
def test_public_dtos_reject_secret_and_unknown_fields(contracts, extra):
    with pytest.raises(ValidationError):
        contracts.PublicProfile(**{**profile(), **extra})


@pytest.mark.parametrize("payload", [
    {"api_key": "hidden"}, {"nested": [{"BotToken": "hidden"}]},
    {"api-hash": "hidden"}, {"OTP": "12345"}, {"two_factor_password": "hidden"},
    {"credential": "hidden"}, {"authorization": "hidden"}, {"raw_ticket": "hidden"},
    {"provider": "https://user:password@example.test"},
    {"provider": "https://example.test?api_key=hidden"},
    {"provider": "https://example.test/#token=hidden"},
    {"path": "C:/arbitrary.exe"}, {"executable": "cmd.exe"}, {"url": "https://example.test"},
    {"target_path": "C:/arbitrary.exe"}, {"callback_url": "https://example.test"},
    {"shell_command": "echo unsafe"},
])
def test_native_bridge_rejects_recursive_secrets_and_arbitrary_targets(contracts, payload):
    with pytest.raises(ValidationError):
        contracts.NativeCommand(**command(payload_nonsecret=payload))


def test_native_bridge_is_allowlisted_bounded_and_json_only(contracts):
    assert contracts.NativeCommand(**command()).name == "open_connection_dialog"
    for changes in [{"name": "run_shell"}, {"payload_nonsecret": {"provider": "x" * 9000}},
                    {"payload_nonsecret": {"value": float("nan")}},
                    {"payload_nonsecret": {"value": object()}}]:
        with pytest.raises(ValidationError):
            contracts.NativeCommand(**command(**changes))


def test_mutated_native_payload_cannot_serialize_new_secret_fields(contracts):
    value = contracts.NativeCommand(**command(payload_nonsecret={"options": {"provider": "ollama"}}))
    value.payload_nonsecret["options"]["api_key"] = "synthetic-sensitive-marker"
    with pytest.raises(PydanticSerializationError):
        value.model_dump_json()


def test_validation_error_display_does_not_echo_rejected_payload(contracts):
    with pytest.raises(ValidationError) as caught:
        contracts.NativeCommand(**command(payload_nonsecret={"api_key": "synthetic-sensitive-marker"}))
    assert "synthetic-sensitive-marker" not in str(caught.value)


@pytest.mark.parametrize("dump_mode", ["python", "json"])
@pytest.mark.parametrize("mutation", [
    "connections", "nested_capabilities", "disabled_capabilities", "stage_evidence_value",
    "stage_evidence_key", "checksum_value", "checksum_key",
])
def test_mutated_public_collections_fail_closed_without_input_diagnostics(contracts, dump_mode, mutation):
    marker = "synthetic-sensitive-marker"
    injected = {"api_key": marker}
    connection = contracts.ConnectionStatus(service="storage", state="ready", checked_at=None,
                                            code="ok", message="Sẵn sàng", next_action=None,
                                            capabilities=["read"])
    status = contracts.OnboardingStatus(profile=contracts.PublicProfile(**profile()),
                                        connections=[connection], stage_evidence_ids={"welcome": "evidence-1"},
                                        next_action=None, disabled_capabilities=["management"])
    manifest = contracts.BackupManifest(format_version=1, schema_revision="initial", backend="sqlite",
                                        profile_id="local-profile", checksums={"database": "a" * 64},
                                        vector_state="rebuild_required")
    value = status
    if mutation == "connections":
        status.connections.append(injected)
    elif mutation == "nested_capabilities":
        status.connections[0].capabilities.append(injected)
    elif mutation == "disabled_capabilities":
        status.disabled_capabilities.append(injected)
    elif mutation == "stage_evidence_value":
        status.stage_evidence_ids[contracts.OnboardingStage.WELCOME] = injected
    elif mutation == "stage_evidence_key":
        status.stage_evidence_ids["api_key"] = marker
    elif mutation == "checksum_value":
        value = manifest
        manifest.checksums["database"] = injected
    elif mutation == "checksum_key":
        value = manifest
        manifest.checksums["api_key"] = marker
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        with pytest.raises(PydanticSerializationError) as caught:
            if dump_mode == "json":
                value.model_dump_json()
            else:
                value.model_dump()
    assert marker not in str(caught.value)
    assert marker not in repr(caught.value)
    assert captured == [], "No input-bearing serializer warnings may escape validation"


@pytest.mark.parametrize("dump_mode", ["python", "json"])
def test_valid_public_collection_updates_preserve_wire_types(contracts, dump_mode):
    value = contracts.OnboardingStatus(profile=contracts.PublicProfile(**profile(owner_id=9007199254740993)),
                                       connections=[], stage_evidence_ids={}, next_action=None,
                                       disabled_capabilities=[])
    value.connections.append(contracts.ConnectionStatus(service="storage", state="ready", checked_at=None,
                                                        code="ok", message="Sẵn sàng", next_action=None,
                                                        capabilities=["read"]))
    value.stage_evidence_ids[contracts.OnboardingStage.WELCOME] = "evidence-1"
    if dump_mode == "json":
        result = json.loads(value.model_dump_json())
        assert result["profile"]["owner_id"] == "9007199254740993"
    else:
        result = value.model_dump()
        assert result["profile"]["owner_id"] == 9007199254740993
    assert result["connections"][0]["capabilities"] == ["read"]
    assert result["stage_evidence_ids"] == {"welcome": "evidence-1"}


@pytest.mark.parametrize("endpoint", ["https://user:secret@example.test", "https://example.test", "endpoint?token=hidden", "endpoint#secret", " user "])
def test_embedding_endpoint_is_an_identifier_not_a_url(contracts, endpoint):
    with pytest.raises(ValidationError):
        contracts.EmbeddingProfile(provider="ollama", endpoint_id=endpoint, model="model",
                                   embedding_version="v1", dimension=768, store_id="store-1",
                                   cloud_consent=False)


def test_status_normalizes_utc_and_rejects_naive_time(contracts):
    fields = dict(service="storage", state="ready", code="ok", message="Sẵn sàng",
                  next_action=None, capabilities=["read"])
    value = contracts.ConnectionStatus(**fields, checked_at="2026-10-02T14:00:00+07:00")
    assert json.loads(value.model_dump_json())["checked_at"] == "2026-10-02T07:00:00Z"
    with pytest.raises(ValidationError):
        contracts.ConnectionStatus(**fields, checked_at=datetime(2026, 10, 2))


@pytest.mark.parametrize("progress", [-1, 101, True, 1.5, float("inf")])
def test_operation_progress_is_nullable_integer_percentage(contracts, progress):
    with pytest.raises(ValidationError):
        contracts.OperationResult(operation_id="operation-1", state="running", progress=progress,
                                  code="sync", message="Đang đồng bộ", next_action=None)


def test_public_schema_excludes_internal_credentials_and_leases(contracts):
    schema = contracts.public_contract_schema()
    assert set(schema["contracts"]) == {
        "ConnectionStatus", "PublicProfile", "EmbeddingProfile", "OperationResult", "NativeCommand",
        "OnboardingStatus", "RecoveryPlan", "RevocationReport", "MigrationReport", "BackupManifest",
    }
    forbidden = {"raw_ticket", "claim_token", "session_id", "api_key", "bot_token", "api_hash", "otp", "password"}

    def inspect(node):
        if isinstance(node, dict):
            assert forbidden.isdisjoint(node.get("properties", {}))
            for child in node.values():
                inspect(child)
        elif isinstance(node, list):
            for child in node:
                inspect(child)
    inspect(schema)


def run_browser(cases):
    if not NODE.is_file():
        pytest.skip("Node unavailable: set CONTRACTS_NODE to run cross-runtime contract checks")
    module = (ROOT / "dashboard-prototype/src/contracts/generated.js").as_uri()
    script = f"""import {{ contractSchema, validateContract }} from {json.dumps(module)};
let input = ''; for await (const chunk of process.stdin) input += chunk;
const cases = JSON.parse(input);
console.log(JSON.stringify({{schema: contractSchema, results: cases.map(([name, value]) => validateContract(name, value))}}));"""
    result = subprocess.run([str(NODE), "--input-type=module", "-e", script], input=json.dumps(cases),
                            capture_output=True, text=True, check=True, cwd=ROOT)
    return json.loads(result.stdout)


def test_browser_schema_matches_python_and_enforces_wire_boundaries(contracts):
    cases = [
        ("PublicProfile", profile(owner_id="9007199254740993")),
        ("PublicProfile", profile()),
        ("PublicProfile", profile(owner_id=9007199254740993)),
        ("PublicProfile", profile(owner_id="01")),
        ("PublicProfile", profile(owner_id="-1")),
        ("PublicProfile", profile(owner_id="1\n")),
        ("PublicProfile", profile(setup_stage="owner_paired")),
        ("PublicProfile", {**profile(), "bot_token": "hidden"}),
        ("NativeCommand", command(payload_nonsecret={"nested": [{"apiKey": "hidden"}]})),
        ("NativeCommand", command(name="run_shell")),
        ("NativeCommand", command()),
        ("NativeCommand", command(payload_nonsecret={"provider": "https://example.test?token=hidden"})),
        ("NativeCommand", command(payload_nonsecret={"provider": "x" * 9000})),
        ("AdminSession", {"session_id": "hidden"}),
    ]
    result = run_browser(cases)
    assert result["schema"] == contracts.public_contract_schema()
    assert result["results"] == [True, True, False, False, False, False, False, False, False, False, True, False, False, False]


def test_browser_accepts_serialized_public_dtos_and_rejects_invalid_state(contracts):
    values = {
        "ConnectionStatus": dict(service="chat_ai", state="unknown", checked_at=None, code="not_checked",
                                 message="Chưa kiểm tra", next_action="open_connection_dialog", capabilities=[]),
        "EmbeddingProfile": dict(provider="ollama", endpoint_id="local-ollama", model="model", embedding_version="v1",
                                 dimension=768, store_id="store-1", cloud_consent=False),
        "OperationResult": dict(operation_id="op-1", state="uncertain", progress=None, code="reconcile",
                                message="Kiểm tra lại", next_action="review"),
        "OnboardingStatus": dict(profile=profile(), connections=[], stage_evidence_ids={},
                                 next_action="open_connection_dialog", disabled_capabilities=["management"]),
        "RecoveryPlan": dict(plan_id="plan-1", chat_id=-1009007199254740993, store_id="store-1",
                             authorization_epoch=0, index_generation=1, expected_count=2, expires_at="2026-10-02T07:00:00Z"),
        "RevocationReport": dict(chat_id=-100123, authorization_epoch=1, cancelled_jobs=2,
                                 memory_action="retain", operation_id=None),
        "MigrationReport": dict(previous_revision=None, current_revision="initial", changed=True,
                                code="ok", next_action=None),
        "BackupManifest": dict(format_version=1, schema_revision="initial", backend="sqlite",
                               profile_id="local-profile", checksums={"database": "a" * 64}, vector_state="rebuild_required"),
    }
    cases = [(name, json.loads(getattr(contracts, name)(**value).model_dump_json())) for name, value in values.items()]
    cases.extend([
        ("OperationResult", {**cases[2][1], "state": "done"}),
        ("OperationResult", {**cases[2][1], "progress": 1.5}),
        ("ConnectionStatus", {**cases[0][1], "checked_at": "not-a-date"}),
        ("OnboardingStatus", {**cases[3][1], "stage_evidence_ids": {"made_up": "evidence-1"}}),
    ])
    assert run_browser(cases)["results"] == [True] * len(values) + [False] * 4
