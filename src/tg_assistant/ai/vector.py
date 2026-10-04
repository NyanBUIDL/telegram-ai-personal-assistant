from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointIdsList,
    PointStruct,
    VectorParams,
)


class LocalVectorStore:
    COLLECTION = "telegram_messages"

    def __init__(self, path: Path, vector_size: int = 1536, *, profile=None) -> None:
        self.profile = profile
        if profile is not None:
            if vector_size != profile.dimension:
                raise ValueError("Vector identity dimension mismatch")
            manifest = path / "embedding-profile.json"
            identity = profile.model_dump(mode="json", exclude={"cloud_consent"})
            if manifest.exists():
                try:
                    saved = json.loads(manifest.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    raise ValueError("Invalid vector identity") from None
                if saved != identity:
                    raise ValueError("Vector identity mismatch; preserve the existing corpus")
            elif path.exists() and any(path.iterdir()):
                raise ValueError("Unknown vector identity; explicit recovery is required")
        path.mkdir(parents=True, exist_ok=True)
        self.client = QdrantClient(path=str(path))
        try:
            if not self.client.collection_exists(self.COLLECTION):
                self.client.create_collection(
                    self.COLLECTION,
                    vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
                )
            elif (
                self.client.get_collection(self.COLLECTION).config.params.vectors.size
                != vector_size
            ):
                raise ValueError("Vector identity dimension mismatch")
            if profile is not None and not manifest.exists():
                # Qdrant's exclusive local lock serializes competing initializers.
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="w", encoding="utf-8", dir=path, delete=False
                    ) as file:
                        temporary = Path(file.name)
                        json.dump(identity, file, sort_keys=True)
                        file.flush()
                        os.fsync(file.fileno())
                    temporary.replace(manifest)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
        except BaseException:
            self.client.close()
            raise

    def upsert(
        self, reference_id: int, vector: list[float], *, chat_id: int, message_id: int
    ) -> None:
        self.upsert_many([(reference_id, vector, chat_id, message_id)])

    def upsert_many(
        self,
        entries: list[tuple[int, list[float], int, int]],
    ) -> None:
        if not entries:
            return
        self.client.upsert(
            self.COLLECTION,
            [
                PointStruct(
                    id=reference_id,
                    vector=vector,
                    payload={"chat_id": chat_id, "message_id": message_id},
                )
                for reference_id, vector, chat_id, message_id in entries
            ],
        )

    def search(
        self,
        vector: list[float],
        *,
        allowed_chat_ids: list[int],
        allowed_reference_ids: list[int] | None = None,
        limit: int = 10,
    ) -> list[tuple[int, float, dict]]:
        if not allowed_chat_ids or allowed_reference_ids == []:
            return []
        from qdrant_client.models import FieldCondition, Filter, HasIdCondition, MatchAny

        conditions = [
            FieldCondition(key="chat_id", match=MatchAny(any=allowed_chat_ids)),
        ]
        if allowed_reference_ids is not None:
            conditions.append(HasIdCondition(has_id=allowed_reference_ids))

        points = self.client.query_points(
            self.COLLECTION,
            query=vector,
            query_filter=Filter(must=conditions),
            limit=limit,
        ).points
        return [(int(point.id), float(point.score), dict(point.payload or {})) for point in points]

    def reference_ids(self, *, chat_id: int | None = None) -> list[int]:
        query_filter = (
            Filter(
                must=[
                    FieldCondition(
                        key="chat_id",
                        match=MatchValue(value=chat_id),
                    )
                ]
            )
            if chat_id is not None
            else None
        )
        result: list[int] = []
        offset = None
        while True:
            points, offset = self.client.scroll(
                self.COLLECTION,
                scroll_filter=query_filter,
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            result.extend(int(point.id) for point in points)
            if offset is None:
                return result

    def delete_reference_ids(self, reference_ids: list[int]) -> int:
        unique = list(dict.fromkeys(reference_ids))
        if not unique:
            return 0
        self.client.delete(
            self.COLLECTION,
            points_selector=PointIdsList(points=unique),
            wait=True,
        )
        return len(unique)

    def count(self, *, chat_id: int | None = None) -> int:
        query_filter = (
            Filter(
                must=[
                    FieldCondition(
                        key="chat_id",
                        match=MatchValue(value=chat_id),
                    )
                ]
            )
            if chat_id is not None
            else None
        )
        return int(
            self.client.count(
                self.COLLECTION,
                count_filter=query_filter,
                exact=True,
            ).count
        )

    def close(self) -> None:
        self.client.close()
