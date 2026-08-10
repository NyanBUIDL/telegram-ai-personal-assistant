from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class StoredMedia:
    path: Path
    sha256: str
    size_bytes: int
    duplicate: bool


class MediaStore:
    """Kho file thụ động: không mở, chạy hay tự tải nội dung."""

    def __init__(
        self,
        root: Path,
        *,
        enabled: bool = False,
        max_size_mb: int = 20,
        allowed_mime_types: set[str] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.enabled = enabled
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.allowed_mime_types = allowed_mime_types or {
            "application/pdf",
            "text/plain",
            "text/csv",
        }

    def store_bytes(self, data: bytes, *, file_name: str, mime_type: str) -> StoredMedia:
        if not self.enabled:
            raise PermissionError("Tải media đang tắt.")
        if mime_type not in self.allowed_mime_types:
            raise ValueError("MIME type không được phép.")
        if len(data) > self.max_size_bytes:
            raise ValueError("File vượt MEDIA_MAX_SIZE_MB.")
        digest = hashlib.sha256(data).hexdigest()
        clean_name = SAFE_NAME.sub("_", Path(file_name).name).strip("._") or "file"
        target = (self.root / digest[:2] / f"{digest}-{clean_name}").resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Đường dẫn file không an toàn.") from exc
        duplicate = target.exists()
        if not duplicate:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return StoredMedia(target, digest, len(data), duplicate)
