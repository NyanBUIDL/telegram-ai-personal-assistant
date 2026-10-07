"""Literal-loopback readiness transport and real response guard regressions."""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import psutil
import pytest

from tg_assistant.config import Settings
from tg_assistant.desktop.runtime_controller import RuntimeController


@pytest.fixture
def probe(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(name)
    settings = Settings(_env_file=None, data_dir=tmp_path / "profile")
    settings.data_dir.joinpath("config").mkdir(parents=True)
    state = {
        "profile_id": settings.profile_id, "pid": os.getpid(),
        "process_started_at": psutil.Process().create_time(), "port": 0,
        "run_id": uuid4().hex, "mode": "setup", "owner_id": None,
    }
    response = {
        "status": 200, "header": state["run_id"], "delay_body": False,
        "body": {
            "service": "runtime", "state": "ready", "checked_at": datetime.now(UTC).isoformat(),
            "code": "runtime_ready", "message": "Synthetic readiness",
            "next_action": None, "capabilities": ["setup"],
        },
        "requests": [],
        "hold_peer": False, "peer_result": None, "peer_observed": threading.Event(),
    }
    release_body = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            response["requests"].append(self.path)
            self.send_response(response["status"])
            self.send_header("X-TG-Runtime-ID", response["header"])
            if response["status"] == 302:
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            if response["delay_body"]:
                release_body.wait(3)
            body = response["body"]
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            if response["hold_peer"]:
                self.connection.settimeout(1)
                try:
                    response["peer_result"] = "closed" if self.rfile.read(1) == b"" else "unexpected"
                except ConnectionResetError:
                    response["peer_result"] = "closed"
                except TimeoutError:
                    response["peer_result"] = "pending"
                response["peer_observed"].set()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["port"] = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime = RuntimeController(settings)
    runtime.state_file.write_text(json.dumps(state), encoding="utf-8")
    try:
        yield runtime, state, response
    finally:
        release_body.set()
        runtime.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_literal_http_readiness_never_constructs_tls_context(probe, monkeypatch):
    runtime, _, response = probe

    def forbidden(*args, **kwargs):
        raise AssertionError("readiness_probe_constructed_tls_context")

    monkeypatch.setattr(ssl, "create_default_context", forbidden)
    assert runtime._refresh().phase == "ready"
    assert response["requests"] == ["/api/v1/runtime/readiness"]


@pytest.mark.parametrize("body_size", [8192, 8193])
def test_readiness_response_body_is_bounded(probe, body_size):
    runtime, _, response = probe
    body = json.dumps(response["body"]).encode()
    response["body"] = body + b" " * (body_size - len(body))
    measured = runtime._refresh()
    assert (measured.phase == "ready") is (body_size == 8192)


def test_oversized_response_closes_peer_with_retained_exception(probe):
    from tg_assistant.desktop.runtime_controller import _readiness_response

    _, state, response = probe
    body = json.dumps(response["body"]).encode()
    response["body"] = body + b" " * (8193 - len(body))
    response["hold_peer"] = True
    with pytest.raises(ValueError) as retained:
        _readiness_response(state["port"])
    assert retained.value.args == ("runtime_readiness_body_oversized",)
    assert response["peer_observed"].wait(2)
    assert response["peer_result"] == "closed", "Rejected response retained its actual peer socket"


@pytest.mark.parametrize("status", [201, 302, 403, 500])
def test_readiness_requires_exact_success_without_redirect(probe, status):
    runtime, _, response = probe
    response["status"] = status
    assert runtime._refresh().phase != "ready"
    assert response["requests"] == ["/api/v1/runtime/readiness"]


@pytest.mark.parametrize("change", [
    {"service": "storage"}, {"state": "unknown"}, {"capabilities": ["management"]},
    {"checked_at": None}, {"checked_at": "malformed"}, {"code": 42},
])
def test_readiness_retains_public_status_guards(probe, change):
    runtime, _, response = probe
    response["body"].update(change)
    assert runtime._refresh().phase != "ready"


@pytest.mark.parametrize("offset", [-60, 60])
def test_readiness_rejects_stale_and_future_measurements(probe, offset):
    runtime, _, response = probe
    response["body"]["checked_at"] = (datetime.now(UTC) + timedelta(seconds=offset)).isoformat()
    assert runtime._refresh().phase != "ready"


def test_readiness_rejects_wrong_runtime_header(probe):
    runtime, _, response = probe
    response["header"] = uuid4().hex
    assert runtime._refresh().phase != "ready"


def test_readiness_rejects_malformed_body_without_echo(probe):
    runtime, _, response = probe
    response["body"] = b"PRIVATE_RESPONSE_CANARY"
    measured = runtime._refresh()
    assert measured.phase != "ready"
    assert "PRIVATE_RESPONSE_CANARY" not in repr(measured)


def test_readiness_slow_body_remains_unavailable(probe):
    runtime, _, response = probe
    response["delay_body"] = True
    assert runtime.refresh().result(timeout=2).phase != "ready"


@pytest.mark.parametrize("change", [
    {"profile_id": "foreign-profile"}, {"pid": 999999999}, {"process_started_at": 1.0},
])
def test_unowned_state_is_rejected_before_http(probe, change):
    runtime, state, response = probe
    runtime.state_file.write_text(json.dumps({**state, **change}), encoding="utf-8")
    assert runtime._refresh().phase != "ready"
    assert response["requests"] == []


@pytest.mark.parametrize("port", [True, False, 0, 1023, 65536, "18765", 18765.0])
def test_invalid_probe_port_never_opens_a_connection(monkeypatch, port):
    from tg_assistant.desktop import runtime_controller

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid_port_opened_connection")

    monkeypatch.setattr(runtime_controller, "HTTPConnection", forbidden)
    with pytest.raises(ValueError, match="runtime_readiness_port_invalid"):
        runtime_controller._readiness_response(port)


def test_passive_fixture_observation_preserves_real_probe_and_hides_body(probe):
    from desktop.test_launcher import observe_fixture_readiness

    runtime, _, response = probe
    response["body"]["message"] = "PRIVATE_BODY_CANARY"
    observe_fixture_readiness(runtime)
    assert runtime._refresh().phase == "ready"
    assert response["requests"] == ["/api/v1/runtime/readiness"]
    assert runtime.fixture_diagnostics["http"] == "response"
    assert runtime.fixture_diagnostics["header_matches"] is True
    assert runtime.fixture_diagnostics["body"] == "valid"
    assert "PRIVATE_BODY_CANARY" not in repr(runtime.fixture_diagnostics)


@pytest.mark.parametrize("phase", ["migration_started", "PRIVATE_PHASE_CANARY"])
def test_bootstrap_projection_does_not_echo_private_values(probe, phase):
    from desktop.test_launcher import fixture_bootstrap_diagnostics

    runtime, state, _ = probe
    runtime.launch_id = state["run_id"]
    runtime.fixture_phase_file = runtime.settings.data_dir.parent / "synthetic-worker-phases.json"
    runtime.fixture_phase_file.write_text(json.dumps({
        "run_fingerprint": hashlib.sha256(runtime.launch_id.encode()).hexdigest(),
        "terminal": "running", "steps": [{"phase": phase, "elapsed_ms": 12}],
        "private": "PRIVATE_PHASE_CANARY",
    }), encoding="utf-8")
    measured = fixture_bootstrap_diagnostics(runtime)
    assert measured["bootstrap"] == ("observed" if phase == "migration_started" else "invalid")
    assert "PRIVATE_PHASE_CANARY" not in repr(measured)


def test_bootstrap_projection_rejects_a_previous_launch(probe):
    from desktop.test_launcher import fixture_bootstrap_diagnostics

    runtime, state, _ = probe
    runtime.launch_id = state["run_id"]
    runtime.fixture_phase_file = runtime.settings.data_dir.parent / "synthetic-worker-phases.json"
    runtime.fixture_phase_file.write_text(json.dumps({
        "run_fingerprint": hashlib.sha256(uuid4().hex.encode()).hexdigest(),
        "terminal": "running", "steps": [{"phase": "state_published", "elapsed_ms": 12}],
    }), encoding="utf-8")
    assert fixture_bootstrap_diagnostics(runtime) == {
        "bootstrap": "stale", "bootstrap_run_matches": False,
    }


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows diagnostic replacement denial")
def test_passive_diagnostic_denial_does_not_interrupt_original_work(probe):
    from desktop import test_launcher
    from fixtures.launcher_worker import publish_diagnostic

    runtime, state, _ = probe
    runtime.launch_id = state["run_id"]
    path = runtime.settings.data_dir.parent / "synthetic-worker-phases.json"
    runtime.fixture_phase_file = path
    facts = {
        "run_fingerprint": hashlib.sha256(runtime.launch_id.encode()).hexdigest(),
        "terminal": "running", "steps": [{"phase": "migration_started", "elapsed_ms": 12}],
    }
    original = json.dumps(facts).encode()
    path.write_bytes(original)
    # A genuine built-in read handle reliably denies replacement on Windows.
    # Passive instrumentation must still allow the original owning work.
    with path.open("rb"):
        assert publish_diagnostic(path, facts) is False
        assert runtime._refresh().phase == "ready"
        assert path.read_bytes() == original
        assert not list(path.parent.glob(".synthetic-phases-*.tmp"))
    assert publish_diagnostic(path, facts) is True
    assert test_launcher.fixture_bootstrap_diagnostics(runtime)["bootstrap"] == "observed"
