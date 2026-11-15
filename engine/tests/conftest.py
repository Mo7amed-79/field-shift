# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Keep every test offline and away from the real data/cache/."""

import pytest
import requests

from engine import cdl_client, ndvi_client, power_client, soil_client


@pytest.fixture(autouse=True)
def no_network(tmp_path_factory, monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("network disabled in tests")

    monkeypatch.setattr(requests, "get", fail)
    for module in (power_client, soil_client, ndvi_client, cdl_client):
        monkeypatch.setattr(module, "CACHE_DIR", tmp_path_factory.mktemp(module.__name__.split(".")[-1]))
        monkeypatch.setattr(module.time, "sleep", lambda s: None)
    # No LLM key from the developer's .env or shell may reach a test (no real chat calls).
    from backend import chat

    monkeypatch.setattr(chat, "ENV_FILE", tmp_path_factory.mktemp("noenv") / ".env")
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
