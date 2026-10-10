"""Synthetic authorization; actual profile fence; no GUI/IPC success claims."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tg_assistant.contracts import NativeCommand
from tg_assistant.paths import ensure_runtime_dirs
from tg_assistant.services.maintenance import MaintenanceService
from tg_assistant.services.native_dialogs import NativeDialogRelay

NAMES = ("open_connection_dialog", "open_telegram_login", "open_bot_dialog")


@pytest.fixture
def context(tmp_path):
    paths = ensure_runtime_dirs(tmp_path / "profile", profile_id="relay-test")
    fence = MaintenanceService(paths["config"], profile_id="relay-test")
    state = SimpleNamespace(sid="synthetic-current-sid", now=100.0, authorized=True)
    relay = NativeDialogRelay(
        profile_id="relay-test",
        fence=fence,
        sid_getter=lambda: state.sid,
        clock=lambda: state.now,
    )
    try:
        yield relay, state, fence
    finally:
        relay.close()
        fence.close()


def command(name=NAMES[0], **updates):
    return NativeCommand.model_validate(
        {
            "name": name,
            "request_id": uuid4().hex,
            "profile_id": "relay-test",
            "payload_nonsecret": {},
            **updates,
        }
    )


def submit(relay, state, selected=None):
    return relay.submit(selected or command(), authorized=lambda: state.authorized)


def test_no_live_native_handler_cannot_queue(context):
    relay, state, _ = context
    result = submit(relay, state)
    assert result.state == "failed" and result.code == "native_dialog_unavailable"
    assert relay.claim(()) is None


def test_authenticated_command_claims_once_without_success_proof(context):
    relay, state, _ = context
    assert relay.claim(NAMES) is None
    selected = command()
    result = submit(relay, state, selected)
    assert result.state == "queued" and result.progress is None
    assert relay.claim(NAMES) == selected
    assert relay.claim(NAMES) is None
    duplicate = submit(relay, state, selected)
    assert duplicate.state == "failed" and duplicate.code == "native_request_replayed"


@pytest.mark.parametrize("change", ["session", "heartbeat", "command", "sid"])
def test_expiry_or_authority_loss_withdraws_queued_dialog(context, change):
    relay, state, _ = context
    relay.claim(NAMES)
    assert submit(relay, state).state == "queued"
    if change == "session":
        state.authorized = False
    elif change == "heartbeat":
        state.now += 5
        assert submit(relay, state).code == "native_dialog_unavailable"
        return
    elif change == "command":
        state.now += 30
    else:
        state.sid = "synthetic-other-sid"
    assert relay.claim(NAMES) is None


def test_handler_removed_before_claim_is_not_opened(context):
    relay, state, _ = context
    relay.claim(NAMES)
    assert submit(relay, state, command(NAMES[1])).state == "queued"
    assert relay.claim((NAMES[0],)) is None
    assert submit(relay, state, command(NAMES[1])).code == "native_dialog_unavailable"


@pytest.mark.parametrize(
    "selected",
    [
        command("issue_dashboard_ticket"),
        command("runtime_stop"),
        command(profile_id="another-profile"),
        command(payload_nonsecret={"program": "anything"}),
    ],
)
def test_profile_payload_and_command_allowlist(context, selected):
    relay, state, _ = context
    relay.claim(NAMES)
    result = submit(relay, state, selected)
    assert result.state == "failed" and result.code == "native_command_denied"
    assert relay.claim(NAMES) is None


def test_mutated_nested_payload_revalidated_without_input_echo(context):
    relay, state, _ = context
    relay.claim(NAMES)
    selected = command()
    selected.payload_nonsecret["api_key"] = "PRIVATE-CANARY-REJECTED"
    result = submit(relay, state, selected)
    assert result.state == "failed" and result.code == "native_command_denied"
    assert "PRIVATE-CANARY" not in result.model_dump_json()


def test_authorization_exception_is_sanitized(context):
    relay, state, _ = context
    relay.claim(NAMES)

    def unavailable():
        raise ValueError("PRIVATE-AUTHORIZATION-CANARY")

    result = relay.submit(command(), authorized=unavailable)
    assert result.state == "failed" and result.code == "native_command_denied"
    assert "CANARY" not in result.model_dump_json()


def test_queue_is_bounded_and_concurrent_claims_are_atomic(context):
    relay, state, _ = context
    relay.claim(NAMES)
    queued = [command() for _ in range(8)]
    assert all(submit(relay, state, item).state == "queued" for item in queued)
    assert submit(relay, state).code == "native_dialog_busy"
    with ThreadPoolExecutor(max_workers=4) as executor:
        claimed = list(executor.map(lambda _: relay.claim(NAMES), range(16)))
    ids = [item.request_id for item in claimed if item]
    assert len(ids) == len(set(ids)) == 8


def test_actual_maintenance_drain_blocks_submission_and_claim(context):
    relay, state, fence = context
    relay.claim(NAMES)
    assert submit(relay, state).state == "queued"
    lease = fence.acquire("test-drain", timeout=0)
    try:
        assert submit(relay, state).code == "native_dialog_unavailable"
        assert relay.claim(NAMES) is None
    finally:
        fence.release(lease)


def test_shutdown_drops_pending_and_disables_submission(context):
    relay, state, _ = context
    relay.claim(NAMES)
    assert submit(relay, state).state == "queued"
    relay.close()
    assert relay.claim(NAMES) is None
    assert submit(relay, state).code == "native_dialog_unavailable"


def test_claim_rechecks_deadline_after_issuing_session_callback(context):
    relay, state, _ = context
    relay.claim(NAMES)
    calls = []

    def session_check():
        calls.append(True)
        if len(calls) > 1:
            state.now += 30
        return True

    assert relay.submit(command(), authorized=session_check).state == "queued"
    assert relay.claim(NAMES) is None


def test_spent_requests_never_evicted_before_expiry(context):
    relay, state, _ = context
    relay.claim(NAMES)
    oldest = command()
    for item in [oldest] + [command() for _ in range(255)]:
        assert submit(relay, state, item).state == "queued"
        assert relay.claim(NAMES).request_id == item.request_id
    assert submit(relay, state, oldest).code == "native_request_replayed"
    assert submit(relay, state).code == "native_dialog_busy"
    state.now += 300
    relay.claim(NAMES)
    assert submit(relay, state).state == "queued"


def test_denied_candidate_does_not_discard_existing_authorized_request(context):
    relay, state, _ = context
    relay.claim(NAMES)
    accepted = command()
    assert submit(relay, state, accepted).state == "queued"
    invalid = command()
    invalid.payload_nonsecret["otp"] = "PRIVATE-TEMPORARY-CODE"
    assert submit(relay, state, invalid).code == "native_command_denied"
    assert relay.claim(NAMES) == accepted


@pytest.mark.parametrize("bad_clock", [False, float("nan"), 99.0])
def test_invalid_or_regressed_clock_withdraws_commands(context, bad_clock):
    relay, state, _ = context
    relay.claim(NAMES)
    assert submit(relay, state).state == "queued"
    state.now = bad_clock
    assert relay.claim(NAMES) is None
    assert submit(relay, state).code == "native_dialog_unavailable"


def test_public_availability_requires_current_native_heartbeat(context):
    relay, state, _ = context
    assert relay.available_commands() == ()
    relay.claim((NAMES[0],))
    assert relay.available_commands() == (NAMES[0],)
    state.now += 5
    assert relay.available_commands() == ()


def test_availability_does_not_consume_or_renew_requests(context):
    relay, state, _ = context
    relay.claim(NAMES)
    selected = command()
    assert submit(relay, state, selected).state == "queued"
    for _ in range(3):
        assert relay.available_commands() == tuple(sorted(NAMES))
    assert relay.claim(NAMES) == selected
    state.now += 5
    assert relay.available_commands() == ()
