# munin — Decisions

Local, multi-tool AI memory system for per-project context. Named after Odin's raven of memory.

## Why not OB1

- OB1 is hosted (Supabase + Edge Functions). We want local.
- OB1 targets life data (household, CRM, meals). We want dev project context.
- OB1 setup flow, RLS, credential tracker = overkill for single-user local.
- Ideas stolen from OB1: `thoughts` table shape, `match_thoughts()` RPC, content fingerprint dedup, auto-capture skill pattern, MCP-as-interface.

## Requirements

- Local-only. No cloud dependency.
- Per-project scoping, but cross-project recall when asked.
- Usable from Claude Code, Cursor, ChatGPT desktop, Codex, plain shell.
- Single-user. No auth beyond localhost.
- Offline-capable embeddings.

## Stack

| Layer | Choice | Why |
|---|---|---|
| DB | Postgres 16 + pgvector | Proven, same SQL works as OB1, HNSW index |
| Embeddings | llama.cpp server (GGUF) | OpenAI-compat API, language-agnostic, CPU fine |
| Model | `nomic-embed-text-v1.5` Q4_K_M (768d) | ~90MB, solid quality, permissive license |
| Core | Python module | Shared by CLI + MCP |
| CLI | `typer` + `rich` | Type-hinted, human+JSON output |
| MCP | FastMCP (Python) | Matches mcp-servers/ convention |
| Orchestration | Docker Compose | One `up` to start everything |

## Scoping model

- `metadata.project` (required, string) — primary scope
- `metadata.type` (enum) — `decision | fact | gotcha | todo | reference | snippet | summary`
- `metadata.source` — `cli | mcp | hook | import`
- `metadata.tags` (array) — free-form
- `metadata.ref` (optional) — URL or `file:line`
- Auto-detect project from `git rev-parse --show-toplevel` basename
- `--all-projects` / `--project X` overrides
- `content_fingerprint` (SHA256) + unique index → dedup on insert

## CLI surface

```
munin remember "text" [--project X] [--type decision] [--tag foo]
munin recall "query" [--project X | --all-projects] [-k 10] [--type decision]
munin projects
munin show <id>
munin forget <id>
munin import <file>
munin stats [--project X]
```

- Default output: `rich` table
- `--json` flag for piping / hook use

## MCP tools

Same verbs as CLI, same core module. FastMCP exposes:

- `remember(content, project?, type?, tags?, ref?)`
- `recall(query, project?, all_projects?, k?, type?)`
- `list_projects()`
- `show(id)`
- `forget(id)`

## Deferred

- Skills (auto-capture, panning-for-gold, fingerprint dedup worth copying later)
- Remote mode (HTTP wrapper around core)
- Multi-user / auth
- Frontend dashboard
- Import recipes

## Build order

1. Docker Compose: postgres+pgvector + llama.cpp server
2. SQL schema: `thoughts` table, `match_thoughts()` RPC, fingerprint unique index
3. Smoke test: `psql` insert + `curl` embed + recall via SQL
4. Python `core/` module
5. CLI (`typer`)
6. MCP server (FastMCP)
7. Wire into Claude Code `.mcp.json`, Cursor, Codex
8. Skills (later)
