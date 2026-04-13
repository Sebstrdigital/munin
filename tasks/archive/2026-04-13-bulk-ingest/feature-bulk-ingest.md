# Feature: Bulk Knowledge Ingestion

## Introduction

Project knowledge lives scattered across ~12 git repos (CLAUDE.md files, docs/ folders, READMEs) and client document locations. Munin can store and recall memories, but there's no way to bulk-ingest existing knowledge with proper project/scope hierarchy and source provenance. This feature adds a manifest-driven ingestion pipeline that crawls configured sources, chunks markdown by heading, and stores everything with correct project, scope, tags, and source metadata — so agents can immediately recall relevant context across all projects.

## Goals

- Agents can semantically search across all project knowledge from day one after a single `munin ingest` run
- Every ingested thought traces back to its source file and heading
- Re-running ingestion is safe and idempotent (fingerprint dedup)
- Users configure sources once in a manifest, then ingest with one command

## User Stories

### US-001: Configure ingestion sources via manifest

**Description:** As a user, I want to define a `sources.toml` manifest that maps filesystem paths and glob patterns to project/scope/tags, so that munin knows where to find my knowledge and how to categorize it.

**Acceptance Criteria:**
- [ ] When `~/.config/munin/sources.toml` contains a `[[source]]` entry with path, globs, project, and optional scope/tags, `munin ingest` reads and validates it
- [ ] When a source path does not exist or contains no matching files, a warning is logged and ingestion continues with remaining sources
- [ ] When no manifest exists, `munin ingest` exits with a helpful error pointing to the expected path and format

### US-002: Chunk markdown files by heading

**Description:** As a user, I want markdown files split into sections by heading boundaries, so that each chunk is a focused, searchable unit of knowledge rather than a wall of text.

**Acceptance Criteria:**
- [ ] When a markdown file contains `##` headings, each heading-delimited section becomes a separate thought with the heading text stored in metadata
- [ ] When a markdown file has no headings, the entire file content is stored as a single thought
- [ ] When a section is empty (heading with no content below it), it is skipped and not stored

### US-003: Ingest with source provenance and hierarchy

**Description:** As a user, I want each ingested thought tagged with its source file path, heading, and hierarchical project/scope, so I can trace any recalled knowledge back to its origin.

**Acceptance Criteria:**
- [ ] After ingestion, each thought's metadata contains `source_path` (relative to source root) and `heading` (the section title)
- [ ] The `project` field supports hierarchical values like `dua-cs-agent/backend` as configured in the manifest
- [ ] When the same file is re-ingested after content changes, the updated content replaces the old thought via fingerprint dedup

### US-004: Preview and execute bulk ingestion

**Description:** As a user, I want to preview what `munin ingest` will do before committing, and then run it for real with a summary of results.

**Acceptance Criteria:**
- [ ] When `munin ingest --dry-run` is executed, it lists every chunk that would be stored (source file, heading, project, scope, tags) without writing to the database
- [ ] When `munin ingest` is executed without `--dry-run`, it processes all sources and prints a summary: total files scanned, chunks stored, chunks skipped (dedup), failures
- [ ] When `--json` flag is used, the summary output is machine-readable JSON

## Functional Requirements

- FR-1: The manifest file is `~/.config/munin/sources.toml` using `[[source]]` array-of-tables syntax
- FR-2: Each `[[source]]` entry requires `path` (string) and `project` (string); `globs`, `scope`, and `tags` are optional (globs defaults to `["**/*.md"]`)
- FR-3: Glob patterns are resolved relative to the source `path` and support recursive matching
- FR-4: Heading-based chunking splits on lines matching `^#{1,3} ` (h1–h3); content before the first heading is treated as a preamble chunk
- FR-5: Each chunk is stored via `core.memory.remember()` with `project`, `scope`, `tags` from the manifest, plus `metadata.source_path` and `metadata.heading`
- FR-6: The `munin ingest` command reuses the existing `--json` flag convention from other CLI commands
- FR-7: Dry-run output is written to stdout; warnings and errors go to stderr

## Non-Goals

- No periodic/scheduled re-ingestion — one-shot, re-run manually if needed
- No non-markdown formats (code files, TOML, JSON) for v1
- No cloud/remote sources — local filesystem only
- No cross-machine sync
- No MCP tool wrapper for ingest — CLI only for now
- No custom chunking strategies — heading-split only
- No deletion of thoughts whose source file was removed

## Technical Considerations

- Chunking logic should live in `core/` (not CLI) so MCP can reuse it later
- Heading-split regex: `^#{1,3} (.+)$` — captures heading text for metadata
- `sources.toml` parsing via `tomllib` (stdlib Python 3.11+)
- Existing `remember()` handles embedding + dedup via `upsert_thought()` — no DB changes needed
- Large ingestion runs will hit the llama.cpp embed server sequentially; batching is a future optimization

## Success Metrics

- All configured sources ingested in a single `munin ingest` run with zero manual steps
- `munin recall "auth middleware"` returns relevant chunks from the correct project
- Re-running `munin ingest` produces 0 new inserts when nothing changed (dedup works)

## Open Questions

- Should preamble content (text before the first heading) include the filename as its heading metadata, or use a sentinel like `_preamble`?
- For very large files (e.g., a 500-line CLAUDE.md), should there be a max chunk size that triggers sub-splitting?
