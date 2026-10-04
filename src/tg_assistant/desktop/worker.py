"""Headless native worker: setup is available before Telegram credentials."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from uuid import UUID, uuid4

import psutil
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import select
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..config import get_settings
from ..contracts import ConnectionStatus, PublicProfile
from ..db.models import TelegramAccount
from ..paths import ensure_runtime_dirs, resource_path
from ..services.maintenance import MaintenanceService
from ..services.storage import StorageService
from .instance import AlreadyRunning, InstanceGuard, secure_tree


def reserve_loopback(port):
    endpoint = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        endpoint.bind(("127.0.0.1", port))
    except OSError:
        endpoint.close()
        endpoint = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        endpoint.bind(("127.0.0.1", 0))
    endpoint.listen(128)
    endpoint.setblocking(False)
    return endpoint


class RuntimeGateway:
    def __init__(self, port, run_id):
        self.origin = f"http://127.0.0.1:{port}"
        self.host = f"127.0.0.1:{port}"
        self.admin = None
        self.code = "runtime_ready"
        tokens = json.loads(
            files("tg_assistant.desktop").joinpath("design_tokens.json").read_text(encoding="utf-8")
        )
        setup = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        setup.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

        @setup.middleware("http")
        async def boundary(request: Request, call_next):
            origin = request.headers.get("origin")
            if origin and origin != f"http://127.0.0.1:{port}":
                return JSONResponse({"code": "origin_denied"}, status_code=403)
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        @setup.get("/api/v1/runtime/readiness")
        async def readiness(response: Response):
            response.headers["X-TG-Runtime-ID"] = run_id
            return ConnectionStatus(
                service="runtime",
                state="ready",
                checked_at=datetime.now(UTC),
                code=self.code,
                message="Ứng dụng cục bộ đang chạy.",
                next_action=None,
                capabilities=["management"] if self.admin else ["setup"],
            ).model_dump(mode="json")

        @setup.get("/")
        async def welcome():
            colors = tokens["colors"]
            return HTMLResponse(
                '<!doctype html><html lang="vi"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                "<title>Telegram AI</title><style>"
                "@font-face{font-family:Darley;src:url('/assets/ui-font')}"
                "@font-face{font-family:Peter;src:url('/assets/title-font')}"
                f"body{{margin:0;background:{colors['paper']};color:{colors['ink']};font:18px Darley,sans-serif;padding:24px}}"
                f"main{{max-width:680px;margin:24px auto;padding:24px;background:{colors['panel']};border:3px solid {colors['ink']};box-shadow:7px 7px 0 {colors['ink']}}}"
                "h1{font:42px Peter,sans-serif;margin:0 0 24px}p{line-height:1.5}"
                "</style><body><main><h1>Telegram AI</h1>"
                "<p>Mở ứng dụng Windows để tiếp tục thiết lập kết nối.</p></main></body></html>",
                headers={
                    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; font-src 'self'; frame-ancestors 'none'"
                },
            )

        @setup.get("/assets/ui-font")
        async def ui_font():
            return FileResponse(
                resource_path("dashboard-prototype", "public", "fonts", tokens["fonts"]["ui"]),
                media_type="font/otf",
            )

        @setup.get("/assets/title-font")
        async def title_font():
            return FileResponse(
                resource_path("dashboard-prototype", "public", "fonts", tokens["fonts"]["display"]),
                media_type="font/otf",
            )

        self.setup = setup

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = [(key.lower(), value) for key, value in scope["headers"]]
            hosts = [value.decode("latin1") for key, value in headers if key == b"host"]
            origins = [value.decode("latin1") for key, value in headers if key == b"origin"]
            if hosts != [self.host] or (origins and origins != [self.origin]):
                response = JSONResponse(
                    {"code": "origin_denied"},
                    status_code=403,
                    headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
                )
                await response(scope, receive, send)
                return
        target = (
            self.setup
            if (scope["type"] == "lifespan" or scope.get("path") == "/api/v1/runtime/readiness")
            else self.admin or self.setup
        )
        await target(scope, receive, send)


def write_state(path, state):
    temporary = path.with_name(".desktop-state-" + uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(state, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


async def _serve(settings, paths, database, listener):
    port = listener.getsockname()[1]
    state_file = paths["config"] / "desktop-runtime.json"
    stop_file = paths["data"] / "stop.request"
    launch_id = os.environ.get("TG_ASSISTANT_DESKTOP_RUN_ID")
    # A nonsecret incarnation ID survives Windows venv launcher indirection.
    # It is lifecycle correlation, never dashboard authorization.
    if launch_id is None:
        launch_id = uuid4().hex
    elif UUID(launch_id).hex != launch_id:
        raise ValueError("runtime_start_failed")
    gateway = RuntimeGateway(port, launch_id)
    state = dict(
        profile_id=settings.profile_id,
        pid=os.getpid(),
        process_started_at=psutil.Process().create_time(),
        port=port,
        run_id=launch_id,
        mode="setup",
        owner_id=None,
    )
    stop_file.unlink(missing_ok=True)
    await database.ping()
    server = uvicorn.Server(
        uvicorn.Config(
            gateway, access_log=False, log_config=None, server_header=False, lifespan="on"
        )
    )
    server.install_signal_handlers = lambda: None
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    assistant_task = None
    try:
        while not server.started and not server_task.done():
            await asyncio.sleep(0.01)
        if server_task.done():
            await server_task
            raise RuntimeError("runtime_start_failed")
        write_state(state_file, state)
        async with database.session() as session:
            account = await session.scalar(select(TelegramAccount).limit(1))
        if (
            account
            and account.is_owner_paired
            and (paths["sessions"] / "account.session.enc").exists()
        ):
            # Runtime integration is serialized by the coordinator. The callback
            # installs the existing authenticated API after actual owner checks.
            async def assistant():
                from ..runtime import run_application

                def attach(admin):
                    gateway.admin = admin
                    state.update(mode="assistant", owner_id=str(admin.state.admin_context.owner_id))
                    write_state(state_file, state)

                try:
                    await run_application(
                        settings=settings.model_copy(update={"admin_api_port": port}),
                        admin_app_ready=attach,
                    )
                except Exception:
                    gateway.code = "telegram_reconnect_required"
                finally:
                    # The API is owned by the assistant's resources. Never keep
                    # management/readiness attached after that lifecycle ends.
                    gateway.admin = None
                    gateway.code = "telegram_reconnect_required"
                    state.update(mode="setup", owner_id=None)
                    write_state(state_file, state)

            assistant_task = asyncio.create_task(assistant())
        while not server_task.done():
            if stop_file.exists() and stop_file.read_text(encoding="ascii") == state["run_id"]:
                break
            await asyncio.sleep(0.1)
    finally:
        if assistant_task:
            assistant_task.cancel()
            await asyncio.gather(assistant_task, return_exceptions=True)
        server.should_exit = True
        await server_task
        if state_file.exists():
            current = json.loads(state_file.read_text(encoding="utf-8"))
            if current.get("run_id") == state["run_id"]:
                state_file.unlink(missing_ok=True)
                stop_file.unlink(missing_ok=True)


def serve_worker(settings=None, *, instance_directory: Path | None = None):
    settings = settings or get_settings()
    try:
        with InstanceGuard(instance_directory):
            paths = ensure_runtime_dirs(settings.data_dir, profile_id=settings.profile_id)
            maintenance = MaintenanceService(paths["config"], profile_id=settings.profile_id)
            with maintenance.operation():
                secure_tree(paths["data"])
            # Fresh SQLite does not construct or access the credential store.
            store = None
            if settings.storage_backend == "mysql":
                from ..security import SecretStore

                store = SecretStore()
            storage = StorageService(settings, store)
            database = storage.open(
                PublicProfile(
                    profile_id=settings.profile_id,
                    owner_id=None,
                    storage_backend=settings.storage_backend,
                    setup_stage="welcome",
                    version=1,
                )
            )
            try:
                storage.migrate()
                with maintenance.operation(), reserve_loopback(settings.admin_api_port) as listener:

                    async def lifecycle():
                        try:
                            await _serve(settings, paths, database, listener)
                        finally:
                            await database.close()

                    asyncio.run(lifecycle())
            finally:
                asyncio.run(database.close())
    except AlreadyRunning:
        raise SystemExit(17) from None
