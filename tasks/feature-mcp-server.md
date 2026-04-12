# Feature: F-4 — MCP Server

## 1. Introduction / Overview

Expose munin's memory operations as a Model Context Protocol (MCP) server so Claude Code, Cursor, Codex, and any other MCP-capable agent get first-class typed memory tools. The server is a thin FastMCP wrapper over the `core/` module — same logic as the CLI, different surface. Running as a persistent process eliminates the per-call Python startup cost that shell-out would impose, and typed tool schemas give agents accurate parameter hints without reading `--help`.

## 2. Goals

- Every core memory verb available as an MCP tool with a schema agents can read.
- One MCP server per project (client starts it with the project as cwd); auto-scoping "just works."
- Zero divergence from CLI semantics — both surfaces call the same `core.memory.*` functions.
- Installable entry point `munin-mcp` so users can add it to `.mcp.json` / Cursor settings with one line.
- End-to-end recall verified from at least Claude Code and Cursor against a real running stack.

## 3. User Stories

### US-001: Install and register `munin-mcp` in Claude Code
**Description:** As a Claude Code user, I want to add munin to `.mcp.json` and have its tools appear in my tool list so that I can remember and recall thoughts from inside a session.

**Acceptance Criteria:**
- [ ] After `pipx install -e .` provides `munin-mcp` on PATH, adding a `munin` entry to `.mcp.json` with command `munin-mcp` causes Claude Code to list `remember`, `recall`, `list_projects`, `show`, `forget`, `stats` as available tools.
- [ ] Each tool's schema in Claude Code's tool listing shows typed parameters (e.g., `recall.query: string`, `recall.limit: integer`) — not a free-form string blob.
- [ ] The server starts, handshakes, and stays alive across multiple tool calls without being re-spawned per call.

### US-002: Remember a thought via MCP tool
**Description:** As an agent running inside a repo, I want to call the `remember` tool with content and optional metadata so that I can persist decisions and context across sessions.

**Acceptance Criteria:**
- [ ] Calling `remember(content="decided to use HNSW")` from an MCP client stores a row whose `project` is auto-set to the directory name the MCP server was started in.
- [ ] Calling `remember(content="...", scope="design", tags=["auth","security"], metadata={"source":"planning"})` stores those fields on the row.
- [ ] The tool response includes the new thought `id` as a structured field the agent can reference in follow-up tool calls.

### US-003: Recall thoughts via MCP tool
**Description:** As an agent, I want to call `recall` with a natural-language query and receive a structured list of matches so that I can ground my next action in prior context without parsing terminal output.

**Acceptance Criteria:**
- [ ] Calling `recall(query="auth decisions")` returns a structured list of up to 10 thoughts from the current project, each with `id`, `content`, `project`, `scope`, `tags`, `similarity`, and `created_at`.
- [ ] Passing `scope="design"` filters results to that scope; passing `limit=3` caps result length; passing `threshold=0.5` omits low-similarity matches.
- [ ] When the query matches nothing, the tool returns an empty list with a `message` field explaining "no results" — not an error.

### US-004: Introspection tools — `list_projects`, `show`, `stats`
**Description:** As an agent, I want to inspect what's in memory and fetch full thoughts by id so that I can audit stored context before acting on it.

**Acceptance Criteria:**
- [ ] `list_projects()` returns a list of `{project, count}` objects for every distinct project in the store.
- [ ] `show(thought_id)` returns the full thought including untruncated content, tags, metadata, `created_at`, and `updated_at`; invalid ids return a structured `not_found` error.
- [ ] `stats()` returns an object with `total_thoughts`, `total_projects`, `embed_server_reachable` (bool), and `db_reachable` (bool).

### US-005: Delete a thought via MCP tool
**Description:** As an agent, I want to forget a specific thought by id so that I can actively prune stale or wrong memories when the user tells me to.

**Acceptance Criteria:**
- [ ] `forget(thought_id)` on a valid id hard-deletes the row and returns `{deleted: true, id: <id>}`.
- [ ] Calling `forget` on a missing id returns a structured `not_found` error (not a traceback).
- [ ] After a successful delete, a follow-up `show(thought_id)` call returns `not_found`.

### US-006: Verified end-to-end from multiple clients
**Description:** As a maintainer, I want recorded proof that `munin-mcp` works from Claude Code and Cursor against a real stack so that client-specific MCP quirks surface during F-4 and not during first use.

**Acceptance Criteria:**
- [ ] A short manual test script in `docs/f4-smoke.md` walks through: install, add to Claude Code `.mcp.json`, add to Cursor settings, call `remember` + `recall` from each client, verify the same row appears in psql.
- [ ] The script lists every `.mcp.json` / Cursor config snippet verbatim (ready to paste) so future re-verification takes minutes, not spelunking.
- [ ] Errors surfaced by each client during the walkthrough are captured in the doc for future reference.

### US-007: Error handling and graceful degradation
**Description:** As an agent, I want MCP tool errors to be structured and clearly attributable so that I don't get opaque tracebacks when infra is down.

