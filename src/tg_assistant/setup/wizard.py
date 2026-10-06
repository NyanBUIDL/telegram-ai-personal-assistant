"""Fail-closed compatibility entry point for the retired MySQL setup wizard."""


def run_setup() -> None:
    """Never inspect, provision, migrate or adopt a legacy MySQL installation."""
    raise RuntimeError("mysql_setup_unsupported")
