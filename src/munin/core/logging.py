"""Persistent rotating file logging for munin CLI and MCP server."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path.home() / ".local" / "state" / "munin"
_LOG_FILE = _LOG_DIR / "munin.log"
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_configured = False


def setup_logging(verbose: bool = False) -> None:
    """Configure rotating file logging for the munin logger.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _configured
    if _configured:
        return

    level_str = os.environ.get("MUNIN_LOG_LEVEL", "DEBUG" if verbose else "INFO")
    level = getattr(logging, level_str.upper(), logging.INFO)

    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            _LOG_FILE, maxBytes=10_000_000, backupCount=5
        )
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler.setLevel(level)

        root = logging.getLogger("munin")
        root.setLevel(level)
        root.addHandler(handler)
    except OSError:
        print(f"Warning: could not create log file at {_LOG_FILE}", file=sys.stderr)

    _configured = True
