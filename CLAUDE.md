# CLAUDE.md — munin

Guidance for Claude Code when working in this repo.

## Output Style

**Caveman mode default** — use `/caveman full` for all conversational replies and internal planning artifacts (epic, feature, sprint, debug, TODO). Drop articles, filler, pleasantries, hedging. Keep code, SQL, error messages, and file:line citations intact.

Full prose only for: code comments, commit messages, README, and anything explicitly meant for external readers.

## What munin Is

Local, language-agnostic memory store for coding agents (Claude Code, Cursor, Codex). Postgres + pgvector for storage, llama.cpp server for embeddings. Exposed via CLI (`munin`) and MCP server. Successor to OB1 prototype — same design, cleaner implementation.

## Stack

- **Storage:** postgres 16 + pgvector 0.8.2 (docker, port 5433)
- **Embeddings:** llama.cpp server running `nomic-embed-text-v1.5.Q4_K_M.gguf` (docker, port 8088, 768-dim, mean pooling)
- **Language:** Python 3.11+ (psycopg, typer, FastMCP)
- **Config:** `~/.config/munin/config.toml`
- **Logs:** `~/.local/state/munin/munin.log`

## Architecture

```
cli/ ─┐
      ├─> core/ ─> postgres (pgvector)
mcp/ ─┘          └> llama.cpp embed
```

- `core/` is the single source of truth. CLI and MCP are thin wrappers.
- DB stays dumb — embeddings computed client-side, passed as vectors.
- Single `thoughts` table with metadata filter (project, scope, tags).

## Key Docs

- `docs/decisions.md` — design decisions + rationale
- `docs/plan.md` — milestones M0–M5
- `docs/epic.md` — current epic (takt flow)
- `docs/features/` — feature specs
- `docs/sprints/` — sprint definitions

## Running the Stack

```bash
docker compose up -d
docker compose ps          # both healthy
docker compose logs -f llama-embed
```

## Conventions

- SQL migrations in `sql/NNN_name.sql`, applied in order.
- Python: ruff + mypy strict where practical.
- Tests: pytest, docker-compose.test.yml for integration.
- Commits: Conventional Commits, subject ≤50 chars.

## takt
final_gate: false
local_validation: false

## jCodeMunch
indexed_commit: 6e94e74265ab49388aa0f7e4b2ea6894762fd8a9
indexed_at: 2026-04-13T13:58:07Z
