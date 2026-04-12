# munin

Local memory store for coding agents.

## What it does

Munin is a local, language-agnostic memory system that lets you store and retrieve project context using semantic search. Named after Odin's raven of memory, it provides a Postgres + pgvector backend with an OpenAI-compatible embedding server, exposed via a CLI and MCP server. Automatically scope memories by Git project and recall context across projects when needed.

## Quickstart

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/munin.git && cd munin
```

### 2. Download the embedding model

```bash
mkdir -p models
curl -L -o models/nomic-embed-text-v1.5.Q4_K_M.gguf \
  https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.Q4_K_M.gguf
```

### 3. Start the Docker services

```bash
docker compose up -d
```

Wait for both services to report as healthy:

```bash
docker compose ps
```

### 4. Apply database migrations

```bash
for f in sql/*.sql; do cat "$f" | docker exec -i munin-postgres psql -U munin -d munin; done
```

### 5. Install the CLI

```bash
pipx install -e .
```

### 6. Verify the installation

```bash
munin doctor
```

### 7. Try the core commands

Store a memory:

```bash
munin remember "First thought from the quickstart"
```

Retrieve it:

```bash
munin recall "quickstart"
```

### 8. (Optional) Add to Claude Code

To use munin with Claude Code, add this to your `~/.claude/.mcp.json`:

```json
{
  "mcpServers": {
    "munin": {
      "command": "munin-mcp"
    }
  }
}
```

## Commands

| Command | Purpose |
|---------|---------|
| `munin remember <text>` | Store a memory with optional tags and scope |
| `munin recall <query>` | Semantic search over stored memories |
| `munin show [id]` | Display a specific memory or list all |
| `munin forget [id]` | Delete a memory |
| `munin projects` | List all projects in the memory store |
| `munin stats` | Show memory usage statistics |
| `munin import <file>` | Bulk import memories from JSONL |
| `munin doctor` | Self-diagnosis and health check |
| `munin completion` | Generate shell completions (bash, zsh, fish) |

## Architecture

```
cli/ ─┐
      ├─> core/ ─> postgres (pgvector)
mcp/ ─┘          └> llama.cpp embed
```

The `core/` module is the single source of truth. The CLI and MCP server are thin wrappers around core functions. The database remains "dumb" — embeddings are computed client-side and passed as vectors. All memories are stored in a single `thoughts` table with metadata filters for project, scope, and tags.

## Further reading

- [Design Decisions](docs/decisions.md) — rationale and architecture choices
- [Release Plan](docs/plan.md) — milestones M0–M5
- [Epic Documentation](tasks/epic-munin-v1.md) — user stories and acceptance criteria

## Status

Munin is in active development. See the [release plan](docs/plan.md) for upcoming features and milestones.
