"""Disposable D02 Playwright gateway. Tickets arrive only via real native IPC."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from tg_assistant.desktop.ipc import NativePipeServer, native_request
from tg_assistant.desktop.worker import RuntimeGateway, reserve_loopback


async def run():
    with reserve_loopback(0) as listener:
        port, run_id = listener.getsockname()[1], uuid4().hex
        gateway = RuntimeGateway(port, run_id)
        dist = Path(sys.argv[1]).resolve()
        gateway.setup.router.routes = [route for route in gateway.setup.router.routes if getattr(route, 'path', None) != '/']
        @gateway.setup.get('/')
        async def index():
            return FileResponse(dist / 'index.html')
        gateway.setup.mount('/assets', StaticFiles(directory=dist / 'assets'))
        server = uvicorn.Server(uvicorn.Config(gateway, access_log=False, log_config=None, server_header=False))
        server.install_signal_handlers = lambda: None
        with NativePipeServer('default', run_id, gateway.tickets):
            task = asyncio.create_task(server.serve(sockets=[listener]))
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError('fixture_start_failed')
                await asyncio.sleep(0.01)
            def commands():
                for line in sys.stdin:
                    if line.strip() == 'stop':
                        server.should_exit = True
                        break
                    if line.strip() == 'issue':
                        try:
                            result = native_request('default', run_id, {'name': 'issue_dashboard_ticket', 'request_id': uuid4().hex, 'profile_id': 'default', 'payload_nonsecret': {}}, server_pid=os.getpid())
                            print(json.dumps({'type': 'ticket', 'url': gateway.origin + '#launch_ticket=' + result['raw_ticket']}), flush=True)
                        except (OSError, PermissionError):
                            print(json.dumps({'type': 'error', 'code': 'native_denied'}), flush=True)
            threading.Thread(target=commands, daemon=True).start()
            print(json.dumps({'type': 'ready', 'origin': gateway.origin}), flush=True)
            await task


if __name__ == '__main__':
    asyncio.run(run())
