"""A native-enrolled shared key is usable only inside its owning profile."""

import pytest
from desktop.test_provider_context import choose, open_context, selected  # noqa: F401


@pytest.mark.parametrize("first", ["chat_ai", "embeddings"])
def test_native_provider_key_can_verify_both_roles_in_same_profile(selected, first):  # noqa: F811
    models = {"chat_ai": "synthetic/chat", "embeddings": "synthetic/embed"}
    second = "embeddings" if first == "chat_ai" else "chat_ai"
    with open_context(selected) as context:
        first_check = context.provider.service.validate_and_save(
            "openrouter", "synthetic-owned-key", choose(first, models[first])
        )
        assert first_check.state == "ready"
        second_check = context.provider.service.validate_and_save(
            "openrouter", "", choose(second, models[second])
        )
        assert second_check.state == "ready"
        assert context.provider.configuration_verification() is not None
        assert selected[2].values["openrouter_api_key"] == "synthetic-owned-key"
