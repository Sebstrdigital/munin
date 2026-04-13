"""Ingest pipeline with provenance and hierarchy."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from munin.core.chunker import chunk_markdown
from munin.core.config import MuninConfig, load
from munin.core.db import get_pool
from munin.core.embed import embed as embed_fn
from munin.core.manifest import load_sources as _load_sources

logger = logging.getLogger(__name__)


@dataclass
class ChunkPreview:
    """Preview of a chunk that would be stored in dry-run mode."""

    source_file: str
    heading: str
    project: str
    scope: str | None
    tags: list[str]


@dataclass
class IngestResult:
    """Result of an ingest operation."""

    files_scanned: int
    chunks_stored: int
    chunks_skipped: int
    failures: int
    dry_run_chunks: list[ChunkPreview] | None = None


def _relativize(path: Path, root: Path) -> str:
    """Return path relative to root, or the full path if not a subpath."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def ingest(
    sources_path: Path | None = None,
    *,
    dry_run: bool = False,
    config: MuninConfig | None = None,
) -> IngestResult:
    """Ingest all sources from a manifest.

    Args:
        sources_path: Path to sources.toml. Defaults to ~/.config/munin/sources.toml
        dry_run: If True, only list what would be stored without writing to DB
        config: Optional config override

    Returns:
        IngestResult with counts of scanned/stored/skipped/failed items

    Raises:
        MuninError: If manifest not found or other errors
    """
    cfg = config if config is not None else load()
    sources = _load_sources(sources_path=sources_path)

    files_scanned = 0
    chunks_stored = 0
    chunks_skipped = 0
    failures = 0
    dry_run_chunks: list[ChunkPreview] = []

    for source in sources:
        for glob_pattern in source.globs:
            for file_path in sorted(source.path.glob(glob_pattern)):
                if not file_path.is_file():
                    continue

                files_scanned += 1

                try:
                    content = file_path.read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning("Failed to read %s: %s", file_path, e)
                    failures += 1
                    continue

                chunks = chunk_markdown(content, file_path.name)

                for chunk in chunks:
                    rel_path = _relativize(file_path, source.path)
                    metadata: dict[str, Any] = {
                        "source_path": rel_path,
                        "heading": chunk.heading,
                    }

                    if dry_run:
                        logger.info(
                            "[DRY-RUN] would store: project=%s scope=%s path=%s heading=%s",
                            source.project,
                            source.scope,
                            rel_path,
                            chunk.heading,
                        )
                        dry_run_chunks.append(
                            ChunkPreview(
                                source_file=rel_path,
                                heading=chunk.heading,
                                project=source.project,
                                scope=source.scope,
                                tags=list(source.tags) if source.tags else [],
                            )
                        )
                        chunks_stored += 1
                    else:
                        try:
                            fingerprint = hashlib.md5(
                                chunk.content.encode()
                            ).hexdigest()

                            pool = get_pool(cfg)
                            pool.open(wait=True)
                            with pool.connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "SELECT EXISTS("
                                        "  SELECT 1 FROM thoughts"
                                        "  WHERE project = %s"
                                        "    AND content_fingerprint = %s"
                                        ")",
                                        (source.project, fingerprint),
                                    )
                                    row = cur.fetchone()
                                    already_exists = row[0] if row else False

                            if already_exists:
                                chunks_skipped += 1
                            else:
                                vec = embed_fn(chunk.content, config=cfg)
                                vec_str = "[" + ",".join(map(repr, vec)) + "]"

                                with pool.connection() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute(
                                            "SELECT upsert_thought(%s, %s::vector, %s, %s, "
                                            "%s, %s::jsonb)",
                                            (
                                                chunk.content,
                                                vec_str,
                                                source.project,
                                                source.scope,
                                                source.tags,
                                                metadata,
                                            ),
                                        )
                                        cur.fetchone()
                                chunks_stored += 1
                        except Exception as e:
                            logger.warning("Failed to store chunk %s: %s", file_path, e)
                            failures += 1

    return IngestResult(
        files_scanned=files_scanned,
        chunks_stored=chunks_stored,
        chunks_skipped=chunks_skipped,
        failures=failures,
        dry_run_chunks=dry_run_chunks if dry_run else None,
    )
