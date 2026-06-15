"""Config resolution: defaults < TOML < env vars."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from munin.core.errors import MuninConfigError

_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "munin" / "config.toml"

_DEFAULTS: dict[str, str | int | float | bool] = {
    "db_url": "postgresql://munin:munin@localhost:5433/munin",
    "embed_url": "http://localhost:8088",
    "embed_dim": 768,
    "default_limit": 10,
    "embed_batch_size": 32,
    # Hybrid RRF ranking weights (US-003).
    # w_rrf + w_recency + w_hits should sum to 1.0 (not enforced, just documented).
    # w_rrf:     weight for fused RRF relevance signal (default 0.7)
    # w_recency: weight for recency signal derived from last_hit_at / created_at (default 0.2)
    # w_hits:    weight for normalised hit_count signal (default 0.1)
    "recall_w_rrf": 0.7,
    "recall_w_recency": 0.2,
    "recall_w_hits": 0.1,
    # RRF constant k — higher values reduce the influence of rank position (default 60)
    "recall_rrf_k": 60,
    # MMR diversity re-ranking (US-004).
    # recall_mmr_enabled: when True, apply Maximal Marginal Relevance after hybrid fusion
    # recall_mmr_lambda:  trade-off between relevance (1.0) and diversity (0.0); default 0.7
    "recall_mmr_enabled": True,
    "recall_mmr_lambda": 0.7,
    # Semantic near-duplicate detection on write (P2-1).
    # remember_dedup_enabled: when True, ANN-check before insert and skip/merge if
    #   the top in-project hit exceeds remember_dedup_threshold.
    # remember_dedup_threshold: cosine similarity cutoff; >= this value => skip (default 0.95)
    "remember_dedup_enabled": True,
    "remember_dedup_threshold": 0.95,
}

_ENV_MAP: dict[str, str] = {
    "db_url": "MUNIN_DB_URL",
    "embed_url": "MUNIN_EMBED_URL",
    "embed_dim": "MUNIN_EMBED_DIM",
    "default_limit": "MUNIN_DEFAULT_LIMIT",
    "embed_batch_size": "MUNIN_EMBED_BATCH_SIZE",
    "recall_w_rrf": "MUNIN_RECALL_W_RRF",
    "recall_w_recency": "MUNIN_RECALL_W_RECENCY",
    "recall_w_hits": "MUNIN_RECALL_W_HITS",
    "recall_rrf_k": "MUNIN_RECALL_RRF_K",
    "recall_mmr_enabled": "MUNIN_RECALL_MMR_ENABLED",
    "recall_mmr_lambda": "MUNIN_RECALL_MMR_LAMBDA",
    "remember_dedup_enabled": "MUNIN_REMEMBER_DEDUP_ENABLED",
    "remember_dedup_threshold": "MUNIN_REMEMBER_DEDUP_THRESHOLD",
}

_INT_FIELDS = {"embed_dim", "default_limit", "embed_batch_size", "recall_rrf_k"}
_FLOAT_FIELDS = {
    "recall_w_rrf", "recall_w_recency", "recall_w_hits",
    "recall_mmr_lambda", "remember_dedup_threshold",
}
_BOOL_FIELDS = {"recall_mmr_enabled", "remember_dedup_enabled"}


@dataclass
class MuninConfig:
    db_url: str
    embed_url: str
    embed_dim: int
    default_limit: int
    embed_batch_size: int
    # Hybrid recall ranking weights
    recall_w_rrf: float = 0.7
    recall_w_recency: float = 0.2
    recall_w_hits: float = 0.1
    recall_rrf_k: int = 60
    # MMR diversity re-ranking (US-004)
    recall_mmr_enabled: bool = True
    recall_mmr_lambda: float = 0.7
    # Semantic near-duplicate detection on write (P2-1)
    remember_dedup_enabled: bool = True
    remember_dedup_threshold: float = 0.95


def load(config_path: Path | None = None) -> MuninConfig:
    """Load config with precedence: env vars > TOML > defaults."""
    path = config_path if config_path is not None else _DEFAULT_CONFIG_PATH

    # Start from defaults
    resolved: dict[str, str | int | float] = dict(_DEFAULTS)

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
            elif field in _FLOAT_FIELDS:
                try:
                    resolved[field] = float(raw)
                except ValueError as exc:
                    raise MuninConfigError(
                        f"Env var {env_var}={raw!r} is not a valid float"
                    ) from exc
            elif field in _BOOL_FIELDS:
                resolved[field] = raw.lower() not in {"0", "false", "no", "off"}
            else:
                resolved[field] = raw

    return MuninConfig(
        db_url=str(resolved["db_url"]),
        embed_url=str(resolved["embed_url"]),
        embed_dim=int(resolved["embed_dim"]),
        default_limit=int(resolved["default_limit"]),
        embed_batch_size=int(resolved["embed_batch_size"]),
        recall_w_rrf=float(resolved["recall_w_rrf"]),
        recall_w_recency=float(resolved["recall_w_recency"]),
        recall_w_hits=float(resolved["recall_w_hits"]),
        recall_rrf_k=int(resolved["recall_rrf_k"]),
        recall_mmr_enabled=bool(resolved["recall_mmr_enabled"]),
        recall_mmr_lambda=float(resolved["recall_mmr_lambda"]),
        remember_dedup_enabled=bool(resolved["remember_dedup_enabled"]),
        remember_dedup_threshold=float(resolved["remember_dedup_threshold"]),
    )
