"""Real isolated SQLite enrollment and owner pairing tests."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, insert, select, text, update

from alembic import command
from tg_assistant.db.base import configure_sqlite
from tg_assistant.db.models import AppSetting, TelegramAccount
from tg_assistant.desktop.instance import InstanceGuard
from tg_assistant.paths import ensure_runtime_dirs
from tg_assistant.services import bot_pairing, maintenance
from tg_assistant.services.maintenance import MaintenanceService

ROOT = Path(__file__).resolve().parents[2]
OWNER = 9007199254740993


def scoped_reference(profile, generation, *, sid=None):
    from tg_assistant.paths import current_user_sid

    binding = hashlib.sha256(json.dumps(
        [current_user_sid() if sid is None else sid, profile], ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return f"bot:v1:{binding}:{generation}"


@pytest.fixture
def system(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(name)
    profile = "draft-owner-profile"
    paths = ensure_runtime_dirs(tmp_path / "profile", profile_id=profile)
    url = "sqlite:///" + str(paths["db"] / "assistant.sqlite3")
    engines = []
    fences = []
    guard = InstanceGuard(tmp_path / "synthetic-sid-control").acquire()
    clock = [datetime(2026, 10, 7, tzinfo=UTC)]
    proof = [bot_pairing.AccountProof(OWNER, "a" * 64)]

    def ownership():
        assert guard.lock and not guard.lock.closed and not guard.lock.file.closed
        observed = (guard.directory / "runtime.lock").stat()
        handle = os.fstat(guard.lock.file.fileno())
        assert (observed.st_dev, observed.st_ino) == (handle.st_dev, handle.st_ino)

    def make_engine():
        engine = create_engine(url, pool_pre_ping=True)
        event.listen(engine, "connect", configure_sqlite)
        engines.append(engine)
        return engine

    engine = make_engine()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    with engine.begin() as connection:
        connection.execute(insert(TelegramAccount).values(
            telegram_user_id=OWNER, is_active=True, is_owner_paired=False,
        ))

    def make():
        selected = make_engine()
        fence = MaintenanceService(paths["config"], profile_id=profile)
        fences.append(fence)
        return bot_pairing.PairingRepository(
            engine=selected, profile_id=profile, profile_root=paths["data"], fence=fence,
            check_ownership=ownership, account_verifier=lambda: proof[0], now=lambda: clock[0],
        )

    repo = make()
    repo.publish_enrollment(bot_id=123456789, generation="b" * 32, credential_slot=scoped_reference(profile, "b" * 32))
    invitation = repo.issue_challenge()

    def durable():
        with make_engine().connect() as connection:
            document = connection.scalar(select(AppSetting.value).where(AppSetting.key == repo.key))
            paired = connection.scalar(select(TelegramAccount.is_owner_paired))
            return document, paired

    try:
        yield dict(repo=repo, make=make, engine=engine, clock=clock, proof=proof,
                   guard=guard, invitation=invitation, durable=durable, paths=paths)
    finally:
        guard.close()
        for fence in fences:
            fence.close()
        for selected in engines:
            selected.dispose()


def consume(repo, value, *, kind="nonce", sender=OWNER, update=101):
    return repo.consume(value, kind=kind, sender_id=sender, update_id=update)


def test_joint_commit_pairs_document_and_account(system):
    assert consume(system["repo"], system["invitation"].payload) is True
    document, paired = system["durable"]()
    assert paired is True
    assert document["challenge"]["state"] == "consumed"
    assert document["pairing"]["owner_id"] == "9007199254740993"
    assert document["pairing"]["bot_id"] == "123456789"
    assert document["pairing"]["actual_update_id"] == 101


@pytest.mark.parametrize("failed_table", ["telegram_accounts", "app_settings"])
def test_trigger_failure_rolls_back_entire_pair(system, failed_table):
    before = system["durable"]()
    with system["engine"].begin() as connection:
        connection.execute(text(
            f"CREATE TRIGGER draft_fail BEFORE UPDATE ON {failed_table} "
            "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        ))
    with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
        consume(system["repo"], system["invitation"].payload)
    assert system["durable"]() == before
    with system["engine"].begin() as connection:
        connection.execute(text("DROP TRIGGER draft_fail"))
    assert consume(system["repo"], system["invitation"].payload) is True


@pytest.mark.parametrize("manual", [False, True])
def test_independent_connections_consume_once(system, manual):
    first, other = system["repo"], system["make"]()
    barrier = threading.Barrier(2)

    def contender(repo, value, kind, update):
        barrier.wait(timeout=5)  # before BEGIN IMMEDIATE, never inside the writer lock
        return consume(repo, value, kind=kind, update=update)

    other_value = system["invitation"].manual_code if manual else system["invitation"].payload
    other_kind = "manual" if manual else "nonce"
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(contender, first, system["invitation"].payload, "nonce", 101)
        b = pool.submit(contender, other, other_value, other_kind, 102)
        results = [a.result(timeout=10), b.result(timeout=10)]
    assert sorted(results) == [False, True]
    document, paired = system["durable"]()
    assert paired is True and document["challenge"]["state"] == "consumed"
    assert document["revision"] == 3  # enrollment, challenge, one consumption only
    assert document["pairing"]["actual_update_id"] in {101, 102}


def test_wrong_sender_leaves_valid_nonce_available(system):
    before = system["durable"]()
    assert consume(system["repo"], system["invitation"].payload, sender=42) is False
    assert system["durable"]() == before
    assert consume(system["repo"], system["invitation"].payload) is True


@pytest.mark.parametrize("elapsed,allowed", [(-1, False), (299.999, True), (300, False)])
def test_exact_deadline(system, elapsed, allowed):
    system["clock"][0] += timedelta(seconds=elapsed)
    assert consume(system["repo"], system["invitation"].payload) is allowed
    assert system["durable"]()[1] is allowed


def test_account_change_before_commit_rolls_back(system):
    before = system["durable"]()

    def changed(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE telegram_accounts"):
            system["proof"][0] = bot_pairing.AccountProof(OWNER, "c" * 64)

    event.listen(system["repo"].engine, "after_cursor_execute", changed)
    try:
        with pytest.raises(bot_pairing.PairingUnavailable):
            consume(system["repo"], system["invitation"].payload)
    finally:
        event.remove(system["repo"].engine, "after_cursor_execute", changed)
    assert system["durable"]() == before


def test_lost_instance_guard_denies_writer(system):
    before = system["durable"]()
    system["guard"].close()
    with pytest.raises(bot_pairing.PairingUnavailable):
        consume(system["repo"], system["invitation"].payload)
    assert system["durable"]() == before


def test_constructor_lost_guard_has_fixed_unavailable_error(system):
    system["guard"].close()
    with pytest.raises(bot_pairing.PairingUnavailable) as error:
        system["make"]()
    assert str(error.value) == "pairing_unavailable"


def test_real_maintenance_fence_blocks_writer(system):
    before = system["durable"]()
    fence = system["repo"].fence
    lease = fence.acquire("draft-maintenance", timeout=1)
    try:
        with pytest.raises(bot_pairing.PairingUnavailable):
            consume(system["repo"], system["invitation"].payload)
        assert system["durable"]() == before
    finally:
        fence.release(lease)


def test_read_under_actual_maintenance_is_denied(system):
    before = system["durable"]()
    fence = system["repo"].fence
    lease = fence.acquire("draft-read-maintenance", timeout=1)
    try:
        with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
            system["repo"].read()
        assert system["durable"]() == before
    finally:
        fence.release(lease)
    assert system["repo"].read() == before[0]


def test_read_lost_guard_after_query_is_denied(system):
    def release_guard(_connection, _cursor, statement, *_args):
        if statement.startswith("SELECT app_settings.value"):
            system["guard"].close()

    event.listen(system["repo"].engine, "after_cursor_execute", release_guard)
    try:
        with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
            system["repo"].read()
    finally:
        event.remove(system["repo"].engine, "after_cursor_execute", release_guard)


def test_replacement_generation_rejects_old_credentials(system):
    system["repo"].publish_enrollment(
        bot_id=123456789, generation="d" * 32, credential_slot=scoped_reference(system["repo"].profile_id, "d" * 32),
    )
    before = system["durable"]()
    assert consume(system["repo"], system["invitation"].payload) is False
    assert consume(system["repo"], system["invitation"].manual_code, kind="manual") is False
    assert system["durable"]() == before


def test_document_contains_hashes_only(system):
    invitation = system["invitation"]
    raw = json.dumps(system["durable"]()[0])
    assert len(invitation.payload) == 48
    assert invitation.payload not in raw and invitation.manual_code not in raw
    assert "nonce_hash" in raw and "manual_hash" in raw


@pytest.mark.parametrize("operation", ["cancel", "restart"])
def test_cancel_or_restart_invalidates_both_routes(system, operation):
    invitation = system["invitation"]
    if operation == "cancel":
        system["repo"].cancel_challenge(invitation.generation)
    else:
        system["make"]().abandon_pending_after_restart()
    assert consume(system["repo"], invitation.payload) is False
    assert consume(system["repo"], invitation.manual_code, kind="manual") is False
    document, paired = system["durable"]()
    assert paired is False and document["revision"] == 3
    assert document["challenge"]["state"] == ("cancelled" if operation == "cancel" else "abandoned")
    replacement = system["repo"].issue_challenge()
    assert replacement.payload != invitation.payload
    assert consume(system["repo"], replacement.payload) is True


def test_cancel_after_consumption_preserves_completed_pair(system):
    assert consume(system["repo"], system["invitation"].payload)
    before = system["durable"]()
    system["make"]().cancel_challenge(system["invitation"].generation)
    assert system["durable"]() == before
    assert consume(system["make"](), system["invitation"].payload) is False


@pytest.mark.parametrize("paired", [False, True])
def test_revocation_invalidates_current_enrollment_and_pair(system, paired):
    if paired:
        assert consume(system["repo"], system["invitation"].payload)
    generation = system["durable"]()[0]["enrollment"]["generation"]
    system["repo"].invalidate_enrollment(generation, reason="bot_revoked")
    document, flag = system["durable"]()
    assert document["enrollment"] is None and document["pairing"] is None and flag is False
    assert consume(system["repo"], system["invitation"].payload) is False
    assert consume(system["repo"], system["invitation"].manual_code, kind="manual") is False


def test_nonce_and_manual_share_one_use_in_reverse_order(system):
    assert consume(system["repo"], system["invitation"].manual_code, kind="manual") is True
    before = system["durable"]()
    assert consume(system["make"](), system["invitation"].payload) is False
    assert system["durable"]() == before


@pytest.mark.parametrize("operation", ["cancel", "regenerate"])
def test_independent_consume_vs_control_is_serializable(system, operation):
    other = system["make"]()
    barrier = threading.Barrier(2)
    invitation = system["invitation"]

    def use_nonce():
        barrier.wait(timeout=5)
        return consume(system["repo"], invitation.payload)

    def control():
        barrier.wait(timeout=5)
        if operation == "cancel":
            return other.cancel_challenge(invitation.generation)
        return other.issue_challenge()

    with ThreadPoolExecutor(max_workers=2) as pool:
        consuming, controlling = pool.submit(use_nonce), pool.submit(control)
        consumed, controlled = consuming.result(timeout=10), controlling.result(timeout=10)
    document, paired = system["durable"]()
    assert paired is consumed
    assert document["pairing"] is not None if consumed else document["pairing"] is None
    if operation == "cancel":
        assert controlled["challenge"]["state"] in {"consumed", "cancelled"}
        assert document["challenge"]["state"] == ("consumed" if consumed else "cancelled")
        assert document["revision"] == 3
    else:
        assert document["challenge"]["generation"] == controlled.generation
        assert document["challenge"]["state"] == "pending"
        assert document["revision"] == (4 if consumed else 3)
        assert consume(other, invitation.payload) is False
        if consumed:
            assert document["pairing"]["actual_update_id"] == 101


def test_waiting_writer_checks_deadline_after_begin(system):
    entered = threading.Event()
    other = system["make"]()

    def beginning(_connection, _cursor, statement, *_args):
        if statement == "BEGIN IMMEDIATE":
            entered.set()

    event.listen(other.engine, "before_cursor_execute", beginning)
    try:
        with system["engine"].connect() as holder, ThreadPoolExecutor(max_workers=1) as pool:
            holder.execute(text("BEGIN IMMEDIATE"))
            result = pool.submit(consume, other, system["invitation"].payload)
            try:
                assert entered.wait(timeout=5)
                system["clock"][0] += timedelta(seconds=300)
            finally:
                holder.rollback()
            assert result.result(timeout=10) is False
        assert system["durable"]()[1] is False
        assert system["durable"]()[0]["revision"] == 2
    finally:
        event.remove(other.engine, "before_cursor_execute", beginning)


def test_deadline_change_before_commit_rolls_back(system):
    before = system["durable"]()

    def changed(_connection, _cursor, statement, *_args):
        if statement.startswith("UPDATE app_settings"):
            system["clock"][0] += timedelta(seconds=300)

    event.listen(system["repo"].engine, "after_cursor_execute", changed)
    try:
        with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
            consume(system["repo"], system["invitation"].payload)
    finally:
        event.remove(system["repo"].engine, "after_cursor_execute", changed)
    assert system["durable"]() == before


def test_real_revision_cas_conflict_rolls_back_account(system):
    before = system["durable"]()
    injected = []

    def changed(connection, _cursor, statement, *_args):
        if statement.startswith("UPDATE app_settings") and not injected:
            injected.append(True)
            connection.execute(text(
                "UPDATE app_settings SET value=json_set(value, '$.revision', 99) WHERE key=:key"
            ), dict(key=system["repo"].key))

    event.listen(system["repo"].engine, "before_cursor_execute", changed)
    try:
        with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
            consume(system["repo"], system["invitation"].payload)
    finally:
        event.remove(system["repo"].engine, "before_cursor_execute", changed)
    assert injected == [True]
    assert system["durable"]() == before
    assert consume(system["repo"], system["invitation"].payload) is True


@pytest.mark.parametrize("changed", ["profile", "sid", "document", "fence"])
def test_binding_change_denies_without_reset(system, changed):
    marker = system["paths"]["data"] / ".tg-assistant-data"
    original = marker.read_text("utf-8")
    if changed in {"profile", "sid"}:
        values = json.loads(original)
        values["profile_id" if changed == "profile" else "sid"] = "synthetic-foreign"
        marker.write_text(json.dumps(values), encoding="utf-8")
    elif changed == "document":
        document = system["durable"]()[0]
        document["storage_hash"] = "d" * 64
        with system["engine"].begin() as connection:
            connection.execute(update(AppSetting).where(
                AppSetting.key == system["repo"].key,
            ).values(value=document))
    else:
        system["repo"].fence.profile_id = "synthetic-foreign"
    before = system["durable"]()
    try:
        with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
            consume(system["repo"], system["invitation"].payload)
        assert system["durable"]() == before
    finally:
        marker.write_text(original, encoding="utf-8")


@pytest.mark.parametrize("changed", ["none", "invalid", "inactive", "ambiguous"])
def test_current_account_proof_and_unique_active_row_required(system, changed):
    if changed == "none":
        system["proof"][0] = None
    elif changed == "invalid":
        system["proof"][0] = bot_pairing.AccountProof(True, "synthetic-invalid")
    else:
        with system["engine"].begin() as connection:
            if changed == "inactive":
                connection.execute(update(TelegramAccount).values(is_active=False))
            else:
                connection.execute(insert(TelegramAccount).values(
                    telegram_user_id=42, is_active=True, is_owner_paired=False,
                ))
    before = system["durable"]()
    with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
        consume(system["repo"], system["invitation"].payload)
    assert system["durable"]() == before


def test_unrelated_sqlite_target_is_not_opened(system, tmp_path):
    destination = tmp_path / "unrelated.sqlite3"
    other = create_engine("sqlite:///" + str(destination))
    try:
        with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
            bot_pairing.PairingRepository(
                engine=other, profile_id=system["repo"].profile_id,
                profile_root=system["paths"]["data"], fence=system["repo"].fence,
                check_ownership=system["repo"].check_ownership,
                account_verifier=system["repo"].account_verifier, now=system["repo"].now,
            )
        assert not destination.exists()
    finally:
        other.dispose()


@pytest.mark.parametrize("credential,kind", [
    ("pair_" + "a" * 42, "nonce"), ("pair_" + "a" * 42 + "b", "nonce"),
    ("pair_" + "a" * 43 + " ", "nonce"), ("pair_" + "a" * 60, "nonce"),
    ("AAAA-BBBB", "manual"), (True, "nonce"), ("synthetic-canary", "unsupported"),
])
def test_malformed_credentials_leave_challenge_unchanged(system, credential, kind):
    before = system["durable"]()
    assert consume(system["repo"], credential, kind=kind) is False
    assert system["durable"]() == before


@pytest.mark.parametrize("changed", ["revision", "unknown", "pairing"])
def test_corrupt_state_fails_closed_without_republication(system, changed):
    document = system["durable"]()[0]
    if changed == "revision":
        document["revision"] = True
    elif changed == "unknown":
        document["unrecognized_field"] = "synthetic-canary-do-not-echo"
    else:
        document["pairing"] = dict(owner_id=str(OWNER))
    with system["engine"].begin() as connection:
        connection.execute(update(AppSetting).where(
            AppSetting.key == system["repo"].key,
        ).values(value=document))
    before = system["durable"]()
    with pytest.raises(bot_pairing.PairingUnavailable) as error:
        system["repo"].issue_challenge()
    assert str(error.value) == "pairing_unavailable"
    assert system["durable"]() == before


@pytest.mark.parametrize("same_owner", [True, False])
def test_new_account_candidate_preserves_only_same_owner_pair(system, same_owner):
    assert consume(system["repo"], system["invitation"].payload)
    previous = system["durable"]()[0]["pairing"]
    owner = OWNER if same_owner else 42
    if not same_owner:
        with system["engine"].begin() as connection:
            connection.execute(update(TelegramAccount).values(telegram_user_id=owner))
    system["proof"][0] = bot_pairing.AccountProof(owner, "c" * 64)
    invitation = system["repo"].issue_challenge()
    document, paired = system["durable"]()
    assert paired is same_owner
    assert document["pairing"] == previous if same_owner else document["pairing"] is None
    assert document["challenge"]["owner_id"] == str(owner)
    assert document["challenge"]["account_candidate_fingerprint"] == "c" * 64
    assert consume(system["repo"], invitation.payload, sender=owner) is True


@pytest.mark.parametrize("username", ["gif", "_existing", "3existing"])
def test_accepts_approved_scoped_reference_and_verified_username(system, username):
    generation = "e" * 32
    reference = scoped_reference(system["repo"].profile_id, generation)
    enrollment = system["repo"].publish_enrollment(
        bot_id=123456789, generation=generation, credential_slot=reference, username=username,
    )
    assert enrollment["credential_slot"] == reference and enrollment["username"] == username
    invitation = system["repo"].issue_challenge()
    assert consume(system["repo"], invitation.payload) is True
    assert system["durable"]()[0]["pairing"]["enrollment_generation"] == generation


@pytest.mark.parametrize("changed", ["foreign_binding", "foreign_sid", "foreign_generation", "legacy", "global"])
def test_reference_must_bind_current_sid_profile_and_enrollment(system, changed):
    generation = "e" * 32
    if changed == "foreign_binding":
        reference = scoped_reference("synthetic-foreign-profile", generation)
    elif changed == "foreign_sid":
        reference = scoped_reference(system["repo"].profile_id, generation, sid="synthetic-foreign-sid")
    elif changed == "foreign_generation":
        reference = scoped_reference(system["repo"].profile_id, "f" * 32)
    elif changed == "legacy":
        reference = "draft-slot-b"
    else:
        reference = "telegram_bot_token"
    before = system["durable"]()
    with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
        system["repo"].publish_enrollment(
            bot_id=123456789, generation=generation, credential_slot=reference,
        )
    assert system["durable"]() == before


def publication_rows(system):
    with system["engine"].connect() as connection:
        return dict(connection.execute(select(AppSetting.key, AppSetting.value).where(
            AppSetting.key.like("bot-pub.%"),
        )).all())


def test_publication_history_survives_replace_and_revoke(system):
    repo = system["repo"]
    original = publication_rows(system)
    assert len(original) == 1
    assert system["durable"]()[0]["publication_history_schema"] == 1
    repo.publish_enrollment(bot_id=123456789, generation="d" * 32, credential_slot=scoped_reference(system["repo"].profile_id, "d" * 32))
    replaced = publication_rows(system)
    assert len(replaced) == 2 and all(replaced[key] == value for key, value in original.items())
    repo.invalidate_enrollment("d" * 32, reason="bot_disconnected")
    assert publication_rows(system) == replaced


def test_history_insert_failure_rolls_back_enrollment_and_account(system):
    repo = system["repo"]
    assert consume(repo, system["invitation"].payload)
    before = system["durable"](), publication_rows(system)
    with system["engine"].begin() as connection:
        connection.execute(text(
            "CREATE TRIGGER fail_publication BEFORE INSERT ON app_settings "
            "WHEN NEW.key LIKE 'bot-pub.%' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        ))
    with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
        repo.publish_enrollment(bot_id=123456789, generation="d" * 32, credential_slot=scoped_reference(system["repo"].profile_id, "d" * 32))
    assert (system["durable"](), publication_rows(system)) == before


def test_current_reference_and_candidate_history_have_exact_results(system):
    repo = system["repo"]
    first = scoped_reference(repo.profile_id, "b" * 32)
    second = scoped_reference(repo.profile_id, "d" * 32)
    assert repo.current_reference() == first
    assert repo.candidate_unpublished(first) is False
    assert repo.candidate_unpublished(second) is True
    repo.publish_enrollment(bot_id=123456789, generation="d" * 32, credential_slot=second)
    assert repo.candidate_unpublished(first) is False
    repo.invalidate_enrollment("d" * 32, reason="bot_disconnected")
    assert repo.current_reference() is None
    assert repo.candidate_unpublished(first) is False and repo.candidate_unpublished(second) is False
    other = system["make"]()
    assert other.candidate_unpublished(first) is False and other.candidate_unpublished(second) is False
    before = system["durable"](), publication_rows(system)
    with pytest.raises(bot_pairing.PairingUnavailable):
        repo.publish_enrollment(bot_id=123456789, generation="b" * 32, credential_slot=first)
    assert (system["durable"](), publication_rows(system)) == before


@pytest.mark.parametrize("method", ["current_reference", "candidate_unpublished"])
def test_reference_queries_hold_actual_maintenance_fence(system, method):
    repo = system["repo"]
    callback = getattr(repo, method)
    arguments = [scoped_reference(repo.profile_id, "e" * 32)] if method == "candidate_unpublished" else []
    lease = repo.fence.acquire("reference-query-maintenance", timeout=1)
    try:
        with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
            callback(*arguments)
    finally:
        repo.fence.release(lease)


@pytest.mark.parametrize("change", ["profile_id", "sid_hash", "storage_hash", "reference_hash", "generation", "published_revision", "published_at", "unknown"])
def test_corrupt_publication_history_fails_closed(system, change):
    repo = system["repo"]
    key, history = next(iter(publication_rows(system).items()))
    history[change] = True if change == "published_revision" else "synthetic-invalid"
    with system["engine"].begin() as connection:
        connection.execute(update(AppSetting).where(AppSetting.key == key).values(value=history))
    with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
        repo.candidate_unpublished(scoped_reference(repo.profile_id, "b" * 32))
    with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
        repo.read()


@pytest.mark.parametrize("value", [None, True, 0, 2])
def test_old_or_invalid_history_schema_is_not_adopted(system, value):
    repo = system["repo"]
    state = system["durable"]()[0]
    if value is None:
        state.pop("publication_history_schema")
    else:
        state["publication_history_schema"] = value
    with system["engine"].begin() as connection:
        connection.execute(update(AppSetting).where(AppSetting.key == repo.key).values(value=state))
    with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
        repo.candidate_unpublished(scoped_reference(repo.profile_id, "e" * 32))


def test_history_key_binding_and_exact_publication_revision(system):
    repo = system["repo"]
    reference = scoped_reference(repo.profile_id, "b" * 32)
    key, history = next(iter(publication_rows(system).items()))
    expected = hashlib.sha256(json.dumps(
        [repo.profile_id, reference], ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    assert key == "bot-pub." + expected and len(key) <= 128
    assert history["published_revision"] == 1
    assert history["reference_hash"] == hashlib.sha256(reference.encode()).hexdigest()
    assert history["published_at"] == system["clock"][0].isoformat()


def test_missing_state_with_existing_publication_rows_is_not_fresh(system):
    repo = system["repo"]
    with system["engine"].begin() as connection:
        connection.execute(text("DELETE FROM app_settings WHERE key=:key"), {"key": repo.key})
    with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
        repo.candidate_unpublished(scoped_reference(repo.profile_id, "e" * 32))


def test_state_update_failure_rolls_back_already_inserted_history(system):
    repo = system["repo"]
    assert consume(repo, system["invitation"].payload)
    before = system["durable"](), publication_rows(system)
    with system["engine"].begin() as connection:
        connection.execute(text(
            "CREATE TRIGGER fail_state BEFORE UPDATE ON app_settings "
            "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        ))
    with pytest.raises(bot_pairing.PairingUnavailable, match="pairing_unavailable"):
        repo.publish_enrollment(
            bot_id=123456789, generation="d" * 32,
            credential_slot=scoped_reference(repo.profile_id, "d" * 32),
        )
    assert (system["durable"](), publication_rows(system)) == before


def admission_read(repo):
    return repo.read_admission()


def test_admission_read_rejects_busy_publication_without_waiting(system):
    repo = system["repo"]
    entered = threading.Event()

    def read():
        entered.set()
        return admission_read(repo)

    with ThreadPoolExecutor(max_workers=1) as executor:
        with repo._publication_lock:
            future = executor.submit(read)
            assert entered.wait(2)
            with pytest.raises(bot_pairing.PairingUnavailable):
                future.result(timeout=0.5)
        # The task is complete before releasing the owner's publication guard.
        assert future.done()


def test_admission_read_ignores_exhausted_shared_engine_pool(system):
    repo = system["repo"]
    pool = repo.engine.pool
    checked_out = []
    try:
        for _ in range(pool.size() + pool._max_overflow):
            checked_out.append(repo.engine.connect())
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(admission_read, repo)
            try:
                assert future.result(timeout=0.5) == system["durable"]()[0]
            except FutureTimeoutError:
                for connection in checked_out:
                    connection.close()
                checked_out.clear()
                raise
    finally:
        for connection in checked_out:
            connection.close()


@pytest.mark.parametrize("change", ["account", "flag"])
def test_admission_read_rechecks_actual_account_and_pair_flag(system, change):
    repo = system["repo"]
    assert consume(repo, system["invitation"].payload)
    with system["engine"].begin() as connection:
        values = {"telegram_user_id": 42} if change == "account" else {"is_owner_paired": False}
        connection.execute(update(TelegramAccount).values(**values))
    with pytest.raises(bot_pairing.PairingUnavailable):
        admission_read(repo)


def test_admission_read_never_retries_contended_maintenance_control(system, monkeypatch):
    repo = system["repo"]
    retries = []

    def unexpected_retry(_seconds):
        retries.append(True)
        raise AssertionError("unexpected_maintenance_retry")

    with maintenance.FileLock(repo.fence.control, exclusive=True):
        monkeypatch.setattr(maintenance.time, "sleep", unexpected_retry)
        with pytest.raises(bot_pairing.PairingUnavailable):
            admission_read(repo)
    assert retries == []


@pytest.mark.parametrize("budget", [True, -1, 0.21, float("nan"), "0"])
def test_maintenance_admission_rejects_invalid_budget(system, budget):
    with pytest.raises(maintenance.MaintenanceBusy, match="maintenance_admission_invalid"):
        with system["repo"].fence.operation(admission_timeout=budget):
            raise AssertionError("invalid_admission_was_opened")


def test_admission_read_lost_proof_after_sql_query_is_denied(system):
    repo = system["repo"]

    def invalidate(_connection, _cursor, statement, *_args):
        if statement.startswith("SELECT app_settings.value"):
            system["proof"][0] = None

    event.listen(repo._admission_engine, "after_cursor_execute", invalidate)
    try:
        with pytest.raises(bot_pairing.PairingUnavailable):
            repo.read_admission()
    finally:
        event.remove(repo._admission_engine, "after_cursor_execute", invalidate)


def test_monotonic_commit_callback_rechecks_after_sql_writes_and_rolls_back(system):
    repo = system["repo"]
    system["clock"][0] += timedelta(seconds=10)
    before = system["durable"]()
    deadline_passed = []
    checked = []

    def after_write(_connection, _cursor, statement, *_args):
        if statement.startswith("UPDATE app_settings"):
            deadline_passed.append(True)
            # UTC rollback must not extend the native monotonic deadline.
            system["clock"][0] -= timedelta(seconds=1)

    def commit_check():
        checked.append(True)
        assert not deadline_passed

    event.listen(repo.engine, "after_cursor_execute", after_write)
    try:
        kwargs = dict(kind="nonce", sender_id=OWNER, update_id=101)
        kwargs["_commit_check"] = commit_check
        with pytest.raises(bot_pairing.PairingUnavailable):
            repo.consume(system["invitation"].payload, **kwargs)
    finally:
        event.remove(repo.engine, "after_cursor_execute", after_write)
    assert checked == [True] and deadline_passed == [True]
    assert system["durable"]() == before


@pytest.mark.parametrize("value", [True, False, 0, "ok"])
def test_commit_callback_must_return_exact_none(system, value):
    before = system["durable"]()
    with pytest.raises(bot_pairing.PairingUnavailable):
        system["repo"].consume(
            system["invitation"].payload, kind="nonce", sender_id=OWNER,
            update_id=101, _commit_check=lambda: value,
        )
    assert system["durable"]() == before
