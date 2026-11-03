"""Configuration via environment variables."""

import os
from pathlib import Path

# Credential Engine Registry Search API key (required).
CE_API_KEY_VAR = "CE_REGISTRY_API_KEY"

SEARCH_ENDPOINTS = {
    "production": "https://apps.credentialengine.org/assistant/search/ctdl",
    "sandbox": "https://sandbox.credentialengine.org/assistant/search/ctdl",
}


def get_ce_key() -> str | None:
    return os.environ.get(CE_API_KEY_VAR)


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "transcript-ctid-matcher"
