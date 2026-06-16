"""Unit tests for final-review-cycle-1 fixes.

Covers:
  - F4: MMR does not leapfrog — a high-fused/low-rerank tail item must not
        rank above a top reranked item when reranking is active.
  - F2: list_projects excludes superseded/expired rows (lifecycle filter).
  - B1: show() returns superseded_by, valid_from, valid_to fields.
  - MCP M1/M2/M3: recall exposes include_history; remember exposes heading;
        recall response includes fused_score and rerank_score.
  - M4: CLI remember passes heading; _extract_heading extracts H1 and title.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import frontmatter
import pytest

_FAKE_UUID = UUID("12345678-1234-5678-1234-567812345678")
_FAKE_UUID2 = UUID("87654321-4321-8765-4321-876543218765")
_FAKE_PROJECT = "munin"
_NOW = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# F4: MMR mixed-scale leapfrog test
# ---------------------------------------------------------------------------


class TestMmrNoLeapfrog:
    """A high-fused-score/no-rerank-score tail item must not leapfrog a top
    reranked item when reranking is active.

    Before the F4 fix, candidates = reranked + tail meant that when normalising
    all scores together, a tail item with fused_score=0.9 (no rerank_score) and
    a reranked item with rerank_score=-10 (low logit) had min_score=-10, so the
    tail item normalized near 1.0 and was placed above well-reranked items.

    After the fix, candidates = reranked only (tail discarded), so only
    cross-encoder scores participate in normalization.
    """

    def test_top_reranked_item_stays_first(self) -> None:
        from munin.core.memory import ThoughtResult, _mmr_rerank

        top_reranked = ThoughtResult(
            id=UUID("aaaaaaaa-0000-0000-0000-000000000001"),
            content="best reranked",
            project="p",
            scope=None,
            tags=[],
            metadata={},
            similarity=0.5,
            fused_score=0.6,
            created_at=_NOW,
            rerank_score=2.8,  # high cross-encoder score
        )
        second_reranked = ThoughtResult(
            id=UUID("aaaaaaaa-0000-0000-0000-000000000002"),
            content="second reranked",
            project="p",
            scope=None,
            tags=[],
            metadata={},
            similarity=0.4,
            fused_score=0.5,
            created_at=_NOW,
            rerank_score=1.2,
        )
        # Tail item: no rerank_score, but high fused_score (the old leapfrog case).
        tail_item = ThoughtResult(
            id=UUID("aaaaaaaa-0000-0000-0000-000000000003"),
            content="tail item high fused",
            project="p",
            scope=None,
            tags=[],
            metadata={},
            similarity=0.9,
            fused_score=0.95,  # artificially high
            created_at=_NOW,
            rerank_score=None,  # no cross-encoder score
        )

        # F4 fix: candidates = reranked only (tail_item excluded).
        candidates = [top_reranked, second_reranked]
        # Orthogonal embeddings so diversity penalty is zero.
        embeddings = {
            top_reranked.id: [1.0] + [0.0] * 767,
            second_reranked.id: [0.0, 1.0] + [0.0] * 766,
            tail_item.id: [0.0] * 767 + [1.0],
        }

        result = _mmr_rerank(candidates, embeddings, lambda_=0.7, k=2)

        assert result[0].id == top_reranked.id, (
            f"top_reranked must be first; got {result[0].id}"
        )
        # tail_item must not appear — it was not in candidates.
        result_ids = {r.id for r in result}
        assert tail_item.id not in result_ids

    def test_reranked_single_item_is_returned(self) -> None:
        """Even a reranked item with negative logit is returned correctly."""
        from munin.core.memory import ThoughtResult, _mmr_rerank

        top_reranked = ThoughtResult(
            id=UUID("bbbbbbbb-0000-0000-0000-000000000001"),
            content="reranked winner",
            project="p",
            scope=None,
            tags=[],
            metadata={},
            similarity=0.6,
            fused_score=0.7,
            created_at=_NOW,
            rerank_score=-8.0,  # low logit but only candidate
        )
        candidates = [top_reranked]
        embeddings = {top_reranked.id: [1.0] + [0.0] * 767}

        result = _mmr_rerank(candidates, embeddings, lambda_=0.7, k=1)

        assert len(result) == 1
        assert result[0].id == top_reranked.id


# ---------------------------------------------------------------------------
# F2: list_projects lifecycle filter (unit — mocked DB)
# ---------------------------------------------------------------------------


class TestListProjectsLifecycleFilter:
    """list_projects() SQL must include WHERE superseded_by IS NULL AND valid_to IS NULL."""

    def test_sql_contains_lifecycle_filter(self) -> None:
        from munin.core.memory import list_projects

        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = [("alpha", 3), ("beta", 7)]

        conn = MagicMock()
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur

        pool = MagicMock()
        pool.connection.return_value = conn

        with patch("munin.core.memory.get_pool", return_value=pool):
            result = list_projects()

        assert result == [("alpha", 3), ("beta", 7)]
        sql_arg: str = cur.execute.call_args[0][0]
        assert "superseded_by IS NULL" in sql_arg
        assert "valid_to IS NULL" in sql_arg


# ---------------------------------------------------------------------------
# B1: show() returns lifecycle fields
# ---------------------------------------------------------------------------


class TestShowLifecycleFields:
    """show() must return superseded_by, valid_from, valid_to from the DB row."""

    def _mock_pool(self, fetchone_return: object) -> MagicMock:
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchone.return_value = fetchone_return

        conn = MagicMock()
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur

        pool = MagicMock()
        pool.connection.return_value = conn
        return pool

    def test_live_row_has_null_superseded_and_valid_to(self) -> None:
        from munin.core.memory import show

        # Row: id, content, project, scope, tags, metadata, created_at, updated_at,
        #      superseded_by, valid_from, valid_to
        pool = self._mock_pool((
            _FAKE_UUID, "content", "proj", None, [], {}, _NOW, _NOW,
            None, _NOW, None,
        ))
        with patch("munin.core.memory.get_pool", return_value=pool):
            thought = show(_FAKE_UUID)

        assert thought is not None
        assert thought.superseded_by is None
        assert thought.valid_from == _NOW
        assert thought.valid_to is None

    def test_retired_row_has_superseded_by_and_valid_to(self) -> None:
        from munin.core.memory import show

        superseder = UUID("deadbeef-dead-beef-dead-beefdeadbeef")
        pool = self._mock_pool((
            _FAKE_UUID, "old content", "proj", None, [], {}, _NOW, _NOW,
            superseder, _NOW, _NOW,
        ))
        with patch("munin.core.memory.get_pool", return_value=pool):
            thought = show(_FAKE_UUID)

        assert thought is not None
        assert thought.superseded_by == superseder
        assert thought.valid_to == _NOW

    def test_show_sql_fetches_lifecycle_columns(self) -> None:
        from munin.core.memory import show

        pool = self._mock_pool(None)
        with patch("munin.core.memory.get_pool", return_value=pool):
            show(_FAKE_UUID)

        cur = pool.connection.return_value.cursor.return_value
        sql_arg: str = cur.execute.call_args[0][0]
        assert "superseded_by" in sql_arg
        assert "valid_from" in sql_arg
        assert "valid_to" in sql_arg


# ---------------------------------------------------------------------------
# MCP fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_project() -> Generator[None, None, None]:
    import munin.mcp.server as srv

    original = srv._project
    srv._project = _FAKE_PROJECT
    yield
    srv._project = original


def _make_thought_result(rerank_score: float | None = None) -> object:
    from munin.core.memory import ThoughtResult

    return ThoughtResult(
        id=_FAKE_UUID,
        content="test content",
        project=_FAKE_PROJECT,
        scope=None,
        tags=[],
        metadata={},
        similarity=0.8,
        fused_score=0.72,
        created_at=_NOW,
        rerank_score=rerank_score,
    )


# ---------------------------------------------------------------------------
# MCP M1: recall expose include_history
# ---------------------------------------------------------------------------


class TestMcpRecallIncludeHistory:
    def test_passes_include_history_true(self) -> None:
        with patch("munin.mcp.server.memory.recall", return_value=[]) as mock_rec:
            from munin.mcp.server import recall

            recall(query="q", include_history=True)

        mock_rec.assert_called_once_with(
            "q",
            project=_FAKE_PROJECT,
            scope=None,
            limit=10,
            threshold=0.0,
            include_history=True,
        )

    def test_passes_include_history_false_by_default(self) -> None:
        with patch("munin.mcp.server.memory.recall", return_value=[]) as mock_rec:
            from munin.mcp.server import recall

            recall(query="q")

        mock_rec.assert_called_once_with(
            "q",
            project=_FAKE_PROJECT,
            scope=None,
            limit=10,
            threshold=0.0,
            include_history=False,
        )


# ---------------------------------------------------------------------------
# MCP M2: remember expose heading
# ---------------------------------------------------------------------------


class TestMcpRememberHeading:
    def test_passes_heading_to_core(self) -> None:
        with patch("munin.mcp.server.memory.remember", return_value=_FAKE_UUID) as mock_rem:
            from munin.mcp.server import remember

            remember(content="text", heading="## My Section")

        mock_rem.assert_called_once_with(
            "text",
            project=_FAKE_PROJECT,
            scope=None,
            tags=None,
            metadata=None,
            heading="## My Section",
        )

    def test_heading_defaults_to_none(self) -> None:
        with patch("munin.mcp.server.memory.remember", return_value=_FAKE_UUID) as mock_rem:
            from munin.mcp.server import remember

            remember(content="text")

        mock_rem.assert_called_once_with(
            "text",
            project=_FAKE_PROJECT,
            scope=None,
            tags=None,
            metadata=None,
            heading=None,
        )


# ---------------------------------------------------------------------------
# MCP M3: recall response includes fused_score and rerank_score
# ---------------------------------------------------------------------------


class TestMcpRecallScoresInResponse:
    def test_fused_score_present_in_result(self) -> None:
        thought = _make_thought_result()
        with patch("munin.mcp.server.memory.recall", return_value=[thought]):
            from munin.mcp.server import recall

            result = recall(query="q")

        item = result["results"][0]
        assert "fused_score" in item
        assert item["fused_score"] == pytest.approx(0.72)

    def test_rerank_score_present_when_set(self) -> None:
        thought = _make_thought_result(rerank_score=2.5)
        with patch("munin.mcp.server.memory.recall", return_value=[thought]):
            from munin.mcp.server import recall

            result = recall(query="q")

        item = result["results"][0]
        assert "rerank_score" in item
        assert item["rerank_score"] == pytest.approx(2.5)

    def test_rerank_score_is_none_when_not_set(self) -> None:
        thought = _make_thought_result(rerank_score=None)
        with patch("munin.mcp.server.memory.recall", return_value=[thought]):
            from munin.mcp.server import recall

            result = recall(query="q")

        item = result["results"][0]
        assert item["rerank_score"] is None


# ---------------------------------------------------------------------------
# B1 MCP: show tool serializes lifecycle fields
# ---------------------------------------------------------------------------


class TestMcpShowLifecycleFields:
    def _make_thought_obj(
        self,
        superseded_by: UUID | None = None,
        valid_to: datetime | None = None,
    ) -> object:
        from munin.core.memory import Thought

        return Thought(
            id=_FAKE_UUID,
            content="content",
            project=_FAKE_PROJECT,
            scope=None,
            tags=[],
            metadata={},
            created_at=_NOW,
            updated_at=_NOW,
            superseded_by=superseded_by,
            valid_from=_NOW,
            valid_to=valid_to,
        )

    def test_live_row_has_null_superseded_and_valid_to(self) -> None:
        thought = self._make_thought_obj()
        with patch("munin.mcp.server.memory.show", return_value=thought):
            from munin.mcp.server import show

            result = show(thought_id=str(_FAKE_UUID))

        assert result["superseded_by"] is None
        assert result["valid_to"] is None
        assert result["valid_from"] == _NOW.isoformat()

    def test_retired_row_serializes_superseded_by_and_valid_to(self) -> None:
        thought = self._make_thought_obj(superseded_by=_FAKE_UUID2, valid_to=_NOW)
        with patch("munin.mcp.server.memory.show", return_value=thought):
            from munin.mcp.server import show

            result = show(thought_id=str(_FAKE_UUID))

        assert result["superseded_by"] == str(_FAKE_UUID2)
        assert result["valid_to"] == _NOW.isoformat()


# ---------------------------------------------------------------------------
# M4: CLI remember --heading; _extract_heading
# ---------------------------------------------------------------------------


class TestCliRememberHeading:
    def test_heading_option_threaded_to_core(self) -> None:
        from typer.testing import CliRunner

        from munin.cli.main import app

        runner = CliRunner()
        with patch("munin.cli.main._remember", return_value=_FAKE_UUID) as mock_rem:
            result = runner.invoke(app, ["remember", "my content", "--heading", "## Storage"])

        assert result.exit_code == 0, result.output
        mock_rem.assert_called_once()
        call_kwargs = mock_rem.call_args[1]
        assert call_kwargs.get("heading") == "## Storage"


class TestExtractHeading:
    def test_extracts_frontmatter_title(self, tmp_path: Path) -> None:
        from munin.cli.main import _extract_heading

        md = tmp_path / "note.md"
        md.write_text("---\ntitle: My Title\n---\nSome content.", encoding="utf-8")
        post = frontmatter.load(str(md))
        assert _extract_heading(post) == "My Title"

    def test_extracts_first_h1_when_no_title(self, tmp_path: Path) -> None:
        from munin.cli.main import _extract_heading

        md = tmp_path / "note.md"
        md.write_text("# First Heading\n\nSome content.", encoding="utf-8")
        post = frontmatter.load(str(md))
        assert _extract_heading(post) == "First Heading"

    def test_returns_none_when_no_heading(self, tmp_path: Path) -> None:
        from munin.cli.main import _extract_heading

        md = tmp_path / "note.md"
        md.write_text("Just some content without a heading.", encoding="utf-8")
        post = frontmatter.load(str(md))
        assert _extract_heading(post) is None

    def test_title_takes_precedence_over_h1(self, tmp_path: Path) -> None:
        from munin.cli.main import _extract_heading

        md = tmp_path / "note.md"
        md.write_text(
            "---\ntitle: FM Title\n---\n# H1 Heading\n\nContent.", encoding="utf-8"
        )
        post = frontmatter.load(str(md))
        assert _extract_heading(post) == "FM Title"
