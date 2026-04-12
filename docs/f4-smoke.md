# F4: MCP Server Config & Smoke Test

End-to-end walkthrough for connecting munin's MCP server to Claude Code or Cursor, and manually verifying the integration.

## Prerequisites

1. Docker Compose stack running and healthy:
   ```bash
   docker compose up -d
   docker compose ps   # both services show "healthy"
   ```

2. munin installed (editable install is fine):
   ```bash
   pipx install -e .
   # or
   pip install -e .
   ```

3. Verify the entry point is on your PATH:
   ```bash
   which munin-mcp
   munin-mcp --help   # should print FastMCP usage and exit
   ```

---

## Claude Code — `.mcp.json`

Create (or edit) `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "munin": {
      "command": "munin-mcp",
      "args": []
    }
  }
}
```

Restart Claude Code. The `munin` server should appear in `/mcp` and all six tools should be listed:
`remember`, `recall`, `forget`, `show`, `list_projects`, `stats`.

---

## Cursor — MCP Settings

Open **Cursor Settings → MCP** (or edit `~/.cursor/mcp.json`) and add:

```json
{
  "mcpServers": {
    "munin": {
      "command": "munin-mcp",
      "args": []
    }
  }
}
```

Reload the window. Cursor will start `munin-mcp` as a stdio subprocess on demand.

---

## Manual Test Walkthrough

### Step 1 — Store a thought

In a Claude Code or Cursor chat, call:

```
remember("The auth middleware reads the JWT secret from MUNIN_JWT_SECRET env var")
```

Expected response (fields may vary):
```json
{
  "id": "3f7a1c2d-...",
  "project": "my-project"
}
```

### Step 2 — Recall the thought

```
recall("JWT secret configuration")
```

Expected response contains a `results` array. Each result includes:
```json
{
  "id": "3f7a1c2d-...",
  "content": "The auth middleware reads the JWT secret from MUNIN_JWT_SECRET env var",
  "project": "my-project",
  "scope": null,
  "tags": [],
  "similarity": 0.87,
  "created_at": "2026-04-12T10:00:00.000Z"
}
```

### Step 3 — Verify in psql

```bash
docker exec -it munin-postgres psql -U munin -d munin \
  -c "SELECT id, content, project FROM thoughts ORDER BY created_at DESC LIMIT 5;"
```

The thought stored in step 1 should appear.

### Step 4 — List projects

```
list_projects()
```

Expected: a `projects` array with at least one entry showing your project name and a count ≥ 1.

### Step 5 — Check stats

```
stats()
```

Expected:
```json
{
  "total_thoughts": 1,
  "total_projects": 1,
  "embed_server_reachable": true,
  "db_reachable": true
}
```

---

## Known Quirks / Troubleshooting

### `munin-mcp` not found

The entry point is only available after `pip install -e .` or `pipx install`. Running from the source tree without installing will fail with `command not found`.

### DB unreachable error

```json
{"error": {"code": "db_unreachable", "message": "...", "hint": "run `docker compose up -d`"}}
```

Start the compose stack. The server does not crash on DB errors — it returns structured error responses so the calling agent can surface a readable message.

### Embed server unreachable

```json
{"error": {"code": "embed_unreachable", "message": "...", "hint": "check llama.cpp container"}}
```

Check the `llama-embed` container: `docker compose logs llama-embed`. The model file must be present at `./models/nomic-embed-text-v1.5.Q4_K_M.gguf`.

### Project shows as `"unknown"`

munin infers the project name from the working directory (git remote or folder name). If `munin-mcp` is started outside a recognised project directory the project falls back to `"unknown"`. Set the working directory in the MCP config if needed:

```json
{
  "mcpServers": {
    "munin": {
      "command": "munin-mcp",
      "args": [],
      "cwd": "/path/to/your/project"
    }
  }
}
```
