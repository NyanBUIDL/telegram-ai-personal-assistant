"""Actual setup-mode worker serves the built local dashboard and bounded assets."""

import base64
import hashlib
import os

import httpx
import pytest

from tg_assistant.config import Settings, save_settings


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_inline_csp_hash_matches_html_parser_line_endings(tmp_path, newline):
    from tg_assistant.admin_api.setup_dashboard import SetupDashboard

    script = "\nwindow.__owned_artifact = true;\n"
    html = '<script>' + script.replace("\n", newline) + '</script>'
    (tmp_path / "index.html").write_bytes(html.encode())
    response = SetupDashboard(tmp_path).index()
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert "'sha256-" + digest + "'" in response.headers["content-security-policy"]


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows worker lifecycle")
def test_setup_worker_serves_selected_dashboard_without_spa_api_fallback(tmp_path, monkeypatch):
    from desktop.test_launcher import controller, ready, stop

    for name in list(os.environ):
        if name.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(name)
    bundle = tmp_path / "owned-bundle"
    (bundle / "assets").mkdir(parents=True)
    script = "window.__owned_artifact = true;"
    (bundle / "index.html").write_text(
        '<!doctype html><html lang="vi"><script>' + script
        + '</script><div id="root">owned-static-artifact</div></html>', encoding="utf-8"
    )
    (bundle / "assets" / "app.js").write_text("/* owned static resource */", encoding="utf-8")
    (bundle / "assets" / "not-public.txt").write_text("not a public resource", encoding="utf-8")
    settings = Settings(_env_file=None, data_dir=tmp_path / "profile", dashboard_dist_path=bundle)
    save_settings(settings)
    runtime = controller(settings, tmp_path)
    try:
        runtime.start()
        state = ready(runtime)
        with httpx.Client(base_url=state.url, timeout=3, trust_env=False) as client:
            response = client.get("/")
            assert response.status_code == 200 and "owned-static-artifact" in response.text, (
                "setup-mode gateway still serves its placeholder instead of selected built dashboard"
            )
            digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
            assert "'sha256-" + digest + "'" in response.headers["content-security-policy"]
            assert response.headers["cache-control"] == "no-store"
            assert client.get("/assets/app.js").text == "/* owned static resource */"
            assert client.get("/assets/not-public.txt").status_code == 404
            assert client.get("/assets/%2e%2e/index.html").status_code == 404
            assert client.get("/api/v1/not-real").status_code == 404
            assert client.get("/api/v1/overview").status_code in {401, 403, 404}
            assert client.get("/", headers={"Host": "foreign.invalid"}).status_code == 403
            assert client.get("/", headers={"Origin": "https://foreign.invalid"}).status_code == 403
    finally:
        stop(runtime)
