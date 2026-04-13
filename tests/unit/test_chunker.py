"""Unit tests for core.chunker."""

from munin.core.chunker import Chunk, chunk_markdown


def test_h1_h3_headings_become_separate_chunks() -> None:
    """Each h1-h3 heading-delimited section becomes a separate Chunk."""
    content = """# Heading 1

Content under heading 1.

## Heading 2

Content under heading 2.

### Heading 3

Content under heading 3.
"""
    chunks = chunk_markdown(content, "test.md")

    assert len(chunks) == 3
    assert chunks[0].heading == "Heading 1"
    assert "Content under heading 1" in chunks[0].content
    assert chunks[1].heading == "Heading 2"
    assert "Content under heading 2" in chunks[1].content
    assert chunks[2].heading == "Heading 3"
    assert "Content under heading 3" in chunks[2].content


def test_no_headings_returns_single_chunk() -> None:
    """When a markdown file has no headings, the entire file is a single chunk."""
    content = """This is some plain text content.

It has no headings at all.

Just paragraphs.
"""
    chunks = chunk_markdown(content, "plain.md")

    assert len(chunks) == 1
    assert chunks[0].heading == "plain.md"
    assert "plain text content" in chunks[0].content


def test_empty_section_is_skipped() -> None:
    """An empty section (heading with no content below) is skipped."""
    content = """# Heading 1

Content here.

## Empty Heading


### Heading 3

More content.
"""
    chunks = chunk_markdown(content, "test.md")

    assert len(chunks) == 2
    assert chunks[0].heading == "Heading 1"
    assert chunks[1].heading == "Heading 3"


def test_content_before_first_heading_is_preamble() -> None:
    """Content before the first heading is returned as a preamble chunk."""
    content = """This is the preamble.

It appears before any heading.

# First Heading

Content after first heading.
"""
    chunks = chunk_markdown(content, "test.md")

    assert len(chunks) == 2
    assert chunks[0].heading == "test.md"
    assert "preamble" in chunks[0].content
    assert chunks[1].heading == "First Heading"


def test_empty_content_returns_empty_list() -> None:
    """Empty content returns an empty list."""
    chunks = chunk_markdown("", "empty.md")

    assert chunks == []


def test_whitespace_only_returns_empty_list() -> None:
    """Whitespace-only content returns an empty list."""
    chunks = chunk_markdown("   \n\n   ", "whitespace.md")

    assert chunks == []


def test_only_headings_no_content() -> None:
    """When file has only headings with no content, return empty list."""
    content = """# Heading 1

## Heading 2
"""
    chunks = chunk_markdown(content, "test.md")

    assert chunks == []
