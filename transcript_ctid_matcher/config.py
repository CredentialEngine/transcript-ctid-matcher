"""Configuration via environment variables."""

import os
import sys
from pathlib import Path

# Credential Engine Registry Search API key (required).
CE_API_KEY_VAR = "CE_REGISTRY_API_KEY"

SEARCH_ENDPOINTS = {
    "production": "https://apps.credentialengine.org/assistant/search/ctdl",
    "sandbox": "https://sandbox.credentialengine.org/assistant/search/ctdl",
}


def get_ce_key() -> str | None:
    return os.environ.get(CE_API_KEY_VAR)


def cache_base(os_name: str, platform: str, env: dict, home: str) -> str:
    """The platform's usual per-user cache directory: Windows %LOCALAPPDATA%,
    macOS ~/Library/Caches, otherwise ~/.cache. XDG_CACHE_HOME wins
    wherever it's set."""
    if env.get("XDG_CACHE_HOME"):
        return env["XDG_CACHE_HOME"]
    if os_name == "nt" and env.get("LOCALAPPDATA"):
        return env["LOCALAPPDATA"]
    if platform == "darwin":
        return os.path.join(home, "Library", "Caches")
    return os.path.join(home, ".cache")


def default_cache_dir() -> Path:
    base = cache_base(os.name, sys.platform, dict(os.environ), str(Path.home()))
    return Path(base) / "transcript-ctid-matcher"
