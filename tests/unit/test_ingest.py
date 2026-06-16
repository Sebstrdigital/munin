"""Unit tests for core.ingest."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from munin.core.ingest import IngestResult, _relativize, ingest


def test_relativize_returns_relative_path() -> None:
    """Relativize returns path relative to root when subpath."""
    root = Path("/home/user/project")
    path = Path("/home/user/project/docs/readme.md")
    result = _relativize(path, root)
    assert result == "docs/readme.md"


def test_relativize_returns_full_path_when_not_subpath() -> None:
    """Relativize returns full path when not under root."""
    root = Path("/home/user/project")
    path = Path("/home/other/docs/readme.md")
    result = _relativize(path, root)
    assert result == "/home/other/docs/readme.md"


def test_ingest_dry_run_no_db_calls(tmp_path: Path) -> None:
    """Dry run does not call database."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "test.md").write_text("# Hello\n\nContent here.")

    manifest = tmp_path / "sources.toml"
    manifest.write_text(f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*.md"]
project = "test-project"
scope = "backend"
tags = ["docs"]
""")

    with patch("munin.core.ingest._load_sources") as mock_load:
        mock_load.return_value = [
            MagicMock(
                path=docs_dir,
                globs=["**/*.md"],
                project="test-project",
                scope="backend",
                tags=["docs"],
            )
        ]

        result = ingest(sources_path=manifest, dry_run=True)

        assert result.files_scanned == 1
        assert result.chunks_would_store == 1
        assert result.chunks_stored == 0
        assert result.chunks_skipped == 0
        assert result.failures == 0


def test_ingest_skips_non_file_paths(tmp_path: Path) -> None:
    """Ignores directories in glob matches."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    manifest = tmp_path / "sources.toml"
    manifest.write_text(f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*"]
project = "test"
""")

    with patch("munin.core.ingest._load_sources") as mock_load:
        mock_load.return_value = [
            MagicMock(path=docs_dir, globs=["**/*"], project="test", scope=None, tags=[])
        ]

        result = ingest(sources_path=manifest, dry_run=True)

        assert result.files_scanned == 0


def test_ingest_returns_result_object(tmp_path: Path) -> None:
    """Returns IngestResult with all fields populated."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    manifest = tmp_path / "sources.toml"
    manifest.write_text(f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*.md"]
project = "test"
""")

    with patch("munin.core.ingest._load_sources") as mock_load:
        mock_load.return_value = [
            MagicMock(path=docs_dir, globs=["**/*.md"], project="test", scope=None, tags=[])
        ]

        result = ingest(sources_path=manifest, dry_run=True)

        assert isinstance(result, IngestResult)
        assert result.files_scanned == 0
        assert result.chunks_stored == 0
        assert result.chunks_skipped == 0
        assert result.failures == 0


# ---------------------------------------------------------------------------
# Non-dry-run write-path tests (DR-011)
# ---------------------------------------------------------------------------

def _make_pool_mock(fetchone_return: tuple | None) -> MagicMock:
    """Build a mock pool whose cursor.fetchone returns the given row."""
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = fetchone_return
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn
    return mock_pool


def test_new_chunk_stored(tmp_path: Path) -> None:
    """SELECT returns None → embed called → upsert_thought called → chunks_stored=1."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "note.md").write_text("# Hello\n\nSome content.")

    manifest = tmp_path / "sources.toml"
    manifest.write_text(f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*.md"]
project = "proj"
""")

    mock_pool = _make_pool_mock(fetchone_return=None)

    with (
        patch("munin.core.ingest._load_sources") as mock_load,
        patch("munin.core.ingest.get_pool", return_value=mock_pool),
        patch("munin.core.ingest.embed_batch_fn", return_value=[[0.1] * 768]) as mock_embed,
    ):
        mock_load.return_value = [
            MagicMock(path=docs_dir, globs=["**/*.md"], project="proj", scope=None, tags=[])
        ]

        result = ingest(sources_path=manifest, dry_run=False)

    assert result.chunks_stored == 1
    assert result.chunks_skipped == 0
    mock_embed.assert_called_once()

    # Cursor calls: SELECT then upsert_thought (no DELETE)
    mock_conn = mock_pool.connection.return_value.__enter__.return_value
    mock_cur = mock_conn.cursor.return_value.__enter__.return_value
    execute_calls = mock_cur.execute.call_args_list
    sql_stmts = [str(c.args[0]) for c in execute_calls]
    assert any("SELECT" in s and "thoughts" in s for s in sql_stmts)
    assert any("upsert_thought" in s for s in sql_stmts)
    assert not any("DELETE" in s for s in sql_stmts)


def test_unchanged_chunk_skipped(tmp_path: Path) -> None:
    """Matching fingerprint → embed NOT called, upsert NOT called, chunks_skipped=1.

    US-005: fingerprint check now runs BEFORE embed, so unchanged chunks make
    zero embedding HTTP calls.
    """
    import hashlib

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    content = "# Hello\n\nSome content."
    (docs_dir / "note.md").write_text(content)

    manifest = tmp_path / "sources.toml"
    manifest.write_text(f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*.md"]
project = "proj"
""")

    # Derive the fingerprint the ingest code will produce so the SELECT row
    # looks like an exact match.
    from munin.core.chunker import chunk_markdown

    chunks = chunk_markdown(content, "note.md")
    assert chunks, "chunker produced no chunks"
    fingerprint = hashlib.md5(chunks[0].content.encode()).hexdigest()

    mock_pool = _make_pool_mock(fetchone_return=(42, fingerprint))

    with (
        patch("munin.core.ingest._load_sources") as mock_load,
        patch("munin.core.ingest.get_pool", return_value=mock_pool),
        patch("munin.core.ingest.embed_batch_fn", return_value=[[0.1] * 768]) as mock_embed,
    ):
        mock_load.return_value = [
            MagicMock(path=docs_dir, globs=["**/*.md"], project="proj", scope=None, tags=[])
        ]

        result = ingest(sources_path=manifest, dry_run=False)

    assert result.chunks_skipped == 1
    assert result.chunks_stored == 0
    # US-005: embed must NOT be called for unchanged content.
    mock_embed.assert_not_called()

    # upsert_thought and DELETE must NOT appear — chunk was skipped.
    mock_conn = mock_pool.connection.return_value.__enter__.return_value
    mock_cur = mock_conn.cursor.return_value.__enter__.return_value
    execute_calls = mock_cur.execute.call_args_list
    sql_stmts = [str(c.args[0]) for c in execute_calls]
    assert not any("upsert_thought" in s for s in sql_stmts)
    assert not any("DELETE" in s for s in sql_stmts)


def test_changed_chunk_updated(tmp_path: Path) -> None:
    """Different fingerprint → embed called → DELETE → upsert_thought, chunks_stored=1."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "note.md").write_text("# Hello\n\nUpdated content.")

    manifest = tmp_path / "sources.toml"
    manifest.write_text(f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*.md"]
project = "proj"
""")

    # Row exists (id=99) but fingerprint is stale.
    mock_pool = _make_pool_mock(fetchone_return=(99, "stale_fingerprint_abc"))

    with (
        patch("munin.core.ingest._load_sources") as mock_load,
        patch("munin.core.ingest.get_pool", return_value=mock_pool),
        patch("munin.core.ingest.embed_batch_fn", return_value=[[0.1] * 768]) as mock_embed,
    ):
        mock_load.return_value = [
            MagicMock(path=docs_dir, globs=["**/*.md"], project="proj", scope=None, tags=[])
        ]

        result = ingest(sources_path=manifest, dry_run=False)

    assert result.chunks_stored == 1
    assert result.chunks_skipped == 0
    mock_embed.assert_called_once()

    mock_conn = mock_pool.connection.return_value.__enter__.return_value
    mock_cur = mock_conn.cursor.return_value.__enter__.return_value
    execute_calls = mock_cur.execute.call_args_list
    sql_stmts = [str(c.args[0]) for c in execute_calls]
    assert any("SELECT" in s and "thoughts" in s for s in sql_stmts)
    assert any("DELETE" in s for s in sql_stmts)
    assert any("upsert_thought" in s for s in sql_stmts)


def test_multi_chunk_batches_embed_calls(tmp_path: Path) -> None:
    """Multiple new chunks → embed_batch_fn called ONCE with all contents, not once per chunk.

    US-005 AC2: a fresh ingest of multiple chunks sends BATCHED embedding
    requests via embed_batch() rather than one HTTP POST per chunk.
    """
    from munin.core.chunker import chunk_markdown

    # Build a file that produces multiple chunks (each H2 heading = new chunk).
    content = (
        "# Doc\n\n## Section A\n\nContent A.\n\n"
        "## Section B\n\nContent B.\n\n## Section C\n\nContent C.\n"
    )
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "multi.md").write_text(content)

    manifest = tmp_path / "sources.toml"
    manifest.write_text(f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*.md"]
project = "proj"
""")

    # Verify the file actually produces multiple chunks so the test is meaningful.
    produced_chunks = chunk_markdown(content, "multi.md")
    assert len(produced_chunks) > 1, (
        f"Expected >1 chunks from test file, got {len(produced_chunks)}"
    )
    n_chunks = len(produced_chunks)

    # All chunks are new (fetchone returns None for every SELECT).
    mock_pool = _make_pool_mock(fetchone_return=None)

    # Return one vector per chunk — embed_batch_fn receives all contents at once.
    mock_vectors = [[float(i) / 768] * 768 for i in range(n_chunks)]

    with (
        patch("munin.core.ingest._load_sources") as mock_load,
        patch("munin.core.ingest.get_pool", return_value=mock_pool),
        patch("munin.core.ingest.embed_batch_fn", return_value=mock_vectors) as mock_embed,
    ):
        mock_load.return_value = [
            MagicMock(path=docs_dir, globs=["**/*.md"], project="proj", scope=None, tags=[])
        ]

        result = ingest(sources_path=manifest, dry_run=False)

    assert result.chunks_stored == n_chunks
    assert result.chunks_skipped == 0

    # KEY assertion: embed_batch_fn is called exactly ONCE for the whole file,
    # with ALL chunk contents in a single list — not once per chunk.
    assert mock_embed.call_count == 1, (
        f"embed_batch_fn called {mock_embed.call_count} times; expected 1 "
        f"(true batching: all {n_chunks} chunks in one call)"
    )
    called_contents = mock_embed.call_args[0][0]
    assert len(called_contents) == n_chunks, (
        f"embed_batch_fn received {len(called_contents)} contents; expected {n_chunks}"
    )


def test_unchanged_chunks_excluded_from_batch(tmp_path: Path) -> None:
    """Mix of changed + unchanged chunks → only changed chunks sent to embed_batch_fn.

    Unchanged chunks must be skipped before embedding; the batch must contain
    only the contents that actually need (re-)embedding.
    """
    import hashlib

    from munin.core.chunker import chunk_markdown

    content = "# Doc\n\n## Section A\n\nContent A.\n\n## Section B\n\nContent B.\n"
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "mixed.md").write_text(content)

    manifest = tmp_path / "sources.toml"
    manifest.write_text(f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*.md"]
project = "proj"
""")

    produced_chunks = chunk_markdown(content, "mixed.md")
    assert len(produced_chunks) >= 2, "Need at least 2 chunks for this test"

    # First chunk is unchanged (matching fingerprint); second is new (None).
    first_fp = hashlib.md5(produced_chunks[0].content.encode()).hexdigest()

    # fetchone is called:
    #   - once per chunk in pass-1 (fingerprint SELECT): (42, first_fp) then None
    #   - once in pass-3 after upsert_thought to consume the stored result: None
    fetchone_results = iter([(42, first_fp), None, None])

    mock_cur = MagicMock()
    mock_cur.fetchone.side_effect = lambda: next(fetchone_results)
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    with (
        patch("munin.core.ingest._load_sources") as mock_load,
        patch("munin.core.ingest.get_pool", return_value=mock_pool),
        patch("munin.core.ingest.embed_batch_fn", return_value=[[0.2] * 768]) as mock_embed,
    ):
        mock_load.return_value = [
            MagicMock(path=docs_dir, globs=["**/*.md"], project="proj", scope=None, tags=[])
        ]

        result = ingest(sources_path=manifest, dry_run=False)

    assert result.chunks_skipped == 1
    assert result.chunks_stored == 1

    # embed_batch_fn called once, with only the NEW chunk's embed text — not the
    # unchanged chunk.  Since P3-1 (contextual embedding prefix), the embed
    # receives build_embed_text(content, project=..., heading=...) rather than
    # raw content.  We verify the embed text contains the raw content as a
    # substring and that only one item was batched (the changed chunk).
    assert mock_embed.call_count == 1
    called_contents = mock_embed.call_args[0][0]
    assert len(called_contents) == 1, (
        f"Expected 1 content in batch (only the new chunk), got {len(called_contents)}"
    )
    assert produced_chunks[1].content in called_contents[0], (
        f"Embed text should contain raw chunk content.\n"
        f"  embed text:  {called_contents[0]!r}\n"
        f"  raw content: {produced_chunks[1].content!r}"
    )
