from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

import httpx

MODEL_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$"
)


class OllamaError(RuntimeError):
    pass


class OllamaPullCancelled(OllamaError):
    pass


@dataclass(frozen=True, slots=True)
class OllamaModel:
    name: str
    size: int
    modified_at: datetime | None
    family: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None


def validate_model_name(value: str) -> str:
    name = value.strip()
    if not MODEL_NAME_RE.fullmatch(name):
        raise OllamaError(
            "Tên model không hợp lệ. Ví dụ hợp lệ: qwen3:8b hoặc hf.co/user/model."
        )
    return name


def format_model_size(size: int) -> str:
    if size <= 0:
        return "cloud/không rõ"
    units = ("B", "KB", "MB", "GB", "TB")
    rendered = float(size)
    unit = units[0]
    for unit in units:
        if rendered < 1024 or unit == units[-1]:
            break
        rendered /= 1024
    return f"{rendered:.1f} {unit}"


class OllamaService:
    def __init__(
        self,
        openai_base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        native_base = openai_base_url.rstrip("/")
        if native_base.endswith("/v1"):
            native_base = native_base[:-3]
        self.client = httpx.AsyncClient(
            base_url=native_base,
            timeout=30,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        request_timeout: float | None = None,
    ) -> object:
        try:
            response = await self.client.request(
                method,
                path,
                json=json,
                timeout=request_timeout,
            )
        except httpx.TimeoutException as exc:
            raise OllamaError("Ollama phản hồi quá chậm; vui lòng thử lại.") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(
                "Không kết nối được Ollama local. Hãy kiểm tra ứng dụng Ollama đang chạy."
            ) from exc
        if response.is_error:
            try:
                detail = str(response.json().get("error", "")).strip()
            except (ValueError, AttributeError):
                detail = ""
            message = detail[:300] or f"HTTP {response.status_code}"
            raise OllamaError(f"Ollama từ chối yêu cầu: {message}")
        try:
            return response.json()
        except ValueError as exc:
            raise OllamaError("Ollama trả về phản hồi không hợp lệ.") from exc

    async def list_models(self) -> list[OllamaModel]:
        payload = await self._request("GET", "/api/tags")
        rows = payload.get("models", []) if isinstance(payload, dict) else []
        models: list[OllamaModel] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            modified_at = None
            if row.get("modified_at"):
                try:
                    modified_at = datetime.fromisoformat(
                        str(row["modified_at"]).replace("Z", "+00:00")
                    )
                except ValueError:
                    pass
            models.append(
                OllamaModel(
                    name=str(row["name"]),
                    size=int(row.get("size") or 0),
                    modified_at=modified_at,
                    family=str(details.get("family") or "") or None,
                    parameter_size=str(details.get("parameter_size") or "") or None,
                    quantization_level=str(details.get("quantization_level") or "") or None,
                )
            )
        return sorted(models, key=lambda item: item.name.casefold())

    async def pull_model(
        self,
        model: str,
        *,
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
        should_cancel: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        name = validate_model_name(model)
        try:
            async with self.client.stream(
                "POST",
                "/api/pull",
                json={"model": name, "stream": True},
                timeout=6 * 60 * 60,
            ) as response:
                if response.is_error:
                    body = await response.aread()
                    try:
                        detail = str(json.loads(body).get("error", "")).strip()
                    except (ValueError, AttributeError):
                        detail = ""
                    message = detail[:300] or f"HTTP {response.status_code}"
                    raise OllamaError(f"Ollama từ chối yêu cầu: {message}")

                async for line in response.aiter_lines():
                    if should_cancel and await should_cancel():
                        raise OllamaPullCancelled("Owner đã hủy tải model.")
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    total = int(event.get("total") or 0)
                    completed = int(event.get("completed") or 0)
                    progress = (
                        min(99, max(0, round((completed / total) * 100)))
                        if total > 0
                        else None
                    )
                    if event.get("status") == "success":
                        progress = 100
                    if on_progress:
                        await on_progress(
                            {
                                "status": str(event.get("status") or "downloading"),
                                "digest": str(event.get("digest") or "") or None,
                                "total_bytes": total or None,
                                "completed_bytes": completed or None,
                                "progress": progress,
                            }
                        )
                if should_cancel and await should_cancel():
                    raise OllamaPullCancelled("Owner đã hủy tải model.")
        except OllamaPullCancelled:
            raise
        except httpx.TimeoutException as exc:
            raise OllamaError("Ollama phản hồi quá chậm; vui lòng thử lại.") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(
                "Không kết nối được Ollama local. Hãy kiểm tra ứng dụng Ollama đang chạy."
            ) from exc

    async def delete_model(self, model: str) -> None:
        name = validate_model_name(model)
        await self._request("DELETE", "/api/delete", json={"model": name})

    async def embedding_dimension(self, model: str) -> int:
        name = validate_model_name(model)
        payload = await self._request(
            "POST",
            "/api/embed",
            json={"model": name, "input": ["Kiểm tra embedding Telegram RAG"]},
            request_timeout=10 * 60,
        )
        embeddings = payload.get("embeddings", []) if isinstance(payload, dict) else []
        if not embeddings or not isinstance(embeddings[0], list) or not embeddings[0]:
            raise OllamaError(f"Model {name} không trả về vector embedding hợp lệ.")
        return len(embeddings[0])

    async def close(self) -> None:
        await self.client.aclose()
