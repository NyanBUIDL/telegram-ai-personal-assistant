"""Read-only sanitized setup routes; root integration owns Host/Origin/auth.

No route issues or completes stage evidence, saves options, or accepts secrets.
The getter is supplied by the trusted worker, never by an HTTP payload.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..services.onboarding import OnboardingCoordinator

_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def install_setup_routes(app: FastAPI, coordinator_getter: Callable[[], OnboardingCoordinator]):
    async def snapshot(*, connections_only=False):
        try:
            coordinator = coordinator_getter()
            if connections_only:
                measured = await asyncio.to_thread(coordinator.connections.status)
                payload = [item.model_dump(mode="json") for item in measured]
            else:
                measured = await asyncio.to_thread(coordinator.status)
                payload = measured.model_dump(mode="json")
            return JSONResponse(payload, headers=_HEADERS)
        except Exception:
            return JSONResponse(
                {
                    "code": "setup_status_unavailable",
                    "message": "Chưa thể đọc trạng thái. Mở ứng dụng Windows để kiểm tra.",
                },
                status_code=503,
                headers=_HEADERS,
            )

    @app.get("/api/v1/setup/status")
    async def setup_status():
        return await snapshot()

    @app.get("/api/v1/connections")
    async def connections():
        return await snapshot(connections_only=True)
