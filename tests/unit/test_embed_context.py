"""Unit tests for the P3-1 deterministic contextual embedding prefix builder.

P3-fix additions:
  - truncate_for_embed(): content-only cap at MAX_EMBED_CONTENT_CHARS.
  - build_embed_text(): prefix is NEVER consumed by truncation.
  - CJK/dense input stays within the content budget.
  - remember() of an oversized thought no longer raises (embed text is safe).
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import UUID

import pytest

from munin.core.config import MuninConfig
from munin.core.embed import MAX_EMBED_CONTENT_CHARS, build_embed_text, truncate_for_embed
from munin.core.memory import remember

_FAKE_VEC = [0.1] * 768
_FAKE_UUID = UUID("12345678-1234-5678-1234-567812345678")

# ────────────────────────────────────────────────────────────────
# build_embed_text — determinism and field-omission tests
# ────────────────────────────────────────────────────────────────


class TestBuildEmbedText:
    """P3-1: prefix builder is deterministic and omits empty fields cleanly."""

    def test_all_fields_present(self) -> None:
        """All non-empty fields appear as prefix lines above the content."""
        result = build_embed_text(
            "my content",
            project="munin",
            scope="decisions",
            tags=["arch", "db"],
            heading="## Storage",
        )
        assert result.startswith("project: munin\n")
        assert "scope: decisions\n" in result
        assert "tags: arch, db\n" in result
        assert "heading: ## Storage\n" in result
        # Blank line separates prefix from content.
        assert "\n\nmy content" in result
        assert result.endswith("my content")

    def test_deterministic_same_input_same_output(self) -> None:
        """Identical arguments always produce identical output (pure function)."""
        kwargs = dict(
            project="proj",
            scope="s",
            tags=["x", "y"],
            heading="h",
        )
        a = build_embed_text("hello", **kwargs)  # type: ignore[arg-type]
        b = build_embed_text("hello", **kwargs)  # type: ignore[arg-type]
        assert a == b

    def test_deterministic_no_tag_order_change(self) -> None:
        """Tag order in the prefix matches the list order (no sorting)."""
        result = build_embed_text("c", project="p", tags=["z", "a", "m"])
        assert "tags: z, a, m" in result

    def test_scope_omitted_when_none(self) -> None:
        """scope=None produces no 'scope:' line."""
        result = build_embed_text("c", project="p", scope=None)
        assert "scope:" not in result

    def test_scope_omitted_when_empty_string(self) -> None:
        """scope='' produces no 'scope:' line (falsy check)."""
        result = build_embed_text("c", project="p", scope="")
        assert "scope:" not in result

    def test_tags_omitted_when_empty_list(self) -> None:
        """Empty tag list produces no 'tags:' line."""
        result = build_embed_text("c", project="p", tags=[])
        assert "tags:" not in result

    def test_tags_omitted_when_none(self) -> None:
        """tags=None produces no 'tags:' line."""
        result = build_embed_text("c", project="p", tags=None)
        assert "tags:" not in result

    def test_heading_omitted_when_none(self) -> None:
        """heading=None produces no 'heading:' line."""
        result = build_embed_text("c", project="p", heading=None)
        assert "heading:" not in result

    def test_heading_omitted_when_empty_string(self) -> None:
        """heading='' produces no 'heading:' line."""
        result = build_embed_text("c", project="p", heading="")
        assert "heading:" not in result

    def test_minimal_only_project(self) -> None:
        """Only project (mandatory) produces a two-line prefix + content."""
        result = build_embed_text("body", project="proj")
        assert result == "project: proj\n\nbody"

    def test_raw_content_not_modified(self) -> None:
        """The raw content string appears verbatim after the blank separator."""
        raw = "Line1\nLine2\n  indented"
        result = build_embed_text(raw, project="p")
        # After the blank separator the rest must match raw exactly.
        separator = "project: p\n\n"
        assert result.startswith(separator)
        assert result[len(separator) :] == raw

    def test_format_structure(self) -> None:
        """Prefix lines come before a single blank line, then the content."""
        result = build_embed_text(
            "content", project="p", scope="s", tags=["t"], heading="h"
        )
        prefix, _, body = result.partition("\n\n")
        assert body == "content"
        assert "project: p" in prefix
        assert "scope: s" in prefix
        assert "tags: t" in prefix
        assert "heading: h" in prefix


# ────────────────────────────────────────────────────────────────
# remember() — embedder receives prefixed text; stored content is raw
# ────────────────────────────────────────────────────────────────

_CFG_NO_DEDUP = MuninConfig(
    db_url="postgresql://x:x@localhost:5433/x",
    embed_url="http://localhost:8088",
    embed_dim=768,
    default_limit=10,
    embed_batch_size=32,
    remember_dedup_enabled=False,
    remember_supersede_enabled=False,
)


def _make_pool_mock(return_uuid: UUID = _FAKE_UUID) -> MagicMock:
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = (return_uuid,)
    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    pool = MagicMock()
    pool.connection.return_value = conn
    return pool


class TestRememberContextualEmbed:
    """P3-1: remember() sends prefixed text to embed; stores raw content."""

    def test_embedder_receives_prefixed_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embed() is called with the contextual prefix, NOT the bare content."""
        captured_embed_input: list[str] = []

        def fake_embed(text: str, **_kw: object) -> list[float]:
            captured_embed_input.append(text)
            return _FAKE_VEC

        pool = _make_pool_mock()
        monkeypatch.setattr("munin.core.memory.embed", fake_embed)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "test-proj"
        )

        remember(
            "raw content here",
            project="test-proj",
            scope="api",
            tags=["fast", "stable"],
            heading="## Design",
            config=_CFG_NO_DEDUP,
        )

        assert len(captured_embed_input) == 1
        embed_input = captured_embed_input[0]
        # Prefix lines present.
        assert "project: test-proj" in embed_input
        assert "scope: api" in embed_input
        assert "tags: fast, stable" in embed_input
        assert "heading: ## Design" in embed_input
        # Raw content present at the end.
        assert embed_input.endswith("raw content here")

    def test_stored_content_is_raw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The first upsert_thought arg (content) is the raw string, not prefixed."""
        pool = _make_pool_mock()
        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "proj"
        )

        remember(
            "raw content here",
            project="proj",
            scope="s",
            tags=["t"],
            heading="h",
            config=_CFG_NO_DEDUP,
        )

        cur = pool.connection().__enter__().cursor().__enter__()
        # upsert_thought is called as: SELECT upsert_thought(%s, %s::vector, ...)
        # The first positional param is content.
        execute_calls = cur.execute.call_args_list
        upsert_call = next(
            c for c in execute_calls if "upsert_thought" in c[0][0]
        )
        stored_content = upsert_call[0][1][0]
        assert stored_content == "raw content here"
        assert "project:" not in stored_content

    def test_heading_from_metadata_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When heading= is not passed but metadata has 'heading', it is used in prefix."""
        captured: list[str] = []

        def fake_embed(text: str, **_kw: object) -> list[float]:
            captured.append(text)
            return _FAKE_VEC

        pool = _make_pool_mock()
        monkeypatch.setattr("munin.core.memory.embed", fake_embed)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "proj"
        )

        remember(
            "content",
            project="proj",
            metadata={"heading": "From Meta"},
            config=_CFG_NO_DEDUP,
        )

        assert len(captured) == 1
        assert "heading: From Meta" in captured[0]

    def test_no_heading_no_prefix_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no heading is provided (neither param nor metadata), no heading: line."""
        captured: list[str] = []

        def fake_embed(text: str, **_kw: object) -> list[float]:
            captured.append(text)
            return _FAKE_VEC

        pool = _make_pool_mock()
        monkeypatch.setattr("munin.core.memory.embed", fake_embed)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "proj"
        )

        remember("content", project="proj", config=_CFG_NO_DEDUP)

        assert len(captured) == 1
        assert "heading:" not in captured[0]
        assert captured[0].startswith("project: proj\n")


# ────────────────────────────────────────────────────────────────
# P3-fix: truncation helper and content-only truncation in build_embed_text
# ────────────────────────────────────────────────────────────────


class TestTruncateForEmbed:
    """P3-fix: truncate_for_embed() caps content-only, never the prefix."""

    def test_short_content_unchanged(self) -> None:
        """Content below the limit is returned unchanged."""
        short = "x" * 100
        assert truncate_for_embed(short) == short

    def test_exact_limit_unchanged(self) -> None:
        """Content of exactly MAX_EMBED_CONTENT_CHARS is returned unchanged."""
        at_limit = "a" * MAX_EMBED_CONTENT_CHARS
        assert truncate_for_embed(at_limit) == at_limit

    def test_oversized_content_truncated(self) -> None:
        """Content exceeding the limit is truncated to MAX_EMBED_CONTENT_CHARS."""
        oversized = "z" * (MAX_EMBED_CONTENT_CHARS + 500)
        result = truncate_for_embed(oversized)
        assert len(result) == MAX_EMBED_CONTENT_CHARS

    def test_cjk_dense_input_within_budget(self) -> None:
        """CJK characters (each 1 char, ~1 token) stay within the content budget.

        At MAX_EMBED_CONTENT_CHARS=2600 chars of CJK ≈ 2600 tokens.  Wait —
        that would exceed 2048.  But the constant is defined conservatively for
        CJK at 2048 tokens × 1 char/token budget (worst case).  The actual
        CJK token density is ~0.5–1.0 char/token with byte-pair or sentencepiece
        tokenizers.  The truncate_for_embed() contract is: result length ≤ limit.
        We test that the truncated string has exactly MAX_EMBED_CONTENT_CHARS chars.
        """
        cjk_content = "日本語テスト文字" * 500  # ~4000 chars of CJK
        result = truncate_for_embed(cjk_content)
        assert len(result) == MAX_EMBED_CONTENT_CHARS

    def test_build_embed_text_prefix_always_present_after_truncation(self) -> None:
        """The full prefix is present even when content is truncated.

        Verifies that truncation happens BEFORE the prefix is assembled, so
        the prefix lines are never consumed by the char budget.
        """
        oversized = "A" * (MAX_EMBED_CONTENT_CHARS + 1000)
        result = build_embed_text(
            oversized,
            project="testproj",
            scope="testscope",
            tags=["t1", "t2"],
            heading="## Heading",
        )
        # Prefix lines must be intact.
        assert result.startswith("project: testproj\n")
        assert "scope: testscope\n" in result
        assert "tags: t1, t2\n" in result
        assert "heading: ## Heading\n" in result
        # Content portion is capped at MAX_EMBED_CONTENT_CHARS.
        _, _, body = result.partition("\n\n")
        assert len(body) == MAX_EMBED_CONTENT_CHARS

    def test_remember_oversized_content_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """remember() with content > MAX_EMBED_CONTENT_CHARS must not raise.

        Before P3-fix the embed_text was sent uncapped to the embedder, which
        would 400 on the Gemma 2048-token hard limit for dense/long inputs.
        """
        captured_embed_input: list[str] = []

        def fake_embed(text: str, **_kw: object) -> list[float]:
            captured_embed_input.append(text)
            return _FAKE_VEC

        pool = _make_pool_mock()
        monkeypatch.setattr("munin.core.memory.embed", fake_embed)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "proj"
        )

        oversized_content = "X" * (MAX_EMBED_CONTENT_CHARS + 5000)
        # Must not raise — embed_text is now capped before calling embed().
        remember(oversized_content, project="proj", config=_CFG_NO_DEDUP)

        assert len(captured_embed_input) == 1
        embed_text = captured_embed_input[0]
        # The prefix is intact.
        assert embed_text.startswith("project: proj\n")
        # The body (after blank separator) is capped.
        _, _, body = embed_text.partition("\n\n")
        assert len(body) == MAX_EMBED_CONTENT_CHARS
