# munin

Local, multi-tool AI memory system for per-project context. Named after Odin's raven of memory.

> Status: planning. See [docs/decisions.md](docs/decisions.md) and [docs/plan.md](docs/plan.md).

## What

One local Postgres + pgvector store, accessible from any AI tool (Claude Code, Cursor, ChatGPT desktop, Codex, shell) via MCP or CLI. Per-project scoping with cross-project recall when asked.

## Stack

- Postgres 16 + pgvector (Docker)
- llama.cpp server + `nomic-embed-text-v1.5` GGUF (Docker, OpenAI-compatible API)
- Python core module shared by CLI (`typer`) and MCP server (FastMCP)
- One `docker compose up` to start the backend

## Not yet built

Everything. See `docs/plan.md` for milestones.
