# munin

Local memory store for coding agents.

## What it does

Munin is a local, language-agnostic memory system that lets you store and retrieve project context using semantic search. Named after Odin's raven of memory, it provides a Postgres + pgvector backend with an OpenAI-compatible embedding server, exposed via a CLI and MCP server. Automatically scope memories by Git project and recall context across projects when needed.

## Quickstart

### 1. Enter the project directory

```bash
cd munin
```

### 2. Download the models

munin embeds with EmbeddingGemma-300M and re-ranks recall with a bge-reranker-v2-m3
cross-encoder. Both are served locally by llama.cpp and must be present in `./models/`:

```bash
mkdir -p models
hf download ggml-org/embeddinggemma-300M-GGUF embeddinggemma-300M-Q8_0.gguf --local-dir ./models
hf download gpustack/bge-reranker-v2-m3-GGUF bge-reranker-v2-m3-Q8_0.gguf --local-dir ./models
```

### 3. Start the services

Using Podman (recommended, rootless):

```bash
podman compose up -d
```

Or Docker:

```bash
docker compose up -d
```

Wait for both services to report as healthy:

```bash
podman compose ps   # or: docker compose ps
```

### 4. Apply database migrations

```bash
for f in sql/*.sql; do cat "$f" | podman exec -i munin-postgres psql -U munin -d munin; done
```

If using Docker, replace `podman exec` with `docker exec`.

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
