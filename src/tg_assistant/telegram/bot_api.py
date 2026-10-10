"""Private owned Bot API transport with sanitized measurements."""

import asyncio
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import PRODUCTION
from aiogram.exceptions import (
    TelegramConflictError,
    TelegramNetworkError,
    TelegramUnauthorizedError,
)
from aiogram.methods import GetMe, GetUpdates
from aiogram.types import Update


@dataclass(frozen=True)
class BotIdentity:
    bot_id: int
    username: str


class BotApiUnavailable(Exception):
    def __init__(self, code="bot_unavailable"):
        self.code = code if type(code) is str and code in {
            "bot_token_revoked", "bot_polling_conflict", "bot_timeout", "bot_unavailable",
        } else "bot_unavailable"
        super().__init__(self.code)


class _IdentitySession(AiohttpSession):
    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109 - SDK override signature
        # Retain SDK session, URL/form builder and response parser. The only
        # request adaptation refuses redirects of token-bearing Bot API URLs.
        code = None
        result = None
        try:
            session = await self.create_session()
            async with session.post(
                self.api.api_url(token=bot.token, method=method.__api_method__),
                data=self.build_form_data(bot=bot, method=method),
                timeout=self.timeout if timeout is None else timeout,
                allow_redirects=False,
            ) as response:
                content = await response.text()
            result = self.check_response(bot, method, response.status, content).result
        except asyncio.CancelledError:
            raise
        except BotApiUnavailable as error:
            code = error.code
        except TelegramUnauthorizedError:
            code = "bot_token_revoked"
        except TelegramConflictError:
            code = "bot_polling_conflict"
        except TimeoutError:
            code = "bot_timeout"
        except TelegramNetworkError as error:
            code = "bot_timeout" if isinstance(error.__cause__, TimeoutError) else "bot_unavailable"
        except Exception:
            code = "bot_unavailable"
        if code is not None:
            # Borrowed SDK SendMessage/AnswerCallbackQuery also pass here. Never
            # retain a raw SDK parser/body/token-URI exception as context.
            raise BotApiUnavailable(code)
        return result

    def check_response(self, bot, method, status_code, content):
        if status_code == 401:
            raise BotApiUnavailable("bot_token_revoked")
        if status_code == 409:
            raise BotApiUnavailable("bot_polling_conflict")
        if status_code != 200:
            raise BotApiUnavailable()
        if isinstance(method, GetMe) and status_code == 200:
            value = None
            try:
                envelope = json.loads(content)
                if isinstance(envelope, dict) and envelope.get("ok") is True:
                    value = envelope.get("result")
            except (ValueError, TypeError):
                pass
            if (
                not isinstance(value, dict) or type(value.get("id")) is not int
                or value["id"] <= 0 or value.get("is_bot") is not True
                or type(value.get("username")) is not str
                or re.fullmatch(r"[A-Za-z0-9_]{1,32}", value["username"], flags=re.ASCII) is None
            ):
                raise BotApiUnavailable()
        return super().check_response(bot, method, status_code, content)


