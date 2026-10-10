"""Server-selected verified generations, never caller-selected filesystem paths."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import time
from pathlib import Path

from ..db.models import AppSetting, VectorStore
from ..desktop.instance import _assert_owned_path, _refuse_reparse
from ..paths import APP_NAME, current_user_sid

GENERATION_ID = re.compile(r"^[0-9a-f]{32}$")


def pointer_key(store_id: str) -> str:
    return f"vector_active:{store_id}"


def _absolute(path) -> Path:
    return Path(os.path.abspath(path))


def _external_root(settings) -> Path | None:
    root = _absolute(settings.resolved_semantic_vector_path)
    if root.is_relative_to(_absolute(settings.data_dir)):
        return None
    if settings.ollama_qdrant_path is None:
        raise ValueError("vector_path_outside_profile")
    return root


def _identity_manifest(settings, path: Path) -> None:
    try:
        saved = json.loads((path / "embedding-profile.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("vector_identity_unavailable") from None
    if saved != settings.embedding_profile.model_dump(mode="json", exclude={"cloud_consent"}):
        raise ValueError("vector_identity_mismatch")


def _validate_owned_tree(path: Path) -> None:
    pending = [path]
    while pending:
        entry = pending.pop()
        _refuse_reparse(entry)
        _assert_owned_path(entry)
        details = entry.lstat()
        if getattr(details, "st_file_attributes", 0) & 0x400:
            raise ValueError("vector_path_reparse")
        if stat.S_ISDIR(details.st_mode):
            pending.extend(entry.iterdir())
        elif not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise ValueError("vector_path_alias")


def validate_persisted_collection(path: Path, vector_size: int) -> None:
    """Non-creating check of the supported Qdrant local SQLite collection.

    Validate the entire owned tree before reading metadata or opening SQLite.
    No point pickle is loaded, and active content may change after activation.
    Unsupported layouts and bounded-check exhaustion fail closed.
    """
    from qdrant_client.http.models import CreateCollection, Distance, VectorParams
    from qdrant_client.local.persistence import STORAGE_FILE_NAME
    from qdrant_client.local.qdrant_local import META_INFO_FILENAME

    try:
        _validate_owned_tree(path)
        with (path / META_INFO_FILENAME).open("rb") as file:
            raw = file.read(65537)
        if len(raw) > 65536:
            raise ValueError("vector_storage_unavailable")
        meta = json.loads(raw)
        # LocalVectorStore owns one concrete collection; aliases/extra collections
        # would make Qdrant open unvalidated persistence locations on construction.
        if (
            not isinstance(meta, dict)
            or set(meta) != {"collections", "aliases"}
            or not isinstance(meta["collections"], dict)
            or set(meta["collections"]) != {"telegram_messages"}
            or meta["aliases"] != {}
        ):
            raise ValueError("vector_storage_unavailable")
        saved_config = meta["collections"]["telegram_messages"]
        if not isinstance(saved_config, dict):
            raise ValueError("vector_storage_unavailable")
        # Match QdrantLocal._load's one known deprecated-field normalization,
        # without modifying persisted metadata or ignoring other unknown fields.
        normalized_config = dict(saved_config)
        normalized_config.pop("init_from", None)
        config = CreateCollection.model_validate(normalized_config)
        vectors = config.vectors
        if (
            not isinstance(vectors, VectorParams)
            or vectors.size != vector_size
            or vectors.distance != Distance.COSINE
            or config.sparse_vectors
        ):
            raise ValueError("vector_storage_identity_mismatch")
        storage = path / "collection" / "telegram_messages" / STORAGE_FILE_NAME
        if not storage.is_file() or any(
            Path(str(storage) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")
        ):
            raise ValueError("vector_storage_unavailable")
        # immutable prevents even SQLite read-only WAL handling from creating
        # sidecars. Qdrant's supported local layout uses committed DELETE journal
        # storage; outstanding/unsupported journals are refused above.
        connection = sqlite3.connect(storage.as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            deadline = time.monotonic() + 2
            remaining = 10000

            def bounded():
                nonlocal remaining
                remaining -= 1
                return remaining <= 0 or time.monotonic() >= deadline

            connection.set_progress_handler(bounded, 1000)
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA quick_check(1)").fetchall() != [("ok",)]:
                raise ValueError("vector_storage_unavailable")
            tables = connection.execute(
                "SELECT name, type FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            columns = connection.execute("PRAGMA table_info(points)").fetchall()
            if tables != [("points", "table")] or [
                (column[1], column[2], column[3], column[5]) for column in columns
            ] != [("id", "TEXT", 0, 1), ("point", "BLOB", 0, 0)]:
                raise ValueError("vector_storage_unavailable")
        finally:
            connection.close()
    except (OSError, ValueError, TypeError, sqlite3.Error):
        raise ValueError("vector_storage_unavailable") from None


def validate_profile_tree(settings, path: Path, *, tree: bool = False) -> Path:
    """Read-only identity/alias check before opening or creating Qdrant files."""
    root = _absolute(settings.data_dir)
    candidate = _absolute(path)
    external = _external_root(settings)
    ownership_root = root
    if not candidate.is_relative_to(root):
        if external is None or not (
            candidate == external
            or (
                candidate.parent == external / "generations"
                and GENERATION_ID.fullmatch(candidate.name)
            )
        ):
            raise ValueError("vector_path_outside_profile")
        ownership_root = _absolute(settings.ollama_qdrant_path)
        if not external.is_dir():
            raise ValueError("vector_external_root_unavailable")
    _refuse_reparse(candidate)
    marker = root / ".tg-assistant-data"
    for entry in (root, marker, *candidate.parents):
        if entry.exists():
            _refuse_reparse(entry)
            if getattr(entry.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("vector_path_reparse")
            # Ancestors above the profile may belong to Administrators/System.
            if entry == root or entry.is_relative_to(root) or entry.is_relative_to(ownership_root):
                _assert_owned_path(entry)
    try:
        _assert_owned_path(marker)
        if marker.stat().st_nlink != 1:
            raise ValueError("vector_profile_alias")
        expected = {
            "version": 1,
            "app": APP_NAME,
            "profile_id": settings.profile_id,
            "sid": current_user_sid(),
        }
        saved = json.loads(marker.read_text(encoding="utf-8"))
        if saved != expected or type(saved.get("version")) is not int:
            raise ValueError("vector_profile_mismatch")
    except (OSError, json.JSONDecodeError):
        raise ValueError("vector_profile_unavailable") from None
    if candidate.exists():
        _assert_owned_path(candidate)
    # External corpora are adopted only through known identity and full preflight.
    # This never creates the external root, profile marker, or changes its ACL.
    preflight = external if ownership_root != root else candidate
    if (tree or ownership_root != root) and preflight.exists():
        _validate_owned_tree(preflight)
    if ownership_root != root:
        _identity_manifest(settings, external)
    return candidate


def generation_path(settings, generation_id: str, *, existing: bool = False) -> Path:
    if not isinstance(generation_id, str) or not GENERATION_ID.fullmatch(generation_id):
        raise ValueError("invalid_vector_generation")
    path = settings.resolved_semantic_vector_path / "generations" / generation_id
    validate_profile_tree(settings, path, tree=path.exists())
    if existing and not path.is_dir():
        raise ValueError("vector_generation_unavailable")
    return path


def selected_vector_path(settings, record, registry=None, *, owner_id=None) -> Path:
    if record is None:
        path = validate_profile_tree(settings, settings.resolved_semantic_vector_path, tree=True)
        if _external_root(settings) is not None:
            _validate_registry(settings, path, registry)
            validate_persisted_collection(path, settings.embedding_profile.dimension)
        return path
    profile = settings.embedding_profile
    if not isinstance(record, dict) or (
        record.get("profile_id") != settings.profile_id
        or record.get("sid") != current_user_sid()
        or record.get("store_id") != profile.store_id
        or type(record.get("owner_id")) is not int
        or record["owner_id"] <= 0
        or (owner_id is not None and record["owner_id"] != owner_id)
        or type(record.get("generation")) is not int
        or record["generation"] <= 0
        or record.get("state") != "ready"
        or record.get("identity") != profile.model_dump(mode="json", exclude={"cloud_consent"})
    ):
        raise ValueError("invalid_vector_pointer")
    path = generation_path(settings, record.get("generation_id"), existing=True)
    try:
        ready = json.loads((path / "recovery-ready.json").read_text(encoding="utf-8"))
        manifest = json.loads((path / "embedding-profile.json").read_text(encoding="utf-8"))
        if ready != record or manifest != record["identity"]:
            raise ValueError("vector_generation_unverified")
    except (OSError, json.JSONDecodeError):
        raise ValueError("vector_generation_unavailable") from None
    _validate_registry(settings, path, registry)
    validate_persisted_collection(path, profile.dimension)
    return path


def requires_existing_vector_path(settings, path: Path) -> bool:
    """Selected generations and explicitly approved external corpora never create."""
    return _absolute(path) != _absolute(settings.resolved_semantic_vector_path) or (
        _external_root(settings) is not None
    )


def _validate_registry(settings, path, registry):
    profile = settings.embedding_profile
    if registry is None or (
        registry.store_id != profile.store_id
        or registry.collection != "telegram_messages"
        or registry.role == "legacy_read_only"
        or registry.path != str(path.resolve())
        or registry.provider != profile.provider
        or registry.endpoint_id != profile.endpoint_id
        or registry.model != profile.model
        or registry.embedding_version != profile.embedding_version
        or registry.dimension != profile.dimension
    ):
        raise ValueError("vector_registry_mismatch")


async def active_vector_path(session, settings, *, owner_id=None) -> Path:
    pointer = await session.get(AppSetting, pointer_key(settings.embedding_profile.store_id))
    registry = await session.get(VectorStore, settings.embedding_profile.store_id)
    return selected_vector_path(
        settings, pointer.value if pointer else None, registry, owner_id=owner_id
    )


def resolve_vector_path(database, settings, *, owner_id=None) -> Path:
    """Startup counterpart; uses the established database URL and maintenance fence."""
    from .jobs import JobRepository

    repository = JobRepository(
        database.engine.url, profile_id=settings.profile_id, fence=database.fence
    )
    try:
        with repository.transaction() as session:
            pointer = session.get(AppSetting, pointer_key(settings.embedding_profile.store_id))
            registry = session.get(VectorStore, settings.embedding_profile.store_id)
            return selected_vector_path(
                settings, pointer.value if pointer else None, registry, owner_id=owner_id
            )
    finally:
        repository.close()


def write_ready(path: Path, record: dict) -> None:
    """Candidate evidence is fsynced before the SQL pointer can select it."""
    temporary = path / "recovery-ready.pending"
    with temporary.open("x", encoding="utf-8") as file:
        json.dump(record, file, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path / "recovery-ready.json")
