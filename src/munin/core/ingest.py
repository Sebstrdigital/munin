"""Ingest pipeline with provenance and hierarchy."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from munin.core.chunker import Chunk, chunk_markdown
from munin.core.config import MuninConfig, load
from munin.core.db import get_pool
from munin.core.embed import embed_batch as embed_batch_fn
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
    chunks_would_store: int = 0


@dataclass
class _PendingChunk:
    """Internal record for a chunk that passed fingerprint filtering and needs embedding."""

    chunk: Chunk
    rel_path: str
    fingerprint: str
    existing_id: int | None
    project: str
    scope: str | None
    tags: list[str]
    metadata: dict[str, Any]
    file_path: Path


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

    Embedding requests are batched across all chunks that need updating in a
    single pass: fingerprints are checked first (SELECTs), then all contents
    that need (re-)embedding are sent to embed_batch_fn in one call (or in
    batches of config.embed_batch_size handled internally by embed_batch),
    then vectors are mapped back for upsert.

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
    chunks_would_store = 0
    failures = 0
    dry_run_chunks: list[ChunkPreview] = []

    # DR-001: pool is lazy-opened on first write; closed in finally to prevent leak.
    pool = None

    try:
        for source in sources:
            for glob_pattern in source.globs:
                for file_path in sorted(source.path.glob(glob_pattern)):
                    if not file_path.is_file():
                        continue

                    # DR-008: symlink containment — reject paths that escape the
                    # declared source root after symlink resolution.
                    if not str(file_path.resolve()).startswith(
                        str(source.path.resolve())
                    ):
                        logger.warning(
                            "Skipping symlink escape: %s escapes %s",
                            file_path,
                            source.path,
                        )
                        continue

                    # DR-009: skip files larger than 1 MB to avoid OOM on huge blobs.
                    try:
                        file_size = file_path.stat().st_size
                    except OSError as e:
                        logger.warning("Failed to stat %s: %s", file_path, e)
                        failures += 1
                        continue

                    if file_size > 1_048_576:
                        logger.warning(
                            "Skipping oversized file %s (%d bytes > 1 MB)",
                            file_path,
                            file_size,
                        )
                        continue

                    files_scanned += 1

                    try:
                        content = file_path.read_text(encoding="utf-8")
                    except Exception as e:
                        logger.warning("Failed to read %s: %s", file_path, e)
                        failures += 1
                        continue

                    chunks = chunk_markdown(content, file_path.name)

                    if dry_run:
                        for chunk in chunks:
                            rel_path = _relativize(file_path, source.path)
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
                            # DR-012: dry_run counts go to chunks_would_store,
                            # not chunks_stored, to preserve semantic accuracy.
                            chunks_would_store += 1
                        continue

                    # --- Non-dry-run write path ---

                    # DR-001: lazy-open pool only when first write needed.
                    if pool is None:
                        pool = get_pool(cfg)
                        pool.open(wait=True)

                    # Pass 1: fingerprint-check all chunks for this file.
                    # Collect those that need (re-)embedding into pending list;
                    # skip unchanged chunks without touching the embed server.
                    pending: list[_PendingChunk] = []

                    for chunk in chunks:
                        rel_path = _relativize(file_path, source.path)
                        fingerprint = hashlib.md5(chunk.content.encode()).hexdigest()
                        metadata: dict[str, Any] = {
                            "source_path": rel_path,
                            "heading": chunk.heading,
                        }

                        try:
                            with pool.connection() as conn:
                                with conn.cursor() as cur:
                                    # Look up existing thought by source identity key,
                                    # not by content fingerprint — so changed content
                                    # is detected as an update rather than a new insert.
                                    cur.execute(
                                        "SELECT id, content_fingerprint"
                                        " FROM thoughts"
                                        " WHERE project = %s"
                                        "   AND metadata->>'source_path' = %s"
                                        "   AND metadata->>'heading' = %s"
                                        " LIMIT 1",
                                        (
                                            source.project,
                                            rel_path,
                                            chunk.heading,
                                        ),
                                    )
                                    row = cur.fetchone()

                            existing_id = row[0] if row is not None else None
                            existing_fingerprint = row[1] if row is not None else None

                            if (
                                existing_id is not None
                                and existing_fingerprint == fingerprint
                            ):
                                # Content unchanged — skip WITHOUT making any
                                # embedding HTTP call.
                                chunks_skipped += 1
                                continue

                            pending.append(
                                _PendingChunk(
                                    chunk=chunk,
                                    rel_path=rel_path,
                                    fingerprint=fingerprint,
                                    existing_id=existing_id,
                                    project=source.project,
                                    scope=source.scope,
                                    tags=list(source.tags) if source.tags else [],
                                    metadata=metadata,
                                    file_path=file_path,
                                )
                            )
                        except Exception as e:
                            logger.warning(
                                "Failed fingerprint check for chunk %s: %s",
                                file_path,
                                e,
                            )
                            failures += 1

                    if not pending:
                        continue

                    # Pass 2: batch-embed all pending contents in ONE call.
                    # embed_batch internally batches by cfg.embed_batch_size, so
                    # passing the full list here achieves true batching — one HTTP
                    # POST per embed_batch_size chunks, not one per chunk.
                    try:
                        contents = [p.chunk.content for p in pending]
                        vectors = embed_batch_fn(contents, config=cfg)
                    except Exception as e:
                        logger.warning(
                            "embed_batch failed for file %s: %s", file_path, e
                        )
                        failures += len(pending)
                        continue

                    # Pass 3: write each pending chunk with its vector.
                    for p, vec in zip(pending, vectors):
                        # DR-003: use fixed-precision formatting instead of repr()
                        # which can emit 'nan'/'inf' — invalid pgvector literals.
                        vec_str = "[" + ",".join(f"{v:.8g}" for v in vec) + "]"

                        try:
                            with pool.connection() as conn:
                                with conn.transaction():
                                    with conn.cursor() as cur:
                                        # DR-006 + DR-007: single connection for
                                        # DELETE and upsert to keep both atomic.
                                        # upsert_thought ON CONFLICT only updates
                                        # tags/metadata — not content or embedding —
                                        # so a DELETE before upsert is still required
                                        # when content changed.
                                        if p.existing_id is not None:
                                            logger.debug(
                                                "ingest: updating changed chunk"
                                                " path=%s heading=%s",
                                                p.rel_path,
                                                p.chunk.heading,
                                            )
                                            cur.execute(
                                                "DELETE FROM thoughts"
                                                " WHERE id = %s",
                                                (p.existing_id,),
                                            )
                                        cur.execute(
                                            "SELECT upsert_thought("
                                            "%s, %s::vector,"
                                            " %s, %s, %s, %s::jsonb)",
                                            (
                                                p.chunk.content,
                                                vec_str,
                                                p.project,
                                                p.scope,
                                                p.tags,
                                                json.dumps(p.metadata),
                                            ),
                                        )
                                        cur.fetchone()
                            chunks_stored += 1
                        except Exception as e:
                            logger.warning(
                                "Failed to store chunk %s: %s", p.file_path, e
                            )
                            failures += 1

    finally:
        # DR-001: always close pool to release connections, even on exception.
        # Wrapped in try/except so teardown errors don't shadow the original.
        if pool is not None:
            try:
                pool.close()
            except Exception as close_err:
                logger.warning("pool.close() failed: %s", close_err)

    return IngestResult(
        files_scanned=files_scanned,
        chunks_stored=chunks_stored,
        chunks_skipped=chunks_skipped,
        failures=failures,
        dry_run_chunks=dry_run_chunks if dry_run else None,
        chunks_would_store=chunks_would_store,
    )
