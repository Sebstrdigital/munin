"""Unit tests for munin.core.logging.setup_logging."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

import pytest

import munin.core.logging as munin_logging


def _reset() -> None:
    """Reset the _configured guard and remove any handlers added to 'munin' logger."""
    munin_logging._configured = False
    root = logging.getLogger("munin")
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()


@pytest.fixture(autouse=True)
def reset_logging_state() -> None:
    _reset()
    yield
    _reset()


def test_setup_logging_creates_log_dir(tmp_path: Path) -> None:
    log_dir = tmp_path / "state" / "munin"
    log_file = log_dir / "munin.log"

    with (
        patch.object(munin_logging, "_LOG_DIR", log_dir),
        patch.object(munin_logging, "_LOG_FILE", log_file),
    ):
        munin_logging.setup_logging()

    assert log_dir.exists()


def test_setup_logging_attaches_rotating_handler(tmp_path: Path) -> None:
    log_dir = tmp_path / "state" / "munin"
    log_file = log_dir / "munin.log"

    with (
        patch.object(munin_logging, "_LOG_DIR", log_dir),
        patch.object(munin_logging, "_LOG_FILE", log_file),
    ):
        munin_logging.setup_logging()

    munin_logger = logging.getLogger("munin")
    rotating = [h for h in munin_logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(rotating) == 1
    assert rotating[0].maxBytes == 10_000_000
    assert rotating[0].backupCount == 5


def test_configured_guard_prevents_double_init(tmp_path: Path) -> None:
    log_dir = tmp_path / "state" / "munin"
    log_file = log_dir / "munin.log"

    with (
        patch.object(munin_logging, "_LOG_DIR", log_dir),
        patch.object(munin_logging, "_LOG_FILE", log_file),
    ):
        munin_logging.setup_logging()
        munin_logging.setup_logging()  # second call should be no-op

    munin_logger = logging.getLogger("munin")
    rotating = [h for h in munin_logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(rotating) == 1  # only one handler, not two


def test_verbose_true_sets_debug_level(tmp_path: Path) -> None:
    log_dir = tmp_path / "state" / "munin"
    log_file = log_dir / "munin.log"

    with (
        patch.object(munin_logging, "_LOG_DIR", log_dir),
        patch.object(munin_logging, "_LOG_FILE", log_file),
    ):
        munin_logging.setup_logging(verbose=True)

    munin_logger = logging.getLogger("munin")
    assert munin_logger.level == logging.DEBUG


def test_default_sets_info_level(tmp_path: Path) -> None:
    log_dir = tmp_path / "state" / "munin"
    log_file = log_dir / "munin.log"

    with (
        patch.object(munin_logging, "_LOG_DIR", log_dir),
        patch.object(munin_logging, "_LOG_FILE", log_file),
    ):
        munin_logging.setup_logging(verbose=False)

    munin_logger = logging.getLogger("munin")
    assert munin_logger.level == logging.INFO


def test_munin_log_level_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_dir = tmp_path / "state" / "munin"
    log_file = log_dir / "munin.log"
    monkeypatch.setenv("MUNIN_LOG_LEVEL", "WARNING")

    with (
        patch.object(munin_logging, "_LOG_DIR", log_dir),
        patch.object(munin_logging, "_LOG_FILE", log_file),
    ):
        munin_logging.setup_logging()

    munin_logger = logging.getLogger("munin")
    assert munin_logger.level == logging.WARNING


def test_oserror_does_not_raise(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """OSError during handler setup should print a warning but not raise."""
    bad_dir = Path("/proc/nonexistent/munin")
    bad_file = bad_dir / "munin.log"

    with (
        patch.object(munin_logging, "_LOG_DIR", bad_dir),
        patch.object(munin_logging, "_LOG_FILE", bad_file),
    ):
        munin_logging.setup_logging()  # should not raise

    captured = capsys.readouterr()
    assert "Warning" in captured.err
