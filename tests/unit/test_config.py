"""Unit tests for munin.core.config."""

import tomllib  # noqa: F401 — stdlib, confirms 3.11+
from pathlib import Path

import pytest

from munin.core.config import MuninConfig, load


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ---------------------------------------------------------------------------
# AC-1: defaults when no file and no env vars
# ---------------------------------------------------------------------------


def test_defaults_no_file_no_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """load() with a non-existent config path and no env vars returns built-in defaults."""
    _clear_env(monkeypatch)
    cfg = load(config_path=tmp_path / "nonexistent.toml")

    assert isinstance(cfg, MuninConfig)
    assert cfg.db_url == "postgresql://munin:munin@localhost:5433/munin"
    assert cfg.embed_url == "http://localhost:8088"
    assert cfg.embed_dim == 768
    assert cfg.default_limit == 10
    assert cfg.embed_batch_size == 32


# ---------------------------------------------------------------------------
# AC-2: TOML overrides default
# ---------------------------------------------------------------------------


def test_toml_overrides_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A db_url in config.toml wins over the built-in default."""
    _clear_env(monkeypatch)
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, 'db_url = "postgresql://custom:pw@remotehost:5432/mydb"\n')

    cfg = load(config_path=cfg_path)

    assert cfg.db_url == "postgresql://custom:pw@remotehost:5432/mydb"
    # Non-overridden fields keep their defaults
    assert cfg.embed_url == "http://localhost:8088"
    assert cfg.embed_dim == 768


def test_toml_overrides_multiple_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multiple TOML keys are all applied."""
    _clear_env(monkeypatch)
    cfg_path = tmp_path / "config.toml"
    _write_toml(
        cfg_path,
        'db_url = "postgresql://a:b@host/db"\nembed_url = "http://embed:9000"\nembed_dim = 512\n',
    )

    cfg = load(config_path=cfg_path)

    assert cfg.db_url == "postgresql://a:b@host/db"
    assert cfg.embed_url == "http://embed:9000"
    assert cfg.embed_dim == 512


# ---------------------------------------------------------------------------
# AC-3: env var overrides TOML and default
# ---------------------------------------------------------------------------


def test_env_overrides_toml_and_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MUNIN_DB_URL beats both the TOML file and built-in default."""
    _clear_env(monkeypatch)
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, 'db_url = "postgresql://toml:pw@toml-host/db"\n')
    monkeypatch.setenv("MUNIN_DB_URL", "postgresql://env:pw@env-host/db")

    cfg = load(config_path=cfg_path)

    assert cfg.db_url == "postgresql://env:pw@env-host/db"


def test_env_overrides_default_no_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MUNIN_DB_URL beats the built-in default even when there is no TOML file."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("MUNIN_DB_URL", "postgresql://env:pw@env-host/db")

    cfg = load(config_path=tmp_path / "no-file.toml")

    assert cfg.db_url == "postgresql://env:pw@env-host/db"


def test_env_int_fields_parsed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Integer env vars are coerced to int correctly."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("MUNIN_EMBED_DIM", "384")
    monkeypatch.setenv("MUNIN_DEFAULT_LIMIT", "5")
    monkeypatch.setenv("MUNIN_EMBED_BATCH_SIZE", "16")

    cfg = load(config_path=tmp_path / "no-file.toml")

    assert cfg.embed_dim == 384
    assert cfg.default_limit == 5
    assert cfg.embed_batch_size == 16


def test_env_embed_url_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MUNIN_EMBED_URL beats the built-in default."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("MUNIN_EMBED_URL", "http://gpu-server:8088")

    cfg = load(config_path=tmp_path / "no-file.toml")

    assert cfg.embed_url == "http://gpu-server:8088"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_invalid_int_env_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-integer value for an int env var raises MuninConfigError."""
    from munin.core.errors import MuninConfigError

    _clear_env(monkeypatch)
    monkeypatch.setenv("MUNIN_EMBED_DIM", "not-a-number")

    with pytest.raises(MuninConfigError, match="MUNIN_EMBED_DIM"):
        load(config_path=tmp_path / "no-file.toml")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all MUNIN_* env vars so tests start from a clean slate."""
    for var in (
        "MUNIN_DB_URL",
        "MUNIN_EMBED_URL",
        "MUNIN_EMBED_DIM",
        "MUNIN_DEFAULT_LIMIT",
        "MUNIN_EMBED_BATCH_SIZE",
    ):
        monkeypatch.delenv(var, raising=False)
