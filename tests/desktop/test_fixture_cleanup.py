"""Private short-lived processes only; no worker HTTP, DB or account credentials."""

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace

import psutil
import pytest
from desktop import test_launcher as fixtures

from tg_assistant.paths import current_user_sid


def owned_runtime(tmp_path):
    command = [sys.executable, "-c", "import time; time.sleep(30)", str(tmp_path)]
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return SimpleNamespace(
        process=process,
        fixture_worker_command=tuple(command),
        fixture_owner_sid=current_user_sid(),
        attached_process=(process.pid, psutil.Process(process.pid).create_time()),
    )


def finish(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def test_force_cleanup_waits_for_exact_retained_owned_popen(tmp_path):
    runtime = owned_runtime(tmp_path)
    try:
        assert hasattr(fixtures, "force_fixture_worker_exit"), "Safe owned-handle cleanup is absent"
        assert fixtures.force_fixture_worker_exit(runtime) == []
        assert runtime.process.poll() is not None
    finally:
        finish(runtime.process)


@pytest.mark.parametrize("changed", ["incarnation", "sid", "executable", "command", "parent"])
def test_unverified_child_is_never_killed(tmp_path, monkeypatch, changed):
    runtime = owned_runtime(tmp_path)
    unrelated = owned_runtime(tmp_path).process
    candidate = psutil.Process(unrelated.pid)
    runtime.attached_process = (candidate.pid, candidate.create_time())
    original_owner = fixtures.module("tg_assistant.desktop.instance").process_matches_owner
    try:
        assert hasattr(fixtures, "force_fixture_worker_exit"), "Safe owned-handle cleanup is absent"
        if changed == "incarnation":
            runtime.attached_process = (candidate.pid, candidate.create_time() - 10)
        elif changed == "sid":
            instance = fixtures.module("tg_assistant.desktop.instance")
            monkeypatch.setattr(instance, "process_matches_owner", lambda *_args: False)
        elif changed == "executable":
            original = psutil.Process.exe
            monkeypatch.setattr(
                psutil.Process,
                "exe",
                lambda self: (
                    str(tmp_path / "other.exe") if self.pid == candidate.pid else original(self)
                ),
            )
        elif changed == "command":
            original = psutil.Process.cmdline
            monkeypatch.setattr(
                psutil.Process,
                "cmdline",
                lambda self: (
                    [sys.executable, "-c", "wrong-profile"]
                    if self.pid == candidate.pid
                    else original(self)
                ),
            )
        assert original_owner(candidate.pid, candidate.create_time())
        errors = fixtures.force_fixture_worker_exit(runtime)
        assert errors, "Refused or unknown cleanup must be reported"
        assert runtime.process.poll() is not None, "Refused child must not skip the owned parent"
        assert unrelated.poll() is None, "A separate private process must stay untouched"
    finally:
        finish(runtime.process)
        finish(unrelated)


def test_exact_recorded_worker_child_exits_before_cleanup_returns(tmp_path):
    import time

    marker = tmp_path / "private-child.json"
    # Supply the exact command through a private environment entry to avoid recursive code quoting.
    code = (
        "import os,sys,time,json,subprocess; from pathlib import Path; "
        "\nif os.environ.get('FIXTURE_CHILD') == '1': time.sleep(5)"
        "\nelse:"
        "\n child=subprocess.Popen(json.loads(os.environ['FIXTURE_COMMAND']),"
        "env={**os.environ,'FIXTURE_CHILD':'1'},stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
        "\n staged=Path(sys.argv[1]+'.tmp'); staged.write_text(json.dumps({'pid':child.pid}),encoding='utf-8'); staged.replace(sys.argv[1])"
        "\n time.sleep(30)"
    )
    command = [sys._base_executable, "-c", code, str(marker)]
    parent = subprocess.Popen(
        command,
        env={**os.environ, "FIXTURE_COMMAND": json.dumps(command)},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child = None
    try:
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), "Private child did not report its fixture PID"
        child = psutil.Process(json.loads(marker.read_text())["pid"])
        runtime = SimpleNamespace(
            process=parent,
            fixture_worker_command=tuple(command),
            fixture_owner_sid=current_user_sid(),
            attached_process=(child.pid, child.create_time()),
        )
        assert fixtures.force_fixture_worker_exit(runtime) == []
        assert parent.poll() is not None and not child.is_running()
    finally:
        finish(parent)
        if child is not None:
            # The private child self-expires after 5s; no unverified target is signalled in teardown.
            child.wait(timeout=7)


def test_resume_producer_is_tracked_and_cleanup_drains_it(tmp_path):
    executor = ThreadPoolExecutor(max_workers=1)
    entered, release = threading.Event(), threading.Event()
    runtime = SimpleNamespace(process=None, executor=executor, attached_process=None)

    def produce():
        entered.set()
        assert release.wait(3)
        runtime.process = owned_runtime(tmp_path).process

    runtime.start = lambda: executor.submit(produce)
    runtime.resume_after_handoff = lambda: executor.submit(produce)
    runtime.stop = lambda: finish(runtime.process) if runtime.process else None
    runtime._refresh = lambda: None
    runtime.close = lambda: executor.shutdown(wait=False, cancel_futures=True)
    fixtures.track_fixture_starts(runtime)
    future = runtime.resume_after_handoff()
    try:
        assert entered.wait(1)
        assert future in runtime.fixture_starts, "Resume can create a worker and must be tracked"
        timer = threading.Timer(0.1, release.set)
        timer.start()
        fixtures.stop(runtime)
        timer.join()
        assert future.done() and runtime.process.poll() is not None
    finally:
        release.set()
        executor.shutdown(wait=True, cancel_futures=True)
        if runtime.process:
            finish(runtime.process)


def test_force_cleanup_failure_does_not_become_test_success(tmp_path):
    runtime = owned_runtime(tmp_path)
    runtime.fixture_starts = []
    runtime.stop = lambda: None
    runtime._refresh = lambda: None
    runtime.close = lambda: None
    try:
        assert "timeout" in __import__("inspect").signature(fixtures.stop).parameters
        with pytest.raises(pytest.fail.Exception, match="did not stop"):
            fixtures.stop(runtime, timeout=0.05)
        assert runtime.process.poll() is not None, "Failed cleanup must still reap its owned parent"
    finally:
        finish(runtime.process)


def test_cancelled_queued_resume_cannot_spawn_after_cleanup(tmp_path):
    executor = ThreadPoolExecutor(max_workers=1)
    entered, release = threading.Event(), threading.Event()

    def held():
        entered.set()
        assert release.wait(3)

    blocker = executor.submit(held)
    assert entered.wait(1)
    runtime = SimpleNamespace(process=None, executor=executor)
    spawned = []

    def produce():
        spawned.append(owned_runtime(tmp_path).process)

    runtime.start = lambda: executor.submit(produce)
    runtime.resume_after_handoff = lambda: executor.submit(produce)
    runtime.stop = lambda: None
    runtime.close = lambda: executor.shutdown(wait=False, cancel_futures=True)
    fixtures.track_fixture_starts(runtime)
    queued = runtime.resume_after_handoff()
    try:
        fixtures.stop(runtime)
        assert queued.cancelled()
        assert runtime.resume_after_handoff().cancelled()
        release.set()
        blocker.result(timeout=3)
        assert spawned == []
    finally:
        release.set()
        executor.shutdown(wait=True, cancel_futures=True)
        for process in spawned:
            finish(process)


@pytest.mark.parametrize("missing", [True, False])
def test_child_lookup_failure_still_reaps_original_parent(tmp_path, monkeypatch, missing):
    runtime = owned_runtime(tmp_path)
    other = owned_runtime(tmp_path).process
    candidate = psutil.Process(other.pid)
    runtime.attached_process = (candidate.pid, candidate.create_time())
    original = psutil.Process

    def unavailable(pid):
        if pid == candidate.pid:
            raise psutil.NoSuchProcess(pid) if missing else psutil.AccessDenied(pid)
        return original(pid)

    monkeypatch.setattr(psutil, "Process", unavailable)
    try:
        errors = fixtures.force_fixture_worker_exit(runtime)
        assert bool(errors) is not missing
        assert runtime.process.poll() is not None
        assert other.poll() is None
    finally:
        finish(runtime.process)
        finish(other)


@pytest.mark.parametrize("producer_error", [True, False])
def test_failed_or_late_resume_is_drained_before_cleanup_returns(tmp_path, producer_error):
    import time

    executor = ThreadPoolExecutor(max_workers=1)
    entered = threading.Event()
    runtime = SimpleNamespace(process=None, executor=executor, attached_process=None)

    def produce():
        entered.set()
        time.sleep(0.1)
        runtime.process = owned_runtime(tmp_path).process
        if producer_error:
            raise RuntimeError("synthetic_resume_failed")

    runtime.start = lambda: executor.submit(produce)
    runtime.resume_after_handoff = lambda: executor.submit(produce)
    runtime.stop = lambda: finish(runtime.process) if runtime.process else None
    runtime._refresh = lambda: None
    runtime.close = lambda: executor.shutdown(wait=False, cancel_futures=True)
    fixtures.track_fixture_starts(runtime)
    future = runtime.resume_after_handoff()
    try:
        assert entered.wait(1)
        with pytest.raises(pytest.fail.Exception, match="Fixture cleanup failed"):
            fixtures.stop(runtime, timeout=0.02)
        assert future.done(), "Cleanup cannot return while a producer may still spawn"
        assert runtime.process.poll() is not None
        assert runtime.start().cancelled()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        if runtime.process:
            finish(runtime.process)


def test_concurrent_producer_submission_cannot_escape_cleanup_snapshot():
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)
    runtime = SimpleNamespace(process=None, executor=executor)

    def submit():
        entered.set()
        assert release.wait(3)
        future = Future()
        future.set_result(None)
        return future

    runtime.start = runtime.resume_after_handoff = submit
    runtime.stop = lambda: None
    runtime.close = lambda: executor.shutdown(wait=False, cancel_futures=True)
    fixtures.track_fixture_starts(runtime)
    producer = threading.Thread(target=runtime.resume_after_handoff)
    cleanup = threading.Thread(target=lambda: (fixtures.stop(runtime), finished.set()))
    producer.start()
    try:
        assert entered.wait(1)
        cleanup.start()
        assert not finished.wait(0.05), "Cleanup returned before the submission was recorded"
    finally:
        release.set()
        producer.join(timeout=3)
        if cleanup.ident is not None:
            cleanup.join(timeout=3)
        executor.shutdown(wait=True, cancel_futures=True)
    assert finished.is_set() and len(runtime.fixture_starts) == 1
