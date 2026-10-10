"""Owner-only portable backups; browser input never selects filesystem paths."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import Depends, HTTPException, Request

from ..contracts import BackupManifest, PublicProfile
from ..security import SecretStore
from ..services.maintenance import MaintenanceBusy
from ..services.storage import StorageService

ARCHIVE_NAME = re.compile(r"^(?:backup|before-restore)-[a-f0-9]{32}\.zip$")


def install_backup_routes(app, context, require_session, require_write_session):
    pending = threading.Lock()

    def create_backup():
        if not pending.acquire(blocking=False):
            raise HTTPException(409, detail={"code": "backup_in_progress"})
        storage = None
        try:
            settings = context.settings_getter()
            storage = StorageService(settings, SecretStore())
            database = storage.open(
                PublicProfile(
                    profile_id=settings.profile_id,
                    owner_id=None,
                    storage_backend=settings.storage_backend,
                    setup_stage="storage_ready",
                    version=1,
                )
            )
            asyncio.run(database.close())
            archive_id = "backup-" + uuid4().hex
            manifest = storage.backup(settings.data_dir / "backups" / (archive_id + ".zip"))
            return {"id": archive_id, "manifest": manifest.model_dump(mode="json")}
        except MaintenanceBusy:
            raise HTTPException(503, detail={"code": "maintenance_in_progress"}) from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, detail={"code": "backup_failed"}) from None
        finally:
            if storage is not None and storage.fence is not None:
                storage.fence.close()
            pending.release()

    def list_backups():
        settings = context.settings_getter()
        root = settings.data_dir / "backups"
        items = []
        if not root.is_dir() or root.is_symlink() or root.is_junction():
            return {"items": items}
        for path in sorted(root.glob("*.zip"), key=lambda path: path.name, reverse=True)[:100]:
            if not ARCHIVE_NAME.fullmatch(path.name):
                continue
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                    continue
                with ZipFile(path) as archive:
                    members = archive.infolist()
                    expected = (
                        "database.sqlite3"
                        if settings.storage_backend == "sqlite"
                        else "database.json"
                    )
                    if len(members) != 2 or {member.filename for member in members} != {
                        "manifest.json",
                        expected,
                    }:
                        continue
                    member = archive.getinfo("manifest.json")
                    if member.file_size > 65536:
                        continue
                    manifest = BackupManifest.model_validate_json(archive.read(member))
                    if (
                        manifest.profile_id != settings.profile_id
                        or manifest.backend.value != settings.storage_backend
                        or set(manifest.checksums) != {expected}
                    ):
                        continue
                items.append(
                    {
                        "id": path.stem,
                        "size_bytes": path.stat().st_size,
                        "manifest": manifest.model_dump(mode="json"),
                        "validation_state": "manifest_only",
                    }
                )
            except (OSError, ValueError, BadZipFile, KeyError, RuntimeError):
                continue
        return {"items": items}

    @app.get("/api/v1/backups")
    async def backups(_session=Depends(require_session)):
        return await asyncio.to_thread(list_backups)

    @app.post("/api/v1/backups", status_code=201)
    async def backup(request: Request, _session=Depends(require_write_session)):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 1024:
                raise HTTPException(400, detail={"code": "backup_request_invalid"})
        try:
            body = json.loads(data) if data else {}
        except (ValueError, RecursionError):
            raise HTTPException(400, detail={"code": "backup_request_invalid"}) from None
        if type(body) is not dict or body:
            raise HTTPException(400, detail={"code": "backup_request_invalid"})
        return await asyncio.to_thread(create_backup)
