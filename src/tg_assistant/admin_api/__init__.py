"""Loopback-only administration API for the local dashboard."""

from .app import AdminContext, create_admin_app
from .auth import dashboard_login_code, ensure_dashboard_secret

__all__ = [
    "AdminContext",
    "create_admin_app",
    "dashboard_login_code",
    "ensure_dashboard_secret",
]