class BotApiTransport:
    def __init__(self, token, *, request_timeout=5.0):
        if (
            type(request_timeout) not in {int, float} or not 0 < request_timeout <= 5
            or not math.isfinite(request_timeout)
        ):
            raise BotApiUnavailable()
        failed = False
        try:
            self._session = _IdentitySession(
                api=PRODUCTION,
                timeout=request_timeout,
            )
            self._bot = Bot(token, session=self._session)
        except Exception:
            failed = True
        if failed:
            raise BotApiUnavailable()
        self._timeout = request_timeout
        self._identity = None
        self._identity_checked_at = None
        self._poll_checked_at = None
        self._closed = False
        self._loop = None
        self._calls = set()
        self._poll_calls = set()
        self._close_task = None
        self._polling = False

    @classmethod
    def _for_test(cls, token, *, api_server_factory, request_timeout=5.0):
        """Private injection for synthetic local HTTP tests, never native input."""
        value = cls(token, request_timeout=request_timeout)
        failed = False
        try:
            value._session.api = api_server_factory()
        except Exception:
            failed = True
        if failed:
            raise BotApiUnavailable()
        return value

    def __repr__(self):
        return "<PrivateBotApiTransport>"

    @property
    def identity(self) -> BotIdentity | None:
        return self._identity

    @property
    def identity_checked_at(self) -> datetime | None:
        return self._identity_checked_at

    @property
    def poll_checked_at(self) -> datetime | None:
        return self._poll_checked_at

    @property
    def bot(self) -> Bot:
        """Borrowed SDK instance; this transport remains its session owner."""
        self._admit()
        return self._bot

    def _admit(self):
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if loop is None:
            raise BotApiUnavailable()
        if self._closed or (self._loop is not None and loop is not self._loop):
            raise BotApiUnavailable()
        self._loop = loop

    def _invalidate(self, operation, code):
        if operation == "identity" or code == "bot_token_revoked":
            self._identity = self._identity_checked_at = None
        if operation == "poll" or code in {"bot_token_revoked", "bot_polling_conflict"}:
            self._poll_checked_at = None

    def _call_finished(self, call):
        self._calls.discard(call)
        self._poll_calls.discard(call)
        if not call.cancelled():
            # Retrieve a late failure even when its original caller timed out;
            # asyncio must never log a raw token-bearing SDK exception.
            call.exception()

    async def _request(self, method, operation):
        self._admit()
        call = asyncio.create_task(self._bot(method, request_timeout=self._timeout))
        self._calls.add(call)
        if operation == "poll":
            self._poll_calls.add(call)
        call.add_done_callback(self._call_finished)
        code = None
        result = None
        try:
            completed, _pending = await asyncio.wait({call}, timeout=self._timeout)
            if not completed:
                call.cancel()
                code = "bot_timeout"
            else:
                result = call.result()
        except BotApiUnavailable as error:
            code = error.code
        except TelegramUnauthorizedError:
            code = "bot_token_revoked"
        except TelegramConflictError:
            code = "bot_polling_conflict"
        except TimeoutError:
            code = "bot_timeout"
        except TelegramNetworkError as error:
            code = "bot_timeout" if isinstance(error.__cause__, TimeoutError) else "bot_unavailable"
        except asyncio.CancelledError:
            call.cancel()
            self._invalidate(operation, "bot_unavailable")
            raise
        except Exception:
            code = "bot_unavailable"
        if code is not None:
            self._invalidate(operation, code)
            # Outside the SDK except suite: no raw exception cause/context.
            raise BotApiUnavailable(code)
        return result

    async def verify_identity(self):
        user = await self._request(GetMe(), "identity")
        self._admit()
        self._identity = BotIdentity(user.id, user.username)
        self._identity_checked_at = datetime.now(UTC)
        return self._identity

    async def poll_once(self, cursor=None, *, allowed_updates=("message",)):
        self._admit()
        if (
            type(allowed_updates) is not tuple or len(allowed_updates) not in {1, 2}
            or any(type(value) is not str for value in allowed_updates)
            or allowed_updates not in (("message",), ("message", "callback_query"))
        ):
            self._invalidate("poll", "bot_unavailable")
            raise BotApiUnavailable()
        if cursor is not None and (type(cursor) is not int or cursor < 0):
            self._invalidate("poll", "bot_unavailable")
            raise BotApiUnavailable()
        if self._polling or self._poll_calls:
            self._invalidate("poll", "bot_polling_conflict")
            raise BotApiUnavailable("bot_polling_conflict")
        self._polling = True
        try:
            updates = await self._request(GetUpdates(
                offset=cursor, timeout=0, limit=100, allowed_updates=list(allowed_updates),
            ), "poll")
            self._admit()
            if not isinstance(updates, list) or any(not isinstance(value, Update) for value in updates):
                self._invalidate("poll", "bot_unavailable")
                raise BotApiUnavailable()
            self._poll_checked_at = datetime.now(UTC)
            return updates
        finally:
            self._polling = False

    async def _drain(self):
        calls = tuple(self._calls)
        for call in calls:
            call.cancel()
        await asyncio.gather(*calls, return_exceptions=True)
        await self._session.close()

    async def close(self):
        if self._loop is not None and asyncio.get_running_loop() is not self._loop:
            raise BotApiUnavailable()
        self._closed = True
        self._identity = self._identity_checked_at = self._poll_checked_at = None
        if self._close_task is None or (
            self._close_task.done() and not self._close_task.cancelled()
            and self._close_task.exception() is not None
        ):
            # Retry only a definitively failed cleanup. A running/uncertain
            # drain remains owned; no Telegram request is retried here.
            self._close_task = asyncio.create_task(self._drain())
        code = None
        try:
            await asyncio.wait_for(asyncio.shield(self._close_task), 5)
        except TimeoutError:
            code = "bot_timeout"
        except Exception:
            code = "bot_unavailable"
        if code is not None:
            raise BotApiUnavailable(code)