**Acceptance Criteria:**
- [ ] When postgres is unreachable, every tool returns a structured error naming `db_unreachable` with a hint like "run `docker compose up -d`".
- [ ] When the embed server is unreachable, `remember` and `recall` return a structured error naming `embed_unreachable`; `list_projects`, `show`, `forget`, `stats` still work (they don't need embeddings).
- [ ] An unexpected exception in core is caught at the MCP boundary, logged with traceback to stderr, and returned as a structured `internal_error` to the client (no raw traceback leaks into the tool response).

## 4. Functional Requirements

- **FR-1:** The MCP server must live under `munin/mcp/` and expose an entry point `munin-mcp = "munin.mcp.server:main"` in `pyproject.toml`.
- **FR-2:** The server must use FastMCP (`mcp` Python SDK, FastMCP flavor) and stdio transport only. No HTTP/SSE in v1.
- **FR-3:** Every tool must be a thin wrapper calling into `core.memory.*` — the MCP module must not import `psycopg`, `httpx`, or any infra dependency directly.
- **FR-4:** Tool names and semantics must mirror the CLI verbs: `remember`, `recall`, `list_projects`, `show`, `forget`, `stats`.
- **FR-5:** Each tool's input schema must be defined via typed Python annotations (pydantic or stdlib dataclass/TypedDict — whichever FastMCP prefers) so agents see real types.
- **FR-6:** Project scope is resolved from the MCP server's `cwd` via `core.scope.current_project()` at server start. The resolved project is stored in a server-level context so every tool call uses it — agents do not pass `project` on each call.
- **FR-7:** If `core.scope.current_project()` returns `None` at server start (not inside a git repo), the server must log a warning to stderr but still start; tools that require a project will fail with a clear error until `project` is set via config or env.
- **FR-8:** Errors must be structured. Define an error shape: `{"error": {"code": "<code>", "message": "<human>", "hint": "<optional>"}}`. Codes: `db_unreachable`, `embed_unreachable`, `not_found`, `validation_error`, `internal_error`.
- **FR-9:** The server must log tool invocations (tool name, duration, success/failure) to stderr so that MCP client logs capture a usable trace. No stdout logging — stdout is the MCP protocol channel.
- **FR-10:** `pyproject.toml` adds `mcp[cli]>=1.0` (or whatever the current FastMCP package is at implementation time — check during F-4).
- **FR-11:** Config snippets for Claude Code (`.mcp.json`) and Cursor (settings JSON) must be included in `docs/f4-smoke.md` and referenced from the main `README.md`.
- **FR-12:** A smoke test under `tests/mcp/` must spawn the server as a subprocess and issue at least `remember` + `recall` via the MCP client library, verifying the tool result contains the expected fields.

## 5. Non-Goals (Out of Scope)

- HTTP / SSE / streamable-http transports — stdio only in v1.
- Running one server that handles multiple projects. One server process = one project scope.
- Auto-capture tools (`capture_decision`, auto-remember from context). Deferred.
- MCP prompts or resources — tools only.
- Auth, token gating, rate limiting.
- `munin_import` tool for bulk ingestion (that's F-5 CLI scope; not exposed via MCP in v1).
- `munin_doctor` tool (F-5).
- Streaming / paginated recall results.
- Exposing raw embedding generation (`embed_text`) as a tool.
- Server-side result cache.
- Custom MCP protocol extensions.
- Hot config reload — server must be restarted to pick up config changes.
- Parallel / concurrent tool invocations (stdio is sequential; FastMCP handles this).

## 6. Design Considerations

- **Tool response shape:** return typed dicts that JSON-serialize cleanly. Reuse the dataclasses from `core/` where possible — don't reshape data per surface.
- **Project echoed in responses:** `remember` and `recall` responses include the resolved `project` in the result payload so agents can verify auto-scoping worked (useful when debugging "why did my memory land in the wrong bucket").
- **Logging target:** stderr, line-prefixed with timestamp and tool name. Avoid any library that captures stdout (it would break MCP).
- **Server identity in MCP handshake:** name `munin`, version from package metadata. Description short: "Local memory store for coding agents."

## 7. Technical Considerations

- **FastMCP version drift:** the MCP SDK is evolving. Pin a known-good version during implementation and note it in `pyproject.toml`. Revisit only if a tool schema feature we need lands in a newer release.
- **Stdio locking:** FastMCP handles framing; we must not print anything to stdout from library code. Audit `core/` imports to make sure nothing stray uses `print()`.
- **Startup failure vs. lazy failure:** fail soft on missing project (warn + continue), fail hard on missing `core.config` values (exit non-zero with a clear error). Rationale: user can fix project by `cd`-ing; broken config means the process can't function at all.
- **Cursor quirks:** Cursor's MCP config historically differs from Claude Code's. Document both snippets with full paths and expected `env` entries.
- **Testing:** FastMCP provides a client for in-process tests. Prefer that over subprocess tests for unit-level coverage; keep one subprocess smoke test for confidence in the real handshake.
- **Hard dependency on `core/`:** when `core.scope.current_project()` is added in F-2, ensure its signature matches what F-4 expects. Coordinate during F-2 implementation if it drifts.

## 8. Success Metrics

- All 7 user stories' acceptance criteria pass.
- `munin-mcp` registers successfully in both Claude Code and Cursor (recorded in `docs/f4-smoke.md`).
- Round-trip from either client: `remember("hello from mcp")` → `recall("hello")` returns the stored thought with similarity > 0.8.
- MCP server stays alive across at least 20 sequential tool calls in a single session without leaking connections or crashing.
- stderr logs show one line per tool call with name, duration, and outcome.

## 9. Open Questions

- **FastMCP package name / version:** confirm the exact import path and version at implementation time (ecosystem has churned).
- **Cursor's expected config schema:** verify against current Cursor docs during F-4 — not hard-coded from memory.
- **`project` override on tools:** initially the design is "server-scoped, no per-call override." If agents routinely need cross-project recall, consider adding an optional `project` arg to `recall` / `list_projects` in a follow-up. Tracked, not built.
- **Health / ping tool:** should `stats` double as a liveness check, or should there be a dedicated `ping` tool? Leaning: `stats` is enough — it already exercises db + embed reachability.
- **Capturing tool-call metadata as thought metadata:** should `remember` auto-tag `source: "mcp"` on every call? Leaning yes — free signal, zero cost. Confirm during implementation.
