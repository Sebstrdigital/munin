"""Recall interface for vector-similarity thought retrieval."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from munin.core import scope as _scope
from munin.core.config import MuninConfig, load
from munin.core.db import get_pool
from munin.core.embed import embed
from munin.core.errors import MuninError

logger = logging.getLogger(__name__)


@dataclass
class ThoughtResult:
    """A single recalled thought with its similarity score."""

    id: UUID
    content: str
    project: str
    scope: str | None
    tags: list[str]
    metadata: dict[str, Any]
    similarity: float
    created_at: datetime


def recall(
    query: str,
    *,
    project: str | None = None,
    scope: str | None = None,
    limit: int | None = None,
    threshold: float = 0.0,
    config: MuninConfig | None = None,
) -> list[ThoughtResult]:
    """Return thoughts most similar to query, filtered by project and optional scope.

    Args:
        query: Natural-language query to embed and search against.
        project: Project name to filter by. Resolved from git root if not provided.
        scope: Optional scope label to further restrict results.
        limit: Maximum number of results. Defaults to config.default_limit.
        threshold: Minimum similarity score (0.0–1.0); results below are omitted.
        config: Optional config override; uses load() if not provided.

    Returns:
        List of ThoughtResult ordered by descending similarity.

    Raises:
        MuninError: If project cannot be determined.
    """
    cfg = config if config is not None else load()

    resolved_project = project or _scope.current_project()
    if resolved_project is None:
        raise MuninError(
            "project could not be determined; pass project= or run from inside a git repo"
        )

    match_limit = limit if limit is not None else cfg.default_limit
    logger.debug("recall: project=%s query_len=%d limit=%d", resolved_project, len(query), match_limit)
    vec = embed(query, config=cfg)
    # DR-003: fixed-precision formatting avoids repr() emitting 'nan'/'inf'.
    vec_str = "[" + ",".join(f"{v:.8g}" for v in vec) + "]"

    pool = get_pool(cfg)
    pool.open(wait=True)

    results: list[ThoughtResult] = []
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, content, project, scope, tags, metadata,"
                " similarity, created_at"
                " FROM match_thoughts(%s::vector, %s, %s, %s, %s)",
                (vec_str, resolved_project, scope, match_limit, threshold),
            )
            for row in cur.fetchall():
                results.append(
                    ThoughtResult(
                        id=row[0],
                        content=row[1],
                        project=row[2],
                        scope=row[3],
                        tags=list(row[4]) if row[4] else [],
                        metadata=dict(row[5]) if row[5] else {},
                        similarity=float(row[6]),
                        created_at=row[7],
                    )
                )

            # Bump hit counters for every returned thought.
            if results:
                hit_ids = [r.id for r in results]
                cur.execute(
                    "UPDATE thoughts"
                    " SET hit_count = hit_count + 1, last_hit_at = now()"
                    " WHERE id = ANY(%s)",
                    (hit_ids,),
                )
                logger.debug("recall: bumped hit_count for %d thoughts", len(hit_ids))

    return results


@dataclass
class Thought:
    """Full thought row — no similarity score. Returned by show()."""

    id: UUID
    content: str
    project: str
    scope: str | None
    tags: list[str]
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


def list_projects(
    *, config: MuninConfig | None = None
) -> list[tuple[str, int]]:
    """Return (project, thought_count) for every project, ordered by name."""
    pool = get_pool(config)
    pool.open(wait=True)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT project, COUNT(*) FROM thoughts"
                " GROUP BY project ORDER BY project"
            )
            rows: list[tuple[str, int]] = [
                (str(r[0]), int(r[1])) for r in cur.fetchall()
            ]
    return rows


def show(
    thought_id: UUID | str, *, config: MuninConfig | None = None
) -> Thought | None:
    """Return the full Thought for thought_id, or None if not found."""
    uid = (
        thought_id if isinstance(thought_id, UUID) else UUID(str(thought_id))
    )
    pool = get_pool(config)
    pool.open(wait=True)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, content, project, scope, tags, metadata, created_at, updated_at"
                " FROM thoughts WHERE id = %s",
                (uid,),
            )
            row: Any = cur.fetchone()
    if row is None:
        return None
    return Thought(
        id=UUID(str(row[0])),
        content=str(row[1]),
        project=str(row[2]),
        scope=str(row[3]) if row[3] is not None else None,
        tags=list(row[4]),
        metadata=dict(row[5]),
        created_at=row[6],
        updated_at=row[7],
    )


def remember(
    content: str,
    *,
    project: str | None = None,
    scope: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    config: MuninConfig | None = None,
) -> UUID:
    """Store a thought, auto-detecting the current git project if needed.

    Args:
        content: The thought content to store.
        project: Project name. Resolved from git root if not provided.
        scope: Optional scope label.
        tags: Optional list of string tags. Defaults to [].
        metadata: Optional JSON-serialisable metadata dict. Defaults to {}.
        config: Optional config override; uses load() if not provided.

    Returns:
        UUID of the inserted (or upserted) thought row.

    Raises:
        MuninError: If project cannot be determined.
    """
    cfg = config if config is not None else load()

    resolved_project = project or _scope.current_project()
    if resolved_project is None:
        raise MuninError(
            "project could not be determined; pass project= or run from inside a git repo"
        )

    resolved_tags: list[str] = tags if tags is not None else []
    resolved_metadata: dict[str, Any] = metadata if metadata is not None else {}

    logger.info("remember: project=%s content_len=%d", resolved_project, len(content))
    vec = embed(content, config=cfg)
    # DR-003: fixed-precision formatting avoids repr() emitting 'nan'/'inf'.
    embedding_str = "[" + ",".join(f"{v:.8g}" for v in vec) + "]"

    pool = get_pool(cfg)
    pool.open(wait=True)

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT upsert_thought(%s, %s::vector, %s, %s, %s, %s::jsonb)",
                (
                    content,
                    embedding_str,
                    resolved_project,
                    scope,
                    resolved_tags,
                    json.dumps(resolved_metadata),
                ),
            )
            row = cur.fetchone()

    if row is None:
        raise MuninError("upsert_thought returned no row")
    return UUID(str(row[0]))


def forget(
    thought_id: UUID | str, *, config: MuninConfig | None = None
) -> bool:
    """Hard-delete a thought. Returns True if deleted, False if not found."""
    uid = (
        thought_id if isinstance(thought_id, UUID) else UUID(str(thought_id))
    )
    pool = get_pool(config)
    pool.open(wait=True)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM thoughts WHERE id = %s RETURNING id", (uid,)
            )
            row: Any = cur.fetchone()
    return row is not None
