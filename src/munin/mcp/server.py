"""FastMCP server exposing remember, recall, and management tools."""

from __future__ import annotations

import functools
import sys
import traceback
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.fastmcp import FastMCP

import munin.core.memory as memory
from munin.core.config import load as _load_config
from munin.core.db import get_pool as _get_pool
from munin.core.embed import embed as _embed
from munin.core.errors import MuninDBError, MuninEmbedError, MuninError
from munin.core.logging import setup_logging as _setup_logging
from munin.core.scope import current_project

_setup_logging()

mcp: FastMCP = FastMCP("munin", instructions="Local memory store for coding agents")

F = TypeVar("F", bound=Callable[..., Any])


def _error_response(code: str, message: str, hint: str | None = None) -> dict[str, Any]:
    resp: dict[str, Any] = {"error": {"code": code, "message": message}}
    if hint:
        resp["error"]["hint"] = hint
    return resp


def _handle_errors(func: F) -> F:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except MuninDBError as e:
            return _error_response("db_unreachable", str(e), "run `docker compose up -d`")
        except MuninEmbedError as e:
            return _error_response("embed_unreachable", str(e), "check llama.cpp container")
        except MuninError as e:
            return _error_response("validation_error", str(e))
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            return _error_response("internal_error", f"unexpected error: {type(e).__name__}: {e}")

    return wrapper  # type: ignore[return-value]

# Resolved once at server startup from cwd so all tool calls share the same project.
_project: str = current_project() or "unknown"


@mcp.tool()
@_handle_errors
def remember(
    content: str,
    scope: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, str] | None = None,
) -> dict[str, str]:
    """Store a thought in munin memory."""
    thought_id = memory.remember(
        content,
        project=_project,
        scope=scope,
        tags=tags,
        metadata=metadata,
    )
    return {"id": str(thought_id), "project": _project}


@mcp.tool()
@_handle_errors
def recall(
    query: str,
    scope: str | None = None,
    limit: int = 10,
    threshold: float = 0.0,
) -> dict[str, object]:
    """Recall similar thoughts from memory."""
    results = memory.recall(
        query,
        project=_project,
        scope=scope,
        limit=limit,
        threshold=threshold,
    )
    return {
        "results": [
            {
                "id": str(r.id),
                "content": r.content,
                "project": r.project,
                "scope": r.scope,
                "tags": r.tags,
                "similarity": r.similarity,
                "created_at": r.created_at.isoformat(),
            }
            for r in results
        ],
        "project": _project,
        "count": len(results),
    }


@mcp.tool()
@_handle_errors
def list_projects() -> list[dict[str, object]]:
    """List all projects with thought counts."""
    results = memory.list_projects()
    return [{"project": p, "count": c} for p, c in results]


@mcp.tool()
@_handle_errors
def show(thought_id: str) -> dict[str, object]:
    """Show a full thought by id."""
    thought = memory.show(thought_id)
    if thought is None:
        return {"error": {"code": "not_found", "message": f"Thought {thought_id} not found"}}
    return {
        "id": str(thought.id),
        "content": thought.content,
        "project": thought.project,
        "scope": thought.scope,
        "tags": thought.tags,
        "metadata": thought.metadata,
        "created_at": thought.created_at.isoformat(),
        "updated_at": thought.updated_at.isoformat(),
    }


@mcp.tool()
@_handle_errors
def forget(thought_id: str) -> dict[str, object]:
    """Delete a thought by id."""
    deleted = memory.forget(thought_id)
    if not deleted:
        return {"error": {"code": "not_found", "message": f"Thought {thought_id} not found"}}
    return {"deleted": True, "id": thought_id}


@mcp.tool()
@_handle_errors
def stats() -> dict[str, object]:
    """Get memory store statistics."""
    cfg = _load_config()

    db_reachable = False
    total_thoughts = 0
    try:
        pool = _get_pool(cfg)
        pool.open(wait=True)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM thoughts")
                row = cur.fetchone()
                total_thoughts = int(row[0]) if row else 0
        db_reachable = True
    except Exception:
        pass

    embed_reachable = False
    try:
        _embed("ping", config=cfg)
        embed_reachable = True
    except Exception:
        pass

    total_projects = len(memory.list_projects()) if db_reachable else 0

    return {
        "total_thoughts": total_thoughts,
        "total_projects": total_projects,
        "embed_server_reachable": embed_reachable,
        "db_reachable": db_reachable,
    }


def main() -> None:
    """Entry point — run the MCP server over stdio."""
    mcp.run(transport="stdio")
