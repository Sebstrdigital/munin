"""Config resolution: defaults < TOML < env vars."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from munin.core.errors import MuninConfigError

_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "munin" / "config.toml"

_DEFAULTS: dict[str, str | int] = {
    "db_url": "postgresql://munin:munin@localhost:5433/munin",
    "embed_url": "http://localhost:8088",
    "embed_dim": 768,
    "default_limit": 10,
    "embed_batch_size": 32,
}

_ENV_MAP: dict[str, str] = {
    "db_url": "MUNIN_DB_URL",
    "embed_url": "MUNIN_EMBED_URL",
    "embed_dim": "MUNIN_EMBED_DIM",
    "default_limit": "MUNIN_DEFAULT_LIMIT",
    "embed_batch_size": "MUNIN_EMBED_BATCH_SIZE",
}

_INT_FIELDS = {"embed_dim", "default_limit", "embed_batch_size"}


@dataclass
class MuninConfig:
    db_url: str
    embed_url: str
    embed_dim: int
    default_limit: int
    embed_batch_size: int


def load(config_path: Path | None = None) -> MuninConfig:
    """Load config with precedence: env vars > TOML > defaults."""
    path = config_path if config_path is not None else _DEFAULT_CONFIG_PATH

    # Start from defaults
    resolved: dict[str, str | int] = dict(_DEFAULTS)

    # Layer in TOML values
    if path.exists():
        try:
            with open(path, "rb") as fh:
                toml_data = tomllib.load(fh)
        except Exception as exc:
            raise MuninConfigError(f"Failed to parse config file {path}: {exc}") from exc

        for field in _DEFAULTS:
            if field in toml_data:
                resolved[field] = toml_data[field]

    # Layer in env vars (highest priority)
    for field, env_var in _ENV_MAP.items():
        raw = os.environ.get(env_var)
        if raw is not None:
            if field in _INT_FIELDS:
                try:
                    resolved[field] = int(raw)
                except ValueError as exc:
                    raise MuninConfigError(
                        f"Env var {env_var}={raw!r} is not a valid integer"
                    ) from exc
            else:
                resolved[field] = raw

    return MuninConfig(
        db_url=str(resolved["db_url"]),
        embed_url=str(resolved["embed_url"]),
        embed_dim=int(resolved["embed_dim"]),
        default_limit=int(resolved["default_limit"]),
        embed_batch_size=int(resolved["embed_batch_size"]),
    )
