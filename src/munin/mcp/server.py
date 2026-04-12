"""FastMCP server exposing remember and recall tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import munin.core.memory as memory
from munin.core.scope import current_project

mcp: FastMCP = FastMCP("munin", instructions="Local memory store for coding agents")

# Resolved once at server startup from cwd so all tool calls share the same project.
_project: str = current_project() or "unknown"


@mcp.tool()
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


def main() -> None:
    """Entry point — run the MCP server over stdio."""
    mcp.run(transport="stdio")
