"""CLI smoke tests — invoke the installed munin entry point via subprocess."""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from tests.cli.conftest import REPO_ROOT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENV = {**os.environ}


def run(*args: str, input: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["munin", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=ENV,
        input=input,
    )


# ---------------------------------------------------------------------------
# Basic CLI behaviour
# ---------------------------------------------------------------------------


def test_help() -> None:
    result = run("--help")
    assert result.returncode == 0
    for word in ("remember", "recall", "show", "forget", "projects"):
        assert word in result.stdout, f"'--help' output missing '{word}'"


def test_version() -> None:
    result = run("--version")
    assert result.returncode == 0
    assert re.search(r"\d+\.\d+\.\d+", result.stdout), "version output has no semver"


# ---------------------------------------------------------------------------
# remember
# ---------------------------------------------------------------------------


def test_remember_arg() -> None:
    result = run("remember", "cli smoke test thought from arg", "--project", "cli_test")
    assert result.returncode == 0
    uuid_pat = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    assert re.match(uuid_pat, result.stdout.strip()), f"expected UUID, got: {result.stdout!r}"
    # Cleanup
    run("forget", result.stdout.strip(), "--yes")


def test_remember_stdin() -> None:
    result = run("remember", "--project", "cli_test", input="stdin smoke thought")
    assert result.returncode == 0
    uuid_pat = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    assert re.match(uuid_pat, result.stdout.strip()), f"expected UUID, got: {result.stdout!r}"
    # Cleanup
    run("forget", result.stdout.strip(), "--yes")


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------


def test_recall_json(remembered_id: str) -> None:
    result = run("recall", "smoke fixture thought", "--project", "cli_test", "--json")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    if data:
        first = data[0]
        for field in ("id", "content", "project", "similarity", "created_at"):
            assert field in first, f"recall result missing field '{field}'"


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show(remembered_id: str) -> None:
    result = run("show", remembered_id, "--json")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["id"] == remembered_id


def test_show_missing() -> None:
    result = run("show", "00000000-0000-0000-0000-000000000000")
    assert result.returncode == 1


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------


def test_forget_yes() -> None:
    # Create a dedicated thought so the module fixture stays intact.
    create = run("remember", "thought for forget test", "--project", "cli_test")
    assert create.returncode == 0
    thought_id = create.stdout.strip()

    result = run("forget", thought_id, "--yes")
    assert result.returncode == 0

    # Confirm it's gone.
    show = run("show", thought_id)
    assert show.returncode == 1


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


def test_projects_json(remembered_id: str) -> None:
    result = run("projects", "--json")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    projects = [row["project"] for row in data]
    assert "cli_test" in projects, f"cli_test not found in projects: {projects}"
