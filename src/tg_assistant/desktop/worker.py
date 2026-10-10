"""Headless native worker: setup is available before Telegram credentials."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
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

from ..admin_api.auth import DashboardTicketService, install_native_auth_routes
from ..config import get_settings, validate_settings
from ..contracts import ConnectionService, ConnectionStatus, PublicProfile
from ..db.models import TelegramAccount
from ..paths import current_user_sid, ensure_runtime_dirs, resource_path
from ..services.maintenance import MaintenanceService
from ..services.storage import StorageService
from .instance import AlreadyRunning, InstanceGuard, secure_tree
from .ipc import NativePipeServer


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
    def __init__(self, port, run_id, *, profile_id="default", setup_context=None,
                 dashboard_root=None):
        self.origin = f"http://127.0.0.1:{port}"
        self.host = f"127.0.0.1:{port}"
        self._admin = None
        self.runtime_owner = None
        self.tickets = DashboardTicketService(profile_id=profile_id, windows_sid=current_user_sid(), origin=self.origin)
        self.tickets.before_authority = self.before_dashboard_ticket
        self.authentication = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.native_relay = None
        if setup_context is not None and setup_context.fence.profile_id == profile_id:
            from ..services.native_dialogs import NativeDialogRelay

            self.native_relay = NativeDialogRelay(
                profile_id=profile_id, fence=setup_context.fence
            )
        install_native_auth_routes(
            self.authentication, self.tickets,
            session_command_handler=self.native_submit if self.native_relay else None,
            dialog_availability=lambda: (
                self.native_relay.available_commands() if self.native_relay else ()
            ),
        )
        self.code = "runtime_ready"
        tokens = json.loads(
            files("tg_assistant.desktop").joinpath("design_tokens.json").read_text(encoding="utf-8")
        )
        setup = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        from ..admin_api.setup_dashboard import SetupDashboard

        dashboard = SetupDashboard(dashboard_root) if dashboard_root is not None else None
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
            if dashboard is not None:
                built = dashboard.index()
                if built is not None:
                    return built
            colors = tokens["colors"]
            bootstrap = resource_path("dashboard-prototype", "public", "auth-bootstrap.js").read_text(encoding="utf-8")
            bootstrap_hash = base64.b64encode(hashlib.sha256(bootstrap.encode()).digest()).decode()
            return HTMLResponse(
                '<!doctype html><html lang="vi"><meta charset="utf-8">'
                f"<script>{bootstrap}</script>"
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
                    "Content-Security-Policy": f"default-src 'none'; script-src 'sha256-{bootstrap_hash}'; connect-src 'self'; style-src 'unsafe-inline'; font-src 'self'; frame-ancestors 'none'"
                },
            )

        @setup.get("/auth-bootstrap.js")
        async def bootstrap():
            return FileResponse(resource_path("dashboard-prototype", "public", "auth-bootstrap.js"), media_type="text/javascript")

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

        if dashboard is not None:
            @setup.get("/assets/{path:path}")
            async def built_assets(path: str):
                return dashboard.asset("assets/" + path)

            @setup.get("/fonts/{path:path}")
            async def built_fonts(path: str):
                return dashboard.asset("fonts/" + path)

        self.setup = setup
        if setup_context is not None:
            from ..admin_api.setup import install_setup_routes

            install_setup_routes(setup, lambda: setup_context.coordinator)

    @property
    def admin(self):
        owner = self.runtime_owner
        admitted = False
        if self._admin is not None and owner is not None:
            try:
                admitted = (
                    not owner._closed and getattr(owner, "_close_task", None) is None
                    and not owner.stopping.is_set()
                    and owner.management_admitted() is True
                )
            except Exception:
                admitted = False
        if self._admin is not None and not admitted:
            # Withdraw management at the request boundary, without waiting for
            # a 15-second health tick or slow SDK/resource shutdown.
            self.tickets.invalidate()
            self.tickets.set_verified_owner(None)
            self._admin = None
        return self._admin

    def before_dashboard_ticket(self):
        # The SID-authenticated pipe issues tickets without traversing HTTP.
        # Reconcile the same current admission before capturing ticket authority.
        _ = self.admin

    def native_submit(self, command, session):
        if self.native_relay is None:
            return {"code": "native_dialog_unavailable"}
        return self.native_relay.submit(
            command,
            authorized=lambda: (
                session.profile_id == self.tickets.profile_id
                and self.tickets.get_session(session.token) is session
            ),
        ).model_dump(mode="json")

    def native_claim(self, command):
        """Private poll, called only after NativePipeServer's actual peer check."""
        from ..contracts import NativeCommand

        try:
            command = NativeCommand.model_validate_json(command.model_dump_json())
            payload = command.payload_nonsecret
            names = payload.get("available")
            if (
                command.profile_id != self.tickets.profile_id
                or command.name.value != "open_connection_dialog"
                or set(payload) != {"delivery", "available"}
                or payload["delivery"] != "claim"
                or not isinstance(names, list)
                or len(names) > 3
                or any(type(name) is not str for name in names)
                or len(set(names)) != len(names)
                or not set(names) <= {
                    "open_connection_dialog", "open_telegram_login", "open_bot_dialog"
                }
            ):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise PermissionError("native_command_denied") from None
        selected = self.native_relay.claim(names) if self.native_relay else None
        return {"command": selected.model_dump(mode="json") if selected else None}

    @admin.setter
    def admin(self, value):
        self.tickets.invalidate()
        owner = value.state.admin_context.owner_id if value is not None else None
        self.tickets.set_verified_owner(owner)
        if value is not None:
            value.state.admin_auth = self.tickets
        self._admin = value

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # Also apply lifecycle withdrawal before ticket/session endpoints.
            # Those requests must not redeem stale management during shutdown.
            _ = self.admin
            headers = [(key.lower(), value) for key, value in scope["headers"]]
            hosts = [value.decode("latin1") for key, value in headers if key == b"host"]
            origins = [value.decode("latin1") for key, value in headers if key == b"origin"]
            if hosts != [self.host] or (origins and origins != [self.origin]) or (
                scope.get("method") not in {"GET", "HEAD", "OPTIONS"} and origins != [self.origin]
            ):
                response = JSONResponse(
                    {"code": "origin_denied"},
                    status_code=403,
                    headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
                )
                await response(scope, receive, send)
                return
        if scope.get("path", "").startswith("/api/v1/auth/") or scope.get("path") in {
            "/api/v1/native/commands", "/api/v1/native/dialogs"
        }:
            await self.authentication(scope, receive, send)
            return
        target = (
            self.setup
            if (scope["type"] == "lifespan" or scope.get("path") in {
                "/api/v1/runtime/readiness", "/api/v1/setup/status", "/api/v1/connections",
            })
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


async def maintain_setup_health(coordinator, *, interval=15, refresh_handler=None):
    """Refresh actual owning-service proof; drain in-flight work on shutdown."""
    while True:
        await asyncio.sleep(interval)
        operation = asyncio.create_task(
            refresh_handler() if refresh_handler is not None
            else asyncio.to_thread(coordinator.resume)
        )
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            # Cancelling to_thread cannot stop its running function. Keep its
            # engine/fence alive until it finishes before releasing this owner.
            await asyncio.gather(operation, return_exceptions=True)
            raise
        except Exception:
            # Probes sanitize errors and stale measurements expire naturally.
            # A transient reconciliation/storage error must not stop refresh.
            logging.getLogger(__name__).warning("setup_health_refresh_unavailable")


async def drain_runtime_owner(runtime, *, on_pending=None):
    """Retain the actual account/SDK owner until a close attempt succeeds.

    Worker cancellation cannot turn an uncertain drain into permission to
    release the surrounding native guard or setup storage. Only cleanup is
    retried here; external actions are never replayed.
    """
    cancelled = False
    while not runtime._resources_released:
        try:
            await runtime.close()
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            if on_pending is not None:
                on_pending()
            try:
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                cancelled = True
    if cancelled:
        raise asyncio.CancelledError


async def _serve(settings, paths, database, listener, *, setup_context=None, instance_guard=None):
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
    gateway = RuntimeGateway(
        port, launch_id, profile_id=settings.profile_id, setup_context=setup_context,
        dashboard_root=settings.resolved_dashboard_dist_path,
    )
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
    if setup_context is not None:
        from ..services.connections import HealthObservation

        def runtime_probe():
            return HealthObservation(
                state="ready" if server.started and not server_task.done() else "unknown",
                capabilities=("management",) if gateway.admin else ("setup",),
            )

        # Trusted worker registration precedes initial health measurement; this
        # callback observes the actual server, not a persisted success flag.
        setup_context.coordinator.connections._probes[ConnectionService.RUNTIME] = runtime_probe
    assistant_task = None
    health_task = None
    running = {}

    async def refresh_setup():
        runtime = running.get("runtime")
        if runtime is not None and (
            runtime._closed or getattr(runtime, "_close_task", None) is not None
            or runtime.stopping.is_set()
        ):
            # Existing owner resources are closing; do not rebuild clients or
            # renew readiness during that shutdown.
            if setup_context.telegram is not None:
                setup_context.telegram.close()
            return await asyncio.to_thread(setup_context.coordinator.status)
        if runtime is not None:
            from .provider_activation import reconcile_saved_provider_settings

            try:
                await reconcile_saved_provider_settings(runtime, fence=setup_context.fence)
            except (ValueError, RuntimeError):
                # Incompatible embedding identity remains visibly pending. No
                # corpus/model switch or success is inferred from saved config.
                logging.getLogger(__name__).warning("provider_activation_unavailable")
            if runtime.bot_runtime is not None:
                await runtime.bot_runtime.refresh(runtime)
            elif setup_context.telegram is not None:
                await setup_context.telegram.refresh(runtime)
            await asyncio.to_thread(
                setup_context.prepare_health, active_settings=runtime.settings,
                refresh_telegram=False,
            )
            if runtime.first_value is not None:
                await runtime.first_value.refresh_observation()
        else:
            await asyncio.to_thread(setup_context.prepare_health)
        await asyncio.to_thread(setup_context.coordinator.resume)
        return await asyncio.to_thread(setup_context.advance_verified)
    pipe = NativePipeServer(
        settings.profile_id, launch_id, gateway.tickets,
        command_handler=gateway.native_claim, before_ticket=gateway.before_dashboard_ticket,
    )
    try:
        pipe.__enter__()
        while not server.started and not server_task.done():
            await asyncio.sleep(0.01)
        if server_task.done():
            await server_task
            raise RuntimeError("runtime_start_failed")
        async with database.session() as session:
            account = await session.scalar(select(TelegramAccount).limit(1))
        paired = bool(account and account.is_owner_paired)
        if setup_context is not None:
            if not paired and (paths["sessions"] / "account.session.enc").is_file():
                from .telegram_context import open_worker_telegram_probe

                try:
                    probe = await asyncio.to_thread(
                        open_worker_telegram_probe, settings, engine=setup_context.engine,
                        fence=setup_context.fence, guard=instance_guard,
                    )
                    setup_context.install_telegram(probe)
                except Exception:
                    gateway.code = "telegram_reconnect_required"
            await refresh_setup()
            health_task = asyncio.create_task(maintain_setup_health(
                setup_context.coordinator, refresh_handler=refresh_setup,
            ))
        write_state(state_file, state)
        if (
            account
            and account.is_owner_paired
            and (paths["sessions"] / "account.session.enc").exists()
        ):
            # Runtime integration is serialized by the coordinator. The callback
            # installs the existing authenticated API after actual owner checks.
            async def assistant():
                from ..runtime import run_application

                owned = {}

                def attach(admin):
                    gateway.admin = admin
                    state.update(mode="assistant", owner_id=str(admin.state.admin_context.owner_id))
                    write_state(state_file, state)

                def attach_runtime(runtime):
                    gateway.runtime_owner = runtime
                    if setup_context is not None:
                        setup_context.install_telegram(runtime.bot_runtime.account_observation)
                        setup_context.install_bot(runtime.bot_runtime.setup_observation)
                        from ..services.first_value import FirstValueService

                        runtime.first_value = FirstValueService(
                            runtime=runtime, coordinator=setup_context.coordinator,
                            windows_sid=current_user_sid(),
                        )
                        runtime.first_value.initialize()
                    running["runtime"] = runtime

                def bot_runtime_factory(runtime):
                    from .runtime_bot_context import RuntimeBotContext

                    if setup_context is None or instance_guard is None:
                        raise RuntimeError("owner_pairing_required")
                    return RuntimeBotContext(
                        settings, engine=setup_context.engine, fence=setup_context.fence,
                        guard=instance_guard, runtime=runtime,
                    )

                try:
                    await run_application(
                        settings=settings.model_copy(update={"admin_api_port": port}),
                        admin_app_ready=attach,
                        runtime_ready=attach_runtime,
                        bot_runtime_factory=bot_runtime_factory,
                        runtime_owned=lambda runtime: owned.update(runtime=runtime),
                    )
                except Exception:
                    gateway.code = "telegram_reconnect_required"
                finally:
                    actual = owned.get("runtime")
                    if actual is not None:
                        def pending_cleanup():
                            gateway.admin = None
                            gateway.code = "runtime_shutdown_pending"

                        await drain_runtime_owner(actual, on_pending=pending_cleanup)
                    running.pop("runtime", None)
                    if setup_context is not None:
                        await asyncio.to_thread(setup_context.detach_telegram)
                    # The API is owned by the assistant's resources. Never keep
                    # management/readiness attached after that lifecycle ends.
                    gateway.admin = None
                    gateway.runtime_owner = None
                    gateway.code = "telegram_reconnect_required"
                    state.update(mode="setup", owner_id=None)
                    write_state(state_file, state)

            assistant_task = asyncio.create_task(assistant())
        while not server_task.done():
            if pipe.failed.is_set():
                gateway.admin = None
                gateway.code = "native_ipc_unavailable"
                break
            if stop_file.exists() and stop_file.read_text(encoding="ascii") == state["run_id"]:
                break
            await asyncio.sleep(0.1)
    finally:
        if gateway.native_relay is not None:
            gateway.native_relay.close()
        if health_task is not None:
            health_task.cancel()
            await asyncio.gather(health_task, return_exceptions=True)
        pipe.close()
        gateway.tickets.invalidate()
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
    settings = validate_settings(settings if settings is not None else get_settings())
    try:
        with InstanceGuard(instance_directory) as guard:
            paths = ensure_runtime_dirs(settings.data_dir, profile_id=settings.profile_id)
            maintenance = MaintenanceService(paths["config"], profile_id=settings.profile_id)
            with maintenance.operation():
                secure_tree(paths["data"])
            # Fresh SQLite does not construct or access the credential store.
            storage = StorageService(settings)
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
                from .setup_context import open_setup_context

                with maintenance.operation(), reserve_loopback(settings.admin_api_port) as listener, open_setup_context(settings) as setup_context:

                    async def lifecycle():
                        try:
                            await _serve(
                                settings, paths, database, listener,
                                setup_context=setup_context, instance_guard=guard,
                            )
                        finally:
                            await database.close()

                    asyncio.run(lifecycle())
            finally:
                asyncio.run(database.close())
    except AlreadyRunning:
        raise SystemExit(17) from None
