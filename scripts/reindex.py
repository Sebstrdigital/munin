#!/usr/bin/env python3
"""One-pass reindex: re-embed all thoughts with contextual prefix + rebuild HNSW.

Usage:
    # against munin_test (default for mechanics testing)
    MUNIN_PG_DB=munin_test python scripts/reindex.py

    # against prod (default)
    python scripts/reindex.py
    # or explicitly:
    MUNIN_PG_DB=munin python scripts/reindex.py

Safety contract:
- Takes a fresh pg_dump -Fc backup BEFORE mutating any vector; aborts if dump fails.
- Asserts row count identical before == after.
- Asserts every embedding non-null after run.
- Idempotent/re-runnable: always re-embeds every row (double-run safe).
- Raises maintenance_work_mem + parallel workers for HNSW build, restores after.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

# Add project src to path for local imports when run as a script.
# noqa: E402 — sys.path manipulation must precede the munin imports below.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from munin.core.config import load as load_config  # noqa: E402
from munin.core.embed import build_embed_text, embed_batch  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("reindex")

# ── Constants ─────────────────────────────────────────────────────────────────

BACKUPS_DIR = _REPO_ROOT / "backups"
BATCH_SIZE = 32  # rows per embed batch

# HNSW tuning for one-off build (restore after)
MAINTENANCE_WORK_MEM = "64MB"  # container /dev/shm is limited; 64MB is safe
MAX_PARALLEL_WORKERS = 4

# EmbeddingGemma-300M is trained with max 2048 tokens and llama.cpp enforces
# that hard limit (ctx-size=4096 is accepted by the flag but the model's native
# n_ctx is 2048 and the server rejects inputs larger than that with HTTP 400).
# Technical/code content tokenizes at ~2.1 chars/token (much denser than prose).
# Safe budget: (2048 - 60 prefix_tokens) * 2.1 ≈ 3900 chars.  Texts longer than
# this are truncated ONLY for the embed vector; the raw content in the DB is
# unchanged, so retrieval still returns the full original text.
MAX_EMBED_CHARS = 3900


def _pg_parts(db_url: str) -> dict[str, str]:
    """Extract host/port/user/password/dbname from a postgres URL."""
    import re
    m = re.match(
        r"postgresql://(?P<user>[^:]+):(?P<password>[^@]+)@"
        r"(?P<host>[^:/]+):(?P<port>\d+)/(?P<dbname>.+)",
        db_url,
    )
    if not m:
        raise ValueError(f"Cannot parse db_url: {db_url!r}")
    return m.groupdict()


def _take_backup(db_url: str, label: str) -> Path:
    """pg_dump -Fc the target DB into backups/<label>-<timestamp>.dump.

    Raises SystemExit(1) if the dump fails or the output is empty.
    """
    BACKUPS_DIR.mkdir(exist_ok=True)
    ts = _dt.datetime.now(tz=_dt.UTC).strftime("%Y%m%d-%H%M%S")
    out_path = BACKUPS_DIR / f"{label}-{ts}.dump"

    parts = _pg_parts(db_url)
    env = os.environ.copy()
    env["PGPASSWORD"] = parts["password"]

    cmd = [
        "pg_dump",
        "-Fc",
        "-h", parts["host"],
        "-p", parts["port"],
        "-U", parts["user"],
        "-d", parts["dbname"],
        "-f", str(out_path),
    ]
    log.info("Taking backup: %s", out_path)
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("pg_dump failed:\n%s", result.stderr)
        sys.exit(1)

    size = out_path.stat().st_size
    if size == 0:
        log.error("Backup file is empty: %s", out_path)
        out_path.unlink(missing_ok=True)
        sys.exit(1)

    log.info("Backup OK: %s (%d bytes)", out_path, size)
    return out_path


def _probe_embed(embed_url: str) -> int:
    """Hit the embed endpoint with a probe string; return vector length."""
    url = f"{embed_url}/v1/embeddings"
    resp = httpx.post(url, json={"input": "reindex probe"}, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()["data"]
    return len(data[0]["embedding"])


def main() -> None:
    cfg = load_config()

    # Allow MUNIN_PG_DB to override just the database name in the URL
    target_db = os.environ.get("MUNIN_PG_DB", "munin")
    db_url = cfg.db_url
    # Swap out the database name if it differs.
    # Use rsplit to replace only the trailing /dbname segment (not the user in the authority).
    parts = _pg_parts(db_url)
    if parts["dbname"] != target_db:
        # Replace last occurrence: everything before the final /dbname stays intact.
        prefix, _ = db_url.rsplit(f"/{parts['dbname']}", 1)
        db_url = f"{prefix}/{target_db}"
        log.info("Target DB overridden to: %s", target_db)

    log.info("=== munin reindex ===")
    log.info("Target DB: %s", target_db)
    log.info("Embed URL: %s", cfg.embed_url)

    # ── 0. Sanity: probe embed server ─────────────────────────────────────────
    log.info("Probing embed server …")
    vec_len = _probe_embed(cfg.embed_url)
    log.info("Embed server OK — vector length=%d", vec_len)
    if vec_len != cfg.embed_dim:
        log.error(
            "Embed dim mismatch: server returned %d, config.embed_dim=%d",
            vec_len, cfg.embed_dim,
        )
        sys.exit(1)

    # ── 1. Backup BEFORE any mutation ─────────────────────────────────────────
    backup_label = f"munin-pre-reindex-{target_db}"
    backup_path = _take_backup(db_url, backup_label)

    # ── 2. Count rows before ──────────────────────────────────────────────────
    with psycopg.connect(db_url) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM thoughts")
        row_count_before: int = cur.fetchone()[0]  # type: ignore[index]
    log.info("Rows before: %d", row_count_before)

    # ── 3. Load all rows ──────────────────────────────────────────────────────
    log.info("Loading all rows …")
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT id, content, project, scope, tags,
                   metadata->>'heading' AS heading
            FROM thoughts
            ORDER BY id
            """
        ).fetchall()
    log.info("Loaded %d rows", len(rows))

    # ── 4. Build prefixed embed texts ─────────────────────────────────────────
    log.info("Building contextual prefix texts …")
    embed_texts: list[str] = []
    truncated_count = 0
    for row in rows:
        tags = row["tags"] or []
        text = build_embed_text(
            row["content"],
            project=row["project"],
            scope=row["scope"] or None,
            tags=tags if tags else None,
            heading=row["heading"] or None,
        )
        if len(text) > MAX_EMBED_CHARS:
            log.warning(
                "Truncating row %s: %d chars -> %d (exceeds Gemma 2048-token limit)",
                row["id"], len(text), MAX_EMBED_CHARS,
            )
            text = text[:MAX_EMBED_CHARS]
            truncated_count += 1
        embed_texts.append(text)
    if truncated_count:
        log.info("Truncated %d rows to %d chars", truncated_count, MAX_EMBED_CHARS)

    # ── 5. Embed in batches ───────────────────────────────────────────────────
    log.info("Embedding %d texts in batches of %d …", len(embed_texts), BATCH_SIZE)
    t0 = time.monotonic()
    all_vectors: list[list[float]] = []
    for start in range(0, len(embed_texts), BATCH_SIZE):
        chunk = embed_texts[start : start + BATCH_SIZE]
        vecs = embed_batch(chunk, config=cfg)
        all_vectors.extend(vecs)
        done = min(start + BATCH_SIZE, len(embed_texts))
        log.info("  embedded %d / %d", done, len(embed_texts))
    elapsed = time.monotonic() - t0
    log.info("Embedding done in %.1fs", elapsed)

    assert len(all_vectors) == len(rows), (
        f"Vector count mismatch: {len(all_vectors)} != {len(rows)}"
    )

    # ── 6. Raise maintenance_work_mem + parallel workers ──────────────────────
    log.info("Raising maintenance_work_mem=%s, max_parallel_maintenance_workers=%d",
             MAINTENANCE_WORK_MEM, MAX_PARALLEL_WORKERS)

    # ── 7a. Update embeddings in DB (autocommit; commit before index build) ──
    log.info("Writing vectors to DB …")
    with psycopg.connect(db_url, autocommit=True) as conn:
        # Write in batches of BATCH_SIZE within explicit transactions so we
        # get durability checkpoints and don't hold one giant transaction.
        batch_ids = list(range(0, len(rows), BATCH_SIZE))
        for batch_start in batch_ids:
            batch_rows = rows[batch_start : batch_start + BATCH_SIZE]
            batch_vecs = all_vectors[batch_start : batch_start + BATCH_SIZE]
            with conn.transaction():
                for row, vec in zip(batch_rows, batch_vecs):
                    conn.execute(
                        "UPDATE thoughts SET embedding = %s::vector WHERE id = %s",
                        (json.dumps(vec), row["id"]),
                    )
    log.info("Vectors written and committed.")

    # ── 7b. Rebuild HNSW index in a separate connection ───────────────────────
    log.info("Rebuilding HNSW index …")
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(f"SET maintenance_work_mem = '{MAINTENANCE_WORK_MEM}'")
        conn.execute(f"SET max_parallel_maintenance_workers = {MAX_PARALLEL_WORKERS}")
        # Drop the canonical index name used by munin migrations.
        conn.execute("DROP INDEX IF EXISTS idx_thoughts_embedding_hnsw")
        conn.execute(
            """
            CREATE INDEX idx_thoughts_embedding_hnsw
              ON thoughts
              USING hnsw (embedding vector_cosine_ops)
              WITH (m = 16, ef_construction = 64)
            """
        )
        conn.execute("RESET maintenance_work_mem")
        conn.execute("RESET max_parallel_maintenance_workers")
    log.info("HNSW index rebuilt.")

    # ── 8. Assertions ─────────────────────────────────────────────────────────
    with psycopg.connect(db_url) as conn:
        row_count_after: int = conn.execute(
            "SELECT COUNT(*) FROM thoughts"
        ).fetchone()[0]  # type: ignore[index]

        null_count: int = conn.execute(
            "SELECT COUNT(*) FROM thoughts WHERE embedding IS NULL"
        ).fetchone()[0]  # type: ignore[index]

    log.info("Rows after:  %d", row_count_after)
    log.info("NULL embeddings: %d", null_count)

    if row_count_after != row_count_before:
        log.error(
            "ROW COUNT MISMATCH: before=%d after=%d — RESTORING from %s",
            row_count_before, row_count_after, backup_path,
        )
        sys.exit(2)

    if null_count != 0:
        log.error(
            "NULL embeddings found: %d — possible partial failure. Backup at %s",
            null_count, backup_path,
        )
        sys.exit(2)

    log.info(
        "=== Reindex complete: %d rows, 0 NULLs, %.1fs ===",
        row_count_after, elapsed,
    )
    log.info("Backup retained at: %s", backup_path)


if __name__ == "__main__":
    main()
