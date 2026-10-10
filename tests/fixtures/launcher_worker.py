"""Owned disposable launcher worker with fixed-code private bootstrap timings."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


def publish_diagnostic(diagnostic, facts):
    temporary = diagnostic.with_name(".synthetic-phases-" + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(facts, stream)
        temporary.replace(diagnostic)
        return True
    except OSError:
        # Passive diagnostics must never change worker lifecycle.
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def main():
    # Controlled checkout helper, no pytest or human credential backend.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from windows_fixture_owner import prepare_windows_fixture_owner

    prepare_windows_fixture_owner()  # Before any diagnostic/profile resource.
    began = time.monotonic()
    diagnostic = Path(sys.argv[1]).parent / "synthetic-worker-phases.json"
    lock = threading.Lock()
    facts = {
        "run_fingerprint": hashlib.sha256(
            os.environ.get("TG_ASSISTANT_DESKTOP_RUN_ID", "").encode()
        ).hexdigest(),
        "steps": [], "terminal": "running",
    }

    def mark(phase=None, *, terminal=None):
        with lock:
            if phase is not None:
                facts["steps"].append({
                    "phase": phase, "elapsed_ms": round((time.monotonic() - began) * 1000),
                })
                facts["steps"] = facts["steps"][-32:]
            if terminal is not None:
                facts["terminal"] = terminal
            publish_diagnostic(diagnostic, facts)

    def timed(original, start, completed):
        def call(*args, **kwargs):
            mark(start)
            result = original(*args, **kwargs)
            mark(completed)
            return result
        return call

    try:
        mark("imports_started")
        from tg_assistant.config import Settings
        from tg_assistant.desktop import setup_context, worker
        from tg_assistant.services.onboarding import OnboardingCoordinator

        mark("imports_ready")
        worker.ensure_runtime_dirs = timed(
            worker.ensure_runtime_dirs, "directories_started", "directories_ready",
        )
        worker.secure_tree = timed(worker.secure_tree, "security_started", "security_ready")
        worker.StorageService.open = timed(
            worker.StorageService.open, "storage_open_started", "storage_open_ready",
        )
        worker.StorageService.migrate = timed(
            worker.StorageService.migrate, "migration_started", "migration_ready",
        )
        setup_context.SetupContext.prepare_health = timed(
            setup_context.SetupContext.prepare_health, "health_started", "health_ready",
        )
        OnboardingCoordinator.resume = timed(
            OnboardingCoordinator.resume, "resume_started", "resume_ready",
        )
        worker.write_state = timed(worker.write_state, "state_write_started", "state_published")
        original_context = setup_context.open_setup_context

        @contextmanager
        def context(*args, **kwargs):
            mark("context_started")
            with original_context(*args, **kwargs) as value:
                mark("context_ready")
                yield value

        setup_context.open_setup_context = context
        original_serve = worker._serve

        async def serve(*args, **kwargs):
            mark("server_starting")
            return await original_serve(*args, **kwargs)

        worker._serve = serve
        worker.serve_worker(
            Settings(_env_file=None, data_dir=Path(sys.argv[1]), admin_api_port=int(sys.argv[3])),
            instance_directory=Path(sys.argv[2]),
        )
        mark(terminal="stopped")
    except Exception:
        mark(terminal="worker_bootstrap_failed")
        raise


if __name__ == "__main__":
    main()
