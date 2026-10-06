"""Read-only built dashboard resources for an authenticated setup-mode gateway."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

_PUBLIC_SUFFIXES = frozenset({
    ".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".otf",
})


class SetupDashboard:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def index(self):
        """Hash the actual inline early bootstrap; do not permit arbitrary inline JS."""
        try:
            path = self._asset("index.html", index=True)
            if path is None:
                return None
            with path.open("rb") as stream:
                raw = stream.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                return None
            # HTML parsing normalizes CRLF and lone CR before script CSP matching.
            html = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            hashes = []
            for attributes, script in re.findall(
                r"<script\b([^>]*)>(.*?)</script\s*>", html, re.I | re.S
            ):
                if script.strip() and not re.search(r"\bsrc\s*=", attributes, re.I):
                    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
                    hashes.append("'sha256-" + digest + "'")
            policy = (
                "default-src 'none'; script-src 'self' " + " ".join(hashes)
                + "; connect-src 'self'; style-src 'self' 'unsafe-inline'; "
                "font-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                "base-uri 'self'; object-src 'none'"
            )
            return HTMLResponse(html, headers={"Content-Security-Policy": policy})
        except (OSError, UnicodeError, ValueError):
            return None

    def _asset(self, name, *, index=False):
        parts = name.split("/")
        if (
            any(part in {"", ".", ".."} for part in parts)
            or "\\" in name
            or (not index and parts[0] not in {"assets", "fonts"})
        ):
            return None
        selected = (self.root / name).resolve()
        try:
            selected.relative_to(self.root)
        except ValueError:
            return None
        if not selected.is_file() or (
            not index and selected.suffix.lower() not in _PUBLIC_SUFFIXES
        ):
            return None
        return selected

    def asset(self, name):
        try:
            selected = self._asset(name)
        except (OSError, ValueError):
            selected = None
        if selected is None:
            return JSONResponse({"code": "resource_unavailable"}, status_code=404)
        return FileResponse(selected)
