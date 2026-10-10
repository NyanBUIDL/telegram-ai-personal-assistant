"""Application-owned first-source intent; no learning or answer evidence yet."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError, field_validator
from sqlalchemy import String, cast, insert, select, update

from ..contracts import ContractModel, FirstSourceStatus, Identifier, NonNegativeInt
from ..db.models import AppSetting


class _SelectionHeader(ContractModel):
    schema_version: Literal[1]
    namespace: Identifier
    revision: NonNegativeInt
    selection_generation: NonNegativeInt
    restore_epoch: Identifier

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("first_value_state_invalid")
        return value


class FirstValueService:
    def __init__(self, *, runtime, coordinator, windows_sid: str):
        if not isinstance(windows_sid, str) or not windows_sid or len(windows_sid) > 184:
            raise ValueError("first_value_binding_invalid")
        self.runtime, self.coordinator = runtime, coordinator
        self._bot = runtime.bot_runtime
        self._engine, self._fence = coordinator.engine, coordinator.fence
        self._profile_id = coordinator.profile_id
        self._namespace = hashlib.sha256(json.dumps(
            [self._profile_id, windows_sid], separators=(",", ":")
        ).encode()).hexdigest()
        self._key = "fv1." + self._namespace
        self._closing = False
        self._selection_tasks: set[asyncio.Task] = set()
        from .first_source_preview import FirstSourcePreviewService
        self.preview = FirstSourcePreviewService(self)

    def _admit(self):
        try:
            current = (
                not self._closing and self.runtime.first_value is self
                and self.runtime.bot_runtime is self._bot and self._bot._runtime is self.runtime
                and self.runtime.settings.profile_id == self._profile_id
                and self.coordinator.profile_id == self._profile_id
                and self.coordinator.engine is self._engine and self.coordinator.fence is self._fence
                and self._bot.engine is self._engine and self._bot.fence is self._fence
                and self.runtime.management_admitted() is True
            )
        except Exception:
            current = False
        if not current:
            raise PermissionError("owner_pairing_required")

    def _read_header(self, connection):
        row = connection.execute(select(cast(AppSetting.value, String)).where(AppSetting.key == self._key)).first()
        if row is None:
            return None
        try:
            raw = row[0]
            if not isinstance(raw, str) or len(raw.encode()) > 8192:
                raise ValueError()
            header = _SelectionHeader.model_validate_json(raw)
            if header.namespace != self._namespace:
                raise ValueError()
            return header
        except (ValidationError, ValueError, TypeError, RecursionError, OverflowError):
            raise ValueError("first_value_state_invalid") from None

    def _select_source(self, chat_id: int) -> None:
        self._admit()

        def metadata(connection):
            self._admit()
            prior = self.coordinator._read(connection)[0].options.source_id
            header = self._read_header(connection)
            original = header
            if header is None:
                header = _SelectionHeader(schema_version=1, namespace=self._namespace, revision=0,
                    selection_generation=0, restore_epoch=uuid4().hex)
            updated = header.model_copy(update={"revision": header.revision + 1,
                "selection_generation": header.selection_generation + int(prior != chat_id)})
            values = updated.model_dump(mode="json")
            if original is None:
                connection.execute(insert(AppSetting).values(key=self._key, value=values))
            else:
                result = connection.execute(update(AppSetting).where(
                    AppSetting.key == self._key,
                    AppSetting.value["revision"].as_integer() == original.revision,
                ).values(value=values))
                if result.rowcount != 1:
                    raise ValueError("first_value_write_conflict")
            self._admit()

        self.coordinator.save_source_selection(chat_id, transaction_update=metadata)

    async def select_source(self, chat_id: int) -> None:
        self._admit()
        if type(chat_id) is not int or chat_id == 0:
            raise ValueError("source_selection_invalid")
        operation = asyncio.create_task(asyncio.to_thread(self._select_source, chat_id))
        self._selection_tasks.add(operation)
        cancelled = False
        try:
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            if cancelled:
                # Consume a late failure while preserving the caller's cancellation.
                if not operation.cancelled():
                    operation.exception()
                raise asyncio.CancelledError
            operation.result()
        finally:
            if operation.done():
                self._selection_tasks.discard(operation)

    def selection_status(self) -> FirstSourceStatus:
        self._admit()
        with self._engine.connect() as connection:
            self._read_header(connection)
            source = self.coordinator._read(connection)[0].options.source_id
        identity = self._bot.service.verified_identity
        username = identity.username if identity is not None else None
        if not isinstance(username, str) or re.fullmatch(r"[A-Za-z0-9_]{1,32}", username) is None:
            username = None
        self._admit()
        return FirstSourceStatus(profile_id=self._profile_id, source_id=source,
            learning_operation=None, answer_operation=None, bot_username=username, test_available=False,
            code="source_selected" if source is not None else "source_required",
            message="Đã lưu nguồn đã chọn." if source is not None else "Chọn một nguồn Telegram.",
            next_action="Mở quản lý nguồn để kiểm tra quyền và dữ liệu sẽ dùng.")

    async def close(self) -> None:
        self._closing = True
        cancelled = False
        while self._selection_tasks:
            operations = tuple(self._selection_tasks)
            drain = asyncio.gather(*operations, return_exceptions=True)
            while not drain.done():
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError:
                    cancelled = True
            drain.result()
            self._selection_tasks.difference_update(task for task in operations if task.done())
        if cancelled:
            raise asyncio.CancelledError
