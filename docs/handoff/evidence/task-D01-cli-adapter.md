# D01 CLI producer increment

Coordinator authorized only `src/tg_assistant/cli.py` and new `tests/desktop/test_cli_launcher.py`. Root owns runtime/worker integration and independently reviews this increment. Earlier primitive review approval does not apply to this agent's CLI implementation.

Normal Windows start/stop/status now consume the reviewed RuntimeController's actual fresh readiness and worker incarnation. Start waits up to20seconds and shows only measured PID/actual loopback port; unknown, queued, stale or error states have fixed sanitized messages without assumed configured-port success. Stop adopts a validated worker, requests graceful shutdown and waits boundedly for process completion; it refuses unconfirmed state and never forcekills. Status reports measured phase/code and exposes URL only at ready. A timeout can leave a worker finishing startup in the background; status confirms it later.

The existing live legacy PID guard remains before migration/setup, labeled readiness unchecked. Malformed legacy markers no longer mask a healthy native runtime. Normal non-Windows background behavior remains available without optional Qt; advanced foreground run/worker remain available and now acquire the same global per-SID InstanceGuard, then S02's owned byte-zero FileLock, before credentials, migrations or network. This replaces append-offset byte locking and retains explicit per-profile lock ownership/cleanup. Invalid profile ownership is refused with sanitized text and original marker/data unchanged.

## Actual test evidence

Runtime: installed `.venv-q01/Scripts/python.exe`, Windows, QT_QPA_PLATFORM=offscreen and PYTHONUTF8=1. All native cases use explicit disposable profile/settings and an injected synthetic control directory, actual Typer CLI calls, actual subprocess workers and sockets. Foreground setup/SecretStore calls are forbidden fixtures. No real profile, secret store contents, account, Telegram or paid/cloud provider call.

| Check | Observed result |
| --- | --- |
| Initial new CLI tests before implementation |7failed, exit1 (`.test-temp/d01/cli-red.log`): native flow reached interactive setup, status missed real worker, per-SID guard bypass and append-offset lock bypass |
| Initial minimal implementation + required old live-PID control |8passed, exit0 (`cli-green.log`) |
| Added queued/config refusal negatives |4failed, exit1 (`cli-config-red.log`): queued Exit misclassified as failed, three ownership errors lacked sanitized output |
| Corrected error handling |11passed, exit0 (`cli-final-green.log`) |
| Added stale legacy-PID/native coexistence regression |1failed, exit1 (`cli-stale-pid-red.log`): stale marker hid measured native URL |
| Final new CLI module + required early legacy PID test |12passed,0skipped, exit0 (`cli-validated-green.log`) |
| Ruff check and format check, both owned files |Pass |

Final command from repository root:

```text
.venv-q01/Scripts/python.exe -m pytest tests/desktop/test_cli_launcher.py tests/integration/test_job_concurrency.py::test_start_running_instance_skips_migration_and_setup -q --basetemp=.test-temp/d01/cli-validated-green
```

The11new cases cover first-run SQLite without foreground credentials, actual fallback port under collision, detached status/stop adoption, sanitized spawn failure, bounded queued startup without success/failure fabrication, stale refusal without stop request, foreground singleton guard before setup, existing byte-zero lock contention, start/stop/status foreign-profile refusal, and malformed legacy marker with a real healthy native runtime. The required old integration control remains unchanged. Fixture cleanup uses only its validated incarnation and exact owned child completion, without forced process termination.

Frozen review source and hashes live under `.superpowers/sdd/windows-public-beta-2026-10-02/D01-cli-review-snapshot/`. No source outside the two assigned files was edited, and no Git action, full-suite repeat, remote CI or external release was performed. Paired runtime/CLI gateway integration, D02 ticket authority, whole-branch regression, packaging and interactive Windows DPI/VM evidence remain outside this local increment's claim. Compatibility-only legacy PID markers do not establish fresh API readiness.

## Restart review repair

Root's independent review identified a Minor functional gap: restart could not start a stopped Windows app because stop refused missing native state. Actual new restart selection before repair produced1failure/2passing controls, exit1 (`.test-temp/d01/cli-restart-red.log`). The missing-state case failed with unknown/readiness unavailable; ready replacement and unconfirmed-live refusal were positive/safety controls.

Windows native restart now measures readiness before choosing a path. Confirmed ready workers undergo existing incarnation-bound graceful stop before replacement. Missing/stale readiness permits start only after the same per-SID InstanceGuard can be acquired; an occupied guard refuses replacement without stop request. The probe is released before child startup so the child owns singleton enforcement. Legacy and non-Windows paths retain their prior sequence; no controller/worker/runtime source changed.

Three real subprocess/socket regressions cover stopped→measured ready, old incarnation exit0→new PID/run ID, and unconfirmed live worker with no stop request, no process death and no Popen attempt. Final focused CLI module plus unchanged early-PID integration control:15passed,0skipped, exit0 (`.test-temp/d01/cli-restart-final-green.log`); Ruff check/format pass. Command uses the previous selection with `--basetemp=.test-temp/d01/cli-restart-final-green`. Frozen replacement source and SHA256 manifest: `.superpowers/sdd/windows-public-beta-2026-10-02/D01-cli-fix1-review-snapshot/`; execution report `task-D01-cli-fix1-report.md`. Initial snapshot remains immutable. Root independent review and combined acceptance remain pending.
