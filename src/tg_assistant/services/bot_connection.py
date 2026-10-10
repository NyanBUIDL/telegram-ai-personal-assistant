"""Private owning-loop bot enrollment, measured readiness and native pairing.

The selected account owns the guard, database and runner. This service owns only
its Bot API client and work; no management router is attached here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta
from functools import wraps
from threading import RLock
from time import monotonic as actual_monotonic

from ..contracts import ConnectionStatus
from ..telegram.bot_api import BotApiTransport
from .bot_pairing import MANUAL, NONCE, AccountProof, NativePairingInvitation, positive_id
from .onboarding import StageVerification

_CODES = frozenset({
    "bot_unavailable", "bot_token_revoked", "bot_polling_conflict", "bot_timeout",
    "bot_account_required", "bot_check_required", "bot_pairing_expired",
})


class BotConnectionUnavailable(RuntimeError):
    def __init__(self, code="bot_unavailable"):
        self.code = code if type(code) is str and code in _CODES else "bot_unavailable"
        super().__init__(self.code)


def _require(condition, code="bot_unavailable"):
    if not condition:
        raise BotConnectionUnavailable(code)


def _owned_operation(method):
    @wraps(method)
    async def execute(self, *args, **kwargs):
        self._bind_loop()
        task = asyncio.current_task()
        self._tasks.add(task)
        code = None
        result = None
        try:
            async with self._lock:
                self._admit()
                result = await method(self, *args, **kwargs)
        except asyncio.CancelledError:
            self._withdraw()
            self._cleanup_candidate()
            raise
        except Exception as error:
            candidate_code = getattr(error, "code", None)
            code = candidate_code if type(candidate_code) is str and candidate_code in _CODES else "bot_unavailable"
        finally:
            self._tasks.discard(task)
        if code is not None:
            self._withdraw(code)
            self._cleanup_candidate()
            if code == "bot_token_revoked" and self._generation is not None:
                try:
                    self.repository.invalidate_enrollment(self._generation, reason="bot_revoked")
                except Exception:
                    code = "bot_unavailable"
                    self._withdraw(code)
            # A normal async wrapper catches in this task. An async generator's
            # athrow could retain the caller's active raw exception as context.
            raise BotConnectionUnavailable(code)
        return result

    return execute


class BotConnectionService:
    def __init__(
        self, repository, credentials, *, now=lambda: datetime.now(UTC),
        monotonic=actual_monotonic, _client_factory=BotApiTransport, _management_mode=False,
    ):
        self.repository, self.credentials = repository, credentials
        self._now, self._monotonic, self._client_factory = now, monotonic, _client_factory
        self._lock, self._mutex = asyncio.Lock(), RLock()
        self._loop = self._client = self._generation = self._reference = None
        self._identity = self._account_binding = None
        self._identity_checked = self._poll_checked = self._last_checked = None
        self._identity_mono = self._poll_mono = None
        self._invitation = self._invite_issued_mono = self._invite_deadline = None
        self._candidate = None
        self._tasks = set()
        self._close_task = None
        self._cursor = None
        self._closed = self._ready = self._started = False
        self._code = "bot_check_required"
        self._reply_code = None
        _require(type(_management_mode) is bool)
        self._management_mode = _management_mode
        self._pending_updates, self._ack_pending = [], []
        self._pending_available = False

    def __repr__(self):
        return "<PrivateBotConnectionService>"

    def _time(self):
        value = self._now()
        _require(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
        return value.astimezone(UTC)

    def _mono(self):
        value = self._monotonic()
        _require(type(value) in {int, float} and math.isfinite(value))
        return value

    def _bind_loop(self):
        loop = asyncio.get_running_loop()
        _require(self._loop is None or self._loop is loop)
        self._loop = loop

    def _owner(self):
        failed = False
        proof = None
        try:
            _require(self.repository.check_ownership() is None)
            proof = self.repository.account_verifier()
            _require(type(proof) is AccountProof and positive_id(proof.owner_id))
            _require(type(proof.fingerprint) is str and re.fullmatch(r"[a-f0-9]{64}", proof.fingerprint))
        except Exception:
            failed = True
        if failed:
            raise BotConnectionUnavailable("bot_account_required")
        return proof

    def _admit(self):
        self._bind_loop()
        _require(not self._closed)
        return self._owner()

    def _same_owner(self, expected):
        _require(self._admit() == expected, "bot_account_required")

    def _withdraw(self, code="bot_check_required"):
        with self._mutex:
            self._ready = False
            self._code = code
            self._identity_checked = self._poll_checked = None
            self._identity_mono = self._poll_mono = None

    def _cleanup_candidate(self):
        if self._candidate is not None:
            try:
                self.credentials.discard_candidate(self._candidate)
            except Exception:
                # A committed/uncertain publication or lost ownership remains
                # retained; history is never treated as a retirement permit.
                return
            self._candidate = None

    async def _drop_client(self):
        self._withdraw()
        if self._client is not None:
            await self._client.close()
            self._client = None
        self._generation = self._reference = None
        self._identity = self._account_binding = None
        self._cursor = None
        self._pending_updates, self._ack_pending = [], []
        self._pending_available = False

    def _forget_invitation(self):
        self._invitation = self._invite_issued_mono = self._invite_deadline = None

    def _cancel_current_invitation(self):
        invitation = self._invitation
        self._forget_invitation()
        if invitation is not None:
            self.repository.cancel_challenge(invitation.generation)

    async def _measure(self, expected, enrollment):
        identity = await self._client.verify_identity()
        self._same_owner(expected)
        _require(identity.bot_id == int(enrollment["bot_id"]) and identity.username == enrollment["username"])
        with self._mutex:
            self._identity = identity
            self._identity_checked = self._client.identity_checked_at
            self._identity_mono = self._mono()
            self._account_binding = expected
        if not self._management_mode or self._poll_checked is None:
            await self._poll_locked(expected)
        return identity

    @_owned_operation
    async def connect(self, token):
        _require(not self._management_mode)
        expected = self._owner()
        await self._drop_client()
        self._forget_invitation()
        self._client = self._client_factory(token)
        identity = await self._client.verify_identity()
        self._same_owner(expected)
        slot = self.credentials.allocate(token)
        self._candidate = slot
        self._same_owner(expected)
        with self.repository.publication_guard():
            enrollment = self.repository.publish_enrollment(
                bot_id=identity.bot_id, username=identity.username,
                generation=slot.generation, credential_slot=slot.reference,
            )
        self._candidate = None
        self._generation, self._reference = enrollment["generation"], enrollment["credential_slot"]
        self._started = True
        self._same_owner(expected)
        with self._mutex:
            self._identity, self._account_binding = identity, expected
            self._identity_checked = self._client.identity_checked_at
            self._identity_mono = self._mono()
        await self._poll_locked(expected)
        return identity

    @_owned_operation
    async def resume(self):
        expected = self._owner()
        if not self._started:
            self.repository.abandon_pending_after_restart()
            self._started = True
        document = self.repository.read()
        enrollment = document["enrollment"]
        _require(enrollment is not None, "bot_check_required")
        if self._management_mode:
            pair = document["pairing"]
            _require(pair is not None and pair["owner_id"] == str(expected.owner_id)
                     and pair["enrollment_generation"] == enrollment["generation"], "bot_check_required")
        if self._account_binding is not None and self._account_binding != expected:
            self._cancel_current_invitation()
        if self._generation != enrollment["generation"] or self._reference != enrollment["credential_slot"]:
            await self._drop_client()
            self._forget_invitation()
            token = self.credentials.get(enrollment["credential_slot"])
            self._same_owner(expected)
            self._client = self._client_factory(token)
            self._generation, self._reference = enrollment["generation"], enrollment["credential_slot"]
        _require(self._client is not None)
        return await self._measure(expected, enrollment)

    def _invitation_current(self, generation):
        _require(not self._closed and self._invitation is not None)
        _require(self._invitation.generation == generation)
        current = self._mono()
        _require(self._invite_issued_mono <= current < self._invite_deadline, "bot_pairing_expired")
        self._same_owner(self._account_binding)

    @_owned_operation
    async def issue_pairing(self) -> NativePairingInvitation:
        _require(not self._management_mode)
        _require(self.verification() is not None, "bot_check_required")
        issued_mono = self._mono()
        invitation = self.repository.issue_challenge()
        self._admit()
        self._invitation = invitation
        self._invite_issued_mono = issued_mono
        self._invite_deadline = issued_mono + 300
        self._invitation_current(invitation.generation)
        return invitation

    async def _reply(self, chat_id, message):
        code = None
        try:
            self._same_owner(self._account_binding)
            await self._client.bot.send_message(chat_id=chat_id, text=message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            candidate = getattr(error, "code", None)
            code = candidate if type(candidate) is str and candidate in _CODES else "bot_unavailable"
        if code is not None:
            self._reply_code = code
            self._withdraw(code)
            if code == "bot_token_revoked":
                try:
                    self.repository.invalidate_enrollment(self._generation, reason="bot_revoked")
                except Exception:
                    self._withdraw("bot_unavailable")

    async def _handle_update(self, update, expected, *, budget):
        _require(type(update.update_id) is int and 0 <= update.update_id <= 2**63 - 1)
        message = update.message
        invitation = self._invitation
        if message is None:
            return False
        sender, value = message.from_user, message.text
        if (
            sender is None or sender.is_bot is not False or not positive_id(sender.id)
            or message.chat.type != "private"
            or message.chat.id != sender.id or type(value) is not str or len(value) > 128
        ):
            return False
        guidance = "Mở ứng dụng trên máy tính để ghép bot với chủ sở hữu."
        if value in {"/start", "/help"}:
            self._cursor = max(self._cursor or 0, update.update_id + 1)
            if budget["help"] > 0:
                budget["help"] -= 1
                await self._reply(message.chat.id, guidance)
            return False
        if value.startswith("/start ") and NONCE.fullmatch(value[7:]):
            credential, kind = value[7:], "nonce"
        elif value.startswith("/pair ") and MANUAL.fullmatch(value[6:]):
            credential, kind = value[6:], "manual"
        else:
            return False
        if sender.id != expected.owner_id:
            self._cursor = max(self._cursor or 0, update.update_id + 1)
            if budget["help"] > 0:
                budget["help"] -= 1
                await self._reply(message.chat.id, guidance)
            return False
        if invitation is None or budget["attempts"] <= 0:
            return False
        if not self._invite_issued_mono <= self._mono() < self._invite_deadline:
            self._cancel_current_invitation()
            return False
        self._same_owner(expected)
        self._invitation_current(invitation.generation)
        document = self.repository.read()
        challenge = document["challenge"]
        if challenge is None or challenge["generation"] != invitation.generation:
            self._forget_invitation()
            return False
        budget["attempts"] -= 1
        consumed = self.repository.consume(
            credential, kind=kind, sender_id=sender.id, update_id=update.update_id,
            _commit_check=lambda: self._invitation_current(invitation.generation),
        )
        if consumed:
            self._forget_invitation()
            # SQL success is durable before the external reply. A timeout or
            # redelivery cannot cause another consume or blind resend.
            self._cursor = max(self._cursor or 0, update.update_id + 1)
            await self._reply(message.chat.id, "Ghép chủ sở hữu thành công.")
        return consumed

    async def _poll_locked(self, expected):
        _require(self._client is not None and self._generation is not None, "bot_check_required")
        if self._management_mode:
            _require(not self._pending_available and not self._ack_pending, "bot_polling_conflict")
        allowed = ("message", "callback_query") if self._management_mode else ("message",)
        updates = await self._client.poll_once(self._cursor, allowed_updates=allowed)
        _require(len(updates) <= 100)
        self._same_owner(expected)
        document = self.repository.read_admission()
        enrollment = document["enrollment"]
        _require(enrollment is not None and enrollment["generation"] == self._generation
                 and enrollment["credential_slot"] == self._reference)
        if self._management_mode:
            pair = document["pairing"]
            _require(pair is not None and pair["owner_id"] == str(expected.owner_id)
                     and pair["enrollment_generation"] == self._generation, "bot_check_required")
            previous = self._cursor - 1 if self._cursor is not None else -1
            for update in updates:
                _require(type(update.update_id) is int and previous < update.update_id <= 2**63 - 1)
                previous = update.update_id
        with self._mutex:
            self._poll_checked = self._client.poll_checked_at
            self._poll_mono = self._mono()
            self._last_checked = self._poll_checked
            self._ready = self._identity_checked is not None
            self._code = "bot_verified" if self._ready else "bot_check_required"
        if self._management_mode:
            self._pending_updates = updates
            self._pending_available = True
            return False
        paired = False
        budget = {"help": 4, "attempts": 8}
        for update in updates:
            _require(type(update.update_id) is int and 0 <= update.update_id <= 2**63 - 1)
            if self._cursor is not None and update.update_id < self._cursor:
                continue
            paired = await self._handle_update(
                update, expected, budget=budget,
            ) or paired
            self._cursor = max(self._cursor or 0, update.update_id + 1)
        self._same_owner(expected)
        return paired

    @_owned_operation
    async def poll_once(self):
        _require(not self._management_mode)
        expected = self._owner()
        _require(expected == self._account_binding, "bot_account_required")
        return await self._poll_locked(expected)

    @_owned_operation
    async def cancel_pairing(self):
        _require(not self._management_mode)
        self._cancel_current_invitation()

    @_owned_operation
    async def poll_management_once(self):
        _require(self._management_mode and not self._ack_pending, "bot_polling_conflict")
        _require(self.pairing_verification() is not None, "bot_check_required")
        if not self._pending_available:
            await self._poll_locked(self._owner())
        updates = self._pending_updates
        self._pending_updates, self._pending_available = [], False
        self._ack_pending = [update.update_id for update in updates]
        return updates

    def _management_binding(self):
        expected = self._owner()
        _require(self._management_mode and expected == self._account_binding)
        _require(self._identity is not None and self._identity_checked is not None
                 and self._identity_mono is not None)
        _require(timedelta(0) <= self._time() - self._identity_checked < timedelta(seconds=30))
        _require(0 <= self._mono() - self._identity_mono < 30)
        document = self.repository.read_admission()
        enrollment, pair = document["enrollment"], document["pairing"]
        _require(enrollment is not None and pair is not None
                 and enrollment["generation"] == self._generation
                 and enrollment["credential_slot"] == self._reference
                 and enrollment["bot_id"] == str(self._identity.bot_id)
                 and enrollment["username"] == self._identity.username
                 and pair["owner_id"] == str(expected.owner_id)
                 and pair["enrollment_generation"] == self._generation)
        return expected

    @_owned_operation
    async def refresh_pending_poll(self):
        """Measure the same unacknowledged offset without dispatch or ack.

        A long management handler may outlive one polling observation. Its
        health task uses the same owned transport and deduplicates this bounded
        queue, while the normal dispatcher still cannot take another batch.
        """
        expected = self._management_binding()
        sent_cursor = self._cursor
        updates = await self._client.poll_once(
            sent_cursor, allowed_updates=("message", "callback_query"),
        )
        self._same_owner(expected)
        _require(self._management_binding() == expected)
        _require(len(updates) <= 100)
        previous = sent_cursor - 1 if sent_cursor is not None else -1
        for update in updates:
            _require(type(update.update_id) is int and previous < update.update_id <= 2**63 - 1)
            previous = update.update_id
        # Dispatch may acknowledge an issued update while the SDK await yields.
        # Reconcile against the current cursor/issued IDs after the response.
        known = set(self._ack_pending) | {value.update_id for value in self._pending_updates}
        queued = list(self._pending_updates)
        largest = max(known | {self._cursor - 1 if self._cursor is not None else -1})
        for update in updates:
            if self._cursor is not None and update.update_id < self._cursor:
                continue
            if update.update_id in known:
                continue
            _require(update.update_id > largest)
            queued.append(update)
            known.add(update.update_id)
            largest = update.update_id
        _require(len(known) <= 100)
        self._pending_updates = queued
        self._pending_available = self._pending_available or bool(queued)
        with self._mutex:
            self._poll_checked = self._client.poll_checked_at
            self._last_checked = self._poll_checked
            self._poll_mono = self._mono()
            self._ready, self._code = True, "bot_verified"

    def acknowledge_update(self, update_id):
        self._admit()
        _require(self._management_mode and self.pairing_verification() is not None)
        _require(type(update_id) is int and self._ack_pending and update_id == self._ack_pending[0])
        _require(self._cursor is None or update_id >= self._cursor)
        self._ack_pending.pop(0)
        self._cursor = update_id + 1

    @property
    def bot(self):
        self._admit()
        _require(self.pairing_verification() is not None if self._management_mode else self.verification() is not None)
        return self._client.bot

    def _proof_snapshot(self):
        try:
            with self._mutex:
                if self._closed or not self._ready:
                    return None
                account, identity = self._account_binding, self._identity
                client = self._client
                generation, reference = self._generation, self._reference
                checked = (self._identity_checked, self._poll_checked)
                measured = (self._identity_mono, self._poll_mono)
            now, current = self._time(), self._mono()
            _require(all(value is not None and timedelta(0) <= now - value < timedelta(seconds=30) for value in checked))
            _require(all(value is not None and 0 <= current - value < 30 for value in measured))
            _require(self._owner() == account)
            document = self.repository.read_admission()
            enrollment = document["enrollment"]
            _require(enrollment is not None and enrollment["generation"] == generation
                     and enrollment["credential_slot"] == reference
                     and enrollment["bot_id"] == str(identity.bot_id)
                     and enrollment["username"] == identity.username)
            if self._management_mode:
                pair = document["pairing"]
                _require(pair is not None and pair["owner_id"] == str(account.owner_id)
                         and pair["enrollment_generation"] == generation)
            _require(self._owner() == account)
            now, current = self._time(), self._mono()
            with self._mutex:
                _require(not self._closed and self._ready is True
                         and self._client is client and self._identity is identity
                         and self._account_binding == account
                         and self._generation == generation and self._reference == reference
                         and (self._identity_checked, self._poll_checked) == checked
                         and (self._identity_mono, self._poll_mono) == measured)
                _require(all(value is not None and timedelta(0) <= now - value < timedelta(seconds=30)
                             for value in checked))
                _require(all(value is not None and 0 <= current - value < 30 for value in measured))
            return document, account, identity
        except Exception:
            return None

    def verification(self):
        snapshot = self._proof_snapshot()
        if snapshot is None:
            return None
        document, account, _identity = snapshot
        fingerprint = hashlib.sha256(json.dumps(
            [self.repository.profile_id, document["enrollment"], account.owner_id, account.fingerprint],
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")).hexdigest()
        return StageVerification(owner_id=str(account.owner_id), fingerprint=fingerprint)

    def pairing_verification(self):
        snapshot = self._proof_snapshot()
        if snapshot is None:
            return None
        document, account, identity = snapshot
        pair = document["pairing"]
        if pair is None or pair["owner_id"] != str(account.owner_id) or pair["bot_id"] != str(identity.bot_id):
            return None
        fingerprint = hashlib.sha256(json.dumps(
            [self.repository.profile_id, document["enrollment"], pair, account.fingerprint],
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")).hexdigest()
        return StageVerification(owner_id=str(account.owner_id), fingerprint=fingerprint)

    @property
    def verified_identity(self):
        snapshot = self._proof_snapshot()
        return snapshot[2] if snapshot is not None else None

    def pairing_username(self):
        identity = self.verified_identity
        return identity.username if identity is not None else None

    def connection_status(self):
        proof = self.verification()
        with self._mutex:
            checked, code, closed = self._last_checked, self._code, self._closed
        if proof is not None:
            state, code, message = "ready", "bot_verified", "Bot Telegram đã được xác minh."
            capabilities = ["bot_verified"]
            if self.pairing_verification() is not None:
                capabilities.append("owner_paired")
        else:
            if code == "bot_verified":
                code = "bot_check_required"
            state = "disconnected" if closed or code in {"bot_token_revoked", "bot_account_required"} else "unknown"
            message, capabilities = "Cần kiểm tra lại kết nối bot Telegram.", []
        return ConnectionStatus(
            service="control_bot", state=state, checked_at=checked, code=code,
            message=message, next_action=None if proof is not None else "Mở kết nối bot để kiểm tra lại.",
            capabilities=capabilities,
        )

    async def _close_owned(self):
        tasks = tuple(task for task in self._tasks if task is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            self._cleanup_candidate()
            if self._client is not None:
                await self._client.close()
                self._client = None
            self._forget_invitation()

    async def close(self):
        self._bind_loop()
        with self._mutex:
            self._closed = True
        self._withdraw()
        if self._close_task is None or (self._close_task.done() and self._close_task.exception() is not None):
            self._close_task = asyncio.create_task(self._close_owned())
        code = None
        try:
            await asyncio.wait_for(asyncio.shield(self._close_task), 5)
        except TimeoutError:
            code = "bot_timeout"
        except Exception:
            code = "bot_unavailable"
        if code is not None:
            raise BotConnectionUnavailable(code)
