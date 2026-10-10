"""One-source, owner-issued permission preview; restart requires a new preview."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

from sqlalchemy import event, select, text

from ..config import Settings
from ..db.models import PermissionName, TelegramChat, TelegramChatPermission, TelegramChatPolicy
from ..policy import PERMISSION_TEMPLATES
from .actions import PendingActionService
from .provider_connections import ModelSelection
from .revocation import AuthorizationRevoked


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class _Capture:
    action_id: str
    owner_id: int
    source_id: int
    payload: str
    snapshot: str
    owners: tuple
    expiry: datetime
    deadline: float


class FirstSourcePreviewService:
    def __init__(self, owner):
        self.owner = owner
        self._captures: dict[str, _Capture] = {}

    def _identity(self):
        owner, runtime = self.owner, self.owner.runtime
        owner._admit()
        pair = owner._bot.pairing_verification()
        actor = runtime.user.owner_id
        if (pair is None or type(actor) is not int or actor <= 0
                or pair.owner_id != actor or not pair.fingerprint):
            raise AuthorizationRevoked("first_source_preview_stale")
        settings = runtime.settings
        # Read the native owner's current nonsecret settings, never browser input.
        disk = Settings(_env_file=None, data_dir=settings.data_dir, profile_id=settings.profile_id)
        fields = ("ai_provider", "embedding_provider", "cloud_consent", "enable_embeddings",
                  "openai_base_url", "openrouter_base_url", "ollama_base_url",
                  "openai_primary_model", "openrouter_primary_model", "ollama_primary_model",
                  "openai_embedding_model", "openrouter_embedding_model", "ollama_embedding_model",
                  "embedding_version", "cloud_embedding_dimension", "ollama_vector_size")
        router = runtime.ai_router
        engines = []
        pointers = [settings, runtime.database, runtime.policy, router, runtime.ai, runtime.embedding_ai]
        for key, engine in sorted(router.engines.items()):
            client = engine.client
            configured = ModelSelection.parse(engine.provider, {
                "model": engine.model, "endpoint": str(client.base_url) if client else {
                    "ollama": settings.ollama_base_url, "openai": settings.openai_base_url,
                    "openrouter": settings.openrouter_base_url}[engine.provider],
                "cloud_consent": engine.cloud_consent,
            })
            engines.append((key, engine.provider, configured.model, configured.endpoint_id,
                            client is not None, engine.cloud_consent))
            pointers.extend((engine, client))
        embedding = runtime.embedding_ai
        client = embedding.client
        configured = ModelSelection.parse(embedding.provider, {
            "service": "embeddings", "model": embedding.embedding_model,
            "endpoint": str(client.base_url) if client else {
                "ollama": settings.ollama_base_url, "openai": settings.openai_base_url,
                "openrouter": settings.openrouter_base_url}[embedding.provider],
            "cloud_consent": embedding.cloud_consent,
        }) if embedding.provider != "off" else None
        pointers.append(client)
        identity = [actor, pair.fingerprint, settings.profile_id,
                    {key: getattr(settings, key) for key in fields},
                    {key: getattr(disk, key) for key in fields},
                    router.default_provider, router.enabled, router.cloud_consent, engines,
                    [embedding.provider, configured.model if configured else embedding.embedding_model,
                     configured.endpoint_id if configured else "off",
                     embedding.embedding_dimension, embedding.cloud_consent, client is not None],
                    settings.embedding_profile.model_dump(mode="json")]
        return identity, tuple(pointers)

    def _selection(self, connection):
        owner = self.owner
        header = owner._read_header(connection)
        if header is None:
            raise AuthorizationRevoked("first_source_preview_stale")
        source = owner.coordinator._read(connection)[0].options.source_id
        return [source, header.selection_generation, header.restore_epoch, owner._profile_id]

    async def _snapshot(self, session, chat_id):
        owner = self.owner
        owner._admit()
        if (session.bind.dialect.name != "sqlite" or
                Path(session.bind.url.database).resolve() != Path(owner._engine.url.database).resolve()):
            raise AuthorizationRevoked("first_source_preview_stale")
        connection = await session.connection()
        active = await connection.run_sync(lambda conn: conn.connection.driver_connection.in_transaction)
        if not active:
            await session.execute(text("BEGIN IMMEDIATE"))
        selected = await session.run_sync(lambda sync: self._selection(sync.connection()))
        if type(chat_id) is not int or selected[0] != chat_id:
            raise AuthorizationRevoked("first_source_preview_stale")
        chat_type = await session.scalar(select(TelegramChat.chat_type).where(TelegramChat.chat_id == chat_id))
        if chat_type not in {"group", "supergroup", "channel"}:
            raise AuthorizationRevoked("first_source_preview_stale")
        columns = [column for column in TelegramChatPolicy.__table__.columns
                   if column.name not in {"id", "created_at", "updated_at"}]
        policy = (await session.execute(select(*columns).where(TelegramChatPolicy.chat_id == chat_id))).first()
        values = dict(zip((column.name for column in columns), policy, strict=True)) if policy else {}
        if values.get("revoked_at") is not None:
            values["revoked_at"] = _utc(values["revoked_at"]).isoformat()
        permissions = (await session.execute(select(TelegramChatPermission.permission, TelegramChatPermission.enabled)
                       .where(TelegramChatPermission.chat_id == chat_id).order_by(TelegramChatPermission.permission))).all()
        identity, pointers = self._identity()
        route = await owner.runtime.policy.ai_route(session, [chat_id])
        plan = owner.runtime.ai_router.plan(route)
        owner._admit()
        return _json([selected, chat_type, values, [list(row) for row in permissions], identity, plan]), pointers, values, permissions, plan

    async def create(self, session, chat_id: int, limit: int):
        if type(limit) is not int or limit != 1000:
            raise ValueError("first_source_preview_invalid")
        now, tick = datetime.now(UTC), monotonic()
        self._captures = {key: capture for key, capture in self._captures.items()
                          if capture.expiry > now and capture.deadline > tick}
        if len(self._captures) >= 128:
            raise ValueError("first_source_preview_full")
        snapshot, pointers, policy, permissions, plan = await self._snapshot(session, chat_id)
        if len(self._captures) >= 128:
            raise ValueError("first_source_preview_full")
        reference = uuid4().hex
        actor = self.owner.runtime.user.owner_id
        action = await PendingActionService(ttl_seconds=min(300, max(1, self.owner.runtime.settings.confirmation_ttl_seconds))).create(
            session, action_type="enable_group_learning", requested_by=actor, chat_id=chat_id,
            payload={"first_source_preview": reference, "limit": 1000}, preview="Đang tạo bản xem trước.")
        action.action_id = "fv1-" + uuid4().hex
        enabled = {name for name, on in permissions if on}
        target = {permission.value for permission in PERMISSION_TEMPLATES["knowledge"]}
        removed = enabled - target - {PermissionName.GROUP_AI_ASK.value}
        models = ", ".join(f"{provider}/{self.owner.runtime.ai_router.engines[provider].model}" for provider in plan) or "chưa có tuyến chat khả dụng"
        embedding = self.owner.runtime.embedding_ai
        expiry = _utc(action.expires_at)
        identity, _ = self._identity()
        consent = self.owner.runtime.settings.cloud_consent and identity[4]["cloud_consent"]
        action.preview = (
            f"Nguồn {chat_id}; chỉ nguồn này được ALLOW và áp dụng mẫu knowledge sau xác nhận. "
            f"Cấp/bảo đảm quyền: {', '.join(sorted(target))}. "
            f"Gỡ quyền đang bật: {', '.join(sorted(removed)) or 'không có'}. "
            f"Giữ group_ai_ask: {'bật' if 'group_ai_ask' in enabled else 'tắt'}. "
            "Đồng bộ tối đa 1000 tin lịch sử, làm sạch/lập chỉ mục; monitor_new_messages và "
            "auto_knowledge tiếp tục xử lý tin mới theo retention/quota hiện tại. "
            f"Chế độ AI: {policy.get('ai_mode', 'inherit')}; tuyến chat cấu hình: {models}; "
            f"embedding cấu hình: {embedding.provider}/{embedding.embedding_model}. "
            f"Cloud consent hiện tại: {'đã đồng ý' if consent else 'chưa đồng ý'}. "
            "Chỉ tuyến cloud được cho phép mới có thể gửi nội dung nguồn để tạo embedding/trả lời; "
            "local_only cấm nội dung nguồn lên cloud. Bản xem trước không cấp consent, "
            "không chứng minh model đã suy luận thành công. "
            f"Hết hạn gốc (UTC): {expiry.isoformat()}."
        )
        capture = _Capture(action.action_id, actor, chat_id, _json(action.payload), snapshot,
                           pointers, expiry, tick + max(0, (expiry - now).total_seconds()))
        self._captures[reference] = capture
        await self.validate(session, action)
        await session.flush()
        self.owner._admit()
        return action

    async def validate(self, session, action):
        self.owner._admit()
        reference = (action.payload or {}).get("first_source_preview")
        capture = self._captures.get(reference) if isinstance(reference, str) else None
        if (capture is None or action.action_id != capture.action_id
                or action.action_type != "enable_group_learning" or action.chat_id != capture.source_id
                or action.requested_by != capture.owner_id or _json(action.payload) != capture.payload
                or _utc(action.expires_at) != capture.expiry or datetime.now(UTC) >= capture.expiry
                or monotonic() >= capture.deadline):
            raise AuthorizationRevoked("first_source_preview_stale")
        snapshot, pointers, *_ = await self._snapshot(session, capture.source_id)
        if snapshot != capture.snapshot or any(a is not b for a, b in zip(pointers, capture.owners, strict=False)) or len(pointers) != len(capture.owners):
            raise AuthorizationRevoked("first_source_preview_stale")
        identity = _json(self._identity()[0])
        selection = await session.run_sync(lambda sync: self._selection(sync.connection()))

        active = [True]

        def final_admission(sync, *_):
            if not active[0]:
                return
            current, pointers = self._identity()
            if (_json(current) != identity or self._selection(sync.connection()) != selection
                    or datetime.now(UTC) >= capture.expiry or monotonic() >= capture.deadline
                    or len(pointers) != len(capture.owners)
                    or any(a is not b for a, b in zip(pointers, capture.owners, strict=False))):
                raise AuthorizationRevoked("first_source_preview_stale")

        # SQL writer lock keeps selection/policy fixed while the existing preset mutates permissions.
        event.listen(session.sync_session, "before_commit", final_admission, once=True)
        event.listen(session.sync_session, "after_flush_postexec", final_admission)

        def release(*_):
            active[0] = False
            validated = session.sync_session.info.get("first_source_validated_actions", {})
            if validated.get(action.action_id) is capture:
                validated.pop(action.action_id)
            if not validated:
                session.sync_session.info.pop("first_source_validated_actions", None)

        event.listen(session.sync_session, "after_commit", release, once=True)
        event.listen(session.sync_session, "after_rollback", release, once=True)
        session.sync_session.info.setdefault("first_source_validated_actions", {})[action.action_id] = capture
