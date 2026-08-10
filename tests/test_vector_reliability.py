from __future__ import annotations

from tg_assistant.config import Settings
from tg_assistant.services.vector_reliability import legacy_store_id, local_store_id


def test_local_store_identity_is_provider_independent(tmp_path) -> None:
    cloud = Settings(_env_file=None, data_dir=tmp_path, ai_provider="openai")
    local = Settings(_env_file=None, data_dir=tmp_path, ai_provider="ollama")

    assert cloud.resolved_semantic_vector_path == local.resolved_semantic_vector_path
    assert local_store_id(cloud) == local_store_id(local)
    assert local_store_id(cloud) != legacy_store_id(cloud)
