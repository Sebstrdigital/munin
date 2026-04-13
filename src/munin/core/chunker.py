"""Heading-based markdown chunking."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    """A chunk of markdown content delimited by heading boundaries."""

    heading: str
    content: str


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


def chunk_markdown(content: str, filename: str) -> list[Chunk]:
    """Split markdown content into chunks by h1-h3 heading boundaries.

    Args:
        content: The markdown file content.
        filename: The filename, used for the preamble chunk heading.

    Returns:
        A list of Chunk objects, each representing a heading-delimited section.
    """
    matches = list(_HEADING_RE.finditer(content))

    if not matches:
        if not content.strip():
            return []
        return [Chunk(heading=filename, content=content)]

    chunks: list[Chunk] = []

    first_heading_start = matches[0].start()
    if first_heading_start > 0:
        preamble = content[:first_heading_start].strip()
        if preamble:
            chunks.append(Chunk(heading=filename, content=preamble))

    for i, match in enumerate(matches):
        heading_level, heading_text = match.groups()
        heading = heading_text.strip()

        start = match.end()
        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(content)

        section_content = content[start:end].strip()
        if section_content:
            chunks.append(Chunk(heading=heading, content=section_content))

    return chunks
