from __future__ import annotations

import pytest

from tg_assistant.services.files import MediaStore


def test_media_store_default_deny_and_path_safety(tmp_path) -> None:
    disabled = MediaStore(tmp_path)
    with pytest.raises(PermissionError):
        disabled.store_bytes(b"x", file_name="x.txt", mime_type="text/plain")

    store = MediaStore(tmp_path, enabled=True, max_size_mb=1)
    first = store.store_bytes(b"safe", file_name="../../evil.txt", mime_type="text/plain")
    second = store.store_bytes(b"safe", file_name="../../evil.txt", mime_type="text/plain")
    assert first.path.is_relative_to(tmp_path)
    assert ".." not in first.path.name
    assert second.duplicate


def test_media_store_rejects_mime_and_size(tmp_path) -> None:
    store = MediaStore(tmp_path, enabled=True, max_size_mb=0)
    with pytest.raises(ValueError):
        store.store_bytes(b"x", file_name="x.exe", mime_type="application/x-msdownload")
    with pytest.raises(ValueError):
        store.store_bytes(b"x", file_name="x.txt", mime_type="text/plain")
