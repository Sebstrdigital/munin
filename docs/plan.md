# munin — Plan

## Milestone 0 — Infra smoke test

**Goal:** postgres+pgvector and llama.cpp server both run locally and talk.

- [ ] `docker-compose.yml` with two services:
  - `postgres` (pgvector/pgvector:pg16 image)
  - `llama-embed` (ghcr.io/ggerganov/llama.cpp:server, mounts `./models`)
- [ ] Download `nomic-embed-text-v1.5.Q4_K_M.gguf` to `./models/`
- [ ] `docker compose up` → both healthy
- [ ] `curl localhost:8080/v1/embeddings` returns 768-dim vector
- [ ] `psql` connects, `create extension vector` works

## Milestone 1 — Schema

**Goal:** SQL-only end-to-end remember/recall.

- [ ] `sql/001_schema.sql` — thoughts table, indexes, trigger
- [ ] `sql/002_fingerprint.sql` — content_fingerprint column + unique partial index
- [ ] `sql/003_rpc.sql` — `match_thoughts()` function with metadata filter
- [ ] `sql/004_upsert.sql` — `upsert_thought()` handling dedup
- [ ] Manual test: insert 3 rows with different projects, recall by query + project filter

## Milestone 2 — Core module

**Goal:** Python functions that CLI and MCP both use.

- [ ] `core/db.py` — psycopg connection pool
- [ ] `core/embed.py` — HTTP call to llama.cpp server
- [ ] `core/memory.py` — `remember()`, `recall()`, `list_projects()`, `show()`, `forget()`
- [ ] `core/scope.py` — auto-detect project from git root
- [ ] `core/config.py` — load `~/.config/munin/config.toml`
- [ ] Unit tests with testcontainers or docker-compose.test.yml

## Milestone 3 — CLI

**Goal:** `munin` command works from any shell.

- [ ] `cli/main.py` — typer app
- [ ] Commands: remember, recall, projects, show, forget, import, stats
- [ ] `--json` flag everywhere
- [ ] `pyproject.toml` entry point: `munin = "munin.cli.main:app"`
- [ ] Install via `pipx install -e .` during dev

## Milestone 4 — MCP server

**Goal:** Claude Code, Cursor, Codex can call munin tools.

- [ ] `mcp/server.py` — FastMCP app wrapping core
- [ ] Tools mirror CLI verbs
- [ ] Stdio transport for local clients
- [ ] Add to Claude Code `.mcp.json`
- [ ] Add to Cursor settings
- [ ] Test recall from each client

## Milestone 5 — Quality of life

- [ ] `munin import` for bulk ingestion (jsonl, markdown folder)
- [ ] Shell completion (typer supports it)
- [ ] Logging to `~/.local/state/munin/munin.log`
- [ ] `munin doctor` — checks db + embed service + config

## Open questions

- Dimension: 768 (nomic) vs 384 (bge-small). Default 768, make configurable.
- Single `thoughts` table vs one per project. Single + metadata filter (OB1 pattern, proven).
- Embedding on client or server side? → client (core/embed.py), DB stays dumb.
- Should CLI also expose MCP tools over HTTP? → no, defer.
- Sync across machines? → out of scope v1. Postgres dump/restore if needed.
