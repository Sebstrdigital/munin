# Epic: munin v1 — Local memory store for coding agents

## 1. Problem Statement

Coding agents (Claude Code, Cursor, Codex) have no shared persistent memory across sessions or tools. Every session starts cold: context about decisions, failed approaches, project conventions, and user preferences must be re-discovered or re-explained. The OB1 prototype proved the pattern (Postgres + pgvector + MCP) but accumulated cruft. munin is the clean v1: one store, multiple agent surfaces, local-first.

## 2. Target Users

**Primary:** Sebastian (project owner) driving work across multiple repos from Claude Code, Cursor, and Codex — needs a single memory that survives session restarts and tool switches.

**Secondary:** Future team members using the same agent tooling on shared or adjacent repos.

**Not targeted (v1):** Cloud/multi-machine sync, non-coding agents, end users of the products being built.

## 3. Goals

- Local-first memory store: runs entirely on the user's machine, no cloud dependencies.
- CLI + MCP parity: every capability available from both surfaces via shared core.
- Project-scoped recall: auto-detect project from git root, filter recall by scope.
- Deduplication: identical content never stored twice (content fingerprint).
- Fast recall: p50 < 100ms for vector search on realistic corpus (10k thoughts).
- Language-agnostic embeddings: llama.cpp server so any client language can ingest.
- Zero manual DB admin: `docker compose up` → working store.

## 4. Constraints

**Technical:**
- Docker Compose for infra (postgres + llama.cpp server)
- Python 3.11+ for core/CLI/MCP
- pgvector 0.8.2, nomic-embed-text-v1.5 (768-dim, mean pooling)
- Ports: 5433 (pg), 8088 (embed) — avoid conflicts with other local projects
- Single-machine v1, no sync, no replication

**Scope:**
- No web UI
- No cross-machine sync (dump/restore if needed)
- No embedding provider swap in v1 (nomic only; configurable dimension deferred)

**Dependencies:**
- OB1 patterns (proven) guide schema and RPC design
- llama.cpp server API stability for `/v1/embeddings`

## 5. Feature Breakdown

### F-1: SQL Schema & RPC Primitives
**Scope:** End-to-end SQL layer — `thoughts` table, indexes, content fingerprint, `match_thoughts()` RPC for filtered vector search, `upsert_thought()` for dedup-aware insert. Manually testable via psql: insert rows across projects, recall by query + project filter.
**Depends on:** (none — M0 infra already running)

### F-2: Core Python Module
**Scope:** `core/db.py` (psycopg pool), `core/embed.py` (HTTP client for llama.cpp), `core/memory.py` (`remember`, `recall`, `list_projects`, `show`, `forget`), `core/scope.py` (git root detection), `core/config.py` (TOML loader). Unit + integration tests against compose stack. This is the single source of truth the CLI and MCP wrap.
**Depends on:** F-1

### F-3: CLI (`munin` command)
**Scope:** Typer app exposing remember/recall/projects/show/forget/stats, `--json` flag on all commands, pyproject entry point, `pipx install -e .` dev flow. Usable from any shell.
**Depends on:** F-2

### F-4: MCP Server
**Scope:** FastMCP app wrapping core, tools mirroring CLI verbs, stdio transport, config entries for Claude Code (`.mcp.json`) and Cursor. Verify recall from each client.
**Depends on:** F-2 (parallel with F-3 after F-2 lands)

### F-5: Quality of Life
**Scope:** `munin import` for bulk ingestion (jsonl, markdown folder), shell completion, logging to `~/.local/state/munin/munin.log`, `munin doctor` (checks db + embed service + config).
**Depends on:** F-3, F-4

## 6. Sequencing Rationale

F-1 establishes the data contract every other feature depends on — schema and RPCs must be stable before Python wraps them. F-2 builds the shared core that both agent surfaces consume, avoiding duplicate logic between CLI and MCP. F-3 and F-4 could technically run in parallel once F-2 lands, but CLI-first is safer: it's easier to debug interactively and shakes out core bugs before MCP wraps them. F-5 is deferred polish — import, doctor, logging — that only makes sense once the core flow works end-to-end.

## 7. Out of Scope

- Cross-machine sync / replication
- Cloud-hosted variant
- Web UI or dashboard
- Alternative embedding providers (OpenAI, Voyage, etc.) — nomic only in v1
- Embedding dimension auto-migration
- Multi-user auth / permissions
- Non-text memory (images, audio, structured data beyond metadata)
- HTTP MCP transport (stdio only v1)

## 8. Open Questions

- Dimension: 768 (nomic) locked for v1. Revisit when supporting provider swap.
- Thought retention policy: TTL / manual cleanup / never? → decide in F-5 doctor scope.
- `forget` semantics: hard delete vs tombstone? → decide in F-2 design.
- Should `scope` be a single string or hierarchy (e.g., `project/area`)? → decide in F-1 schema.
- Embedding batch size / rate limiting against llama.cpp server → measure in F-2.
