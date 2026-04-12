# Feature: F-3 — CLI (`munin` command)

## 1. Introduction / Overview

Expose the `core/` memory operations as a `munin` shell command usable from any terminal, any repo. The CLI is the first consumer of `core/` — proving the library is ergonomic — and the fastest debug loop for future work. Humans get rich tabular output; scripts and hooks get `--json` for piping.

## 2. Goals

- Single `munin` entry point installable via `pipx install -e .`.
- Every core verb reachable: `remember`, `recall`, `projects`, `show`, `forget`, `stats`.
- Both human-friendly and machine-friendly output modes on every command.
- Project auto-detection from the current working directory; `--project` override when needed.
- Typed, validated arguments via typer so `--help` is accurate and `-h` works.
- Stdin support for `remember` so shell pipes work: `git log -1 --format=%B | munin remember`.

## 3. User Stories

### US-001: Install and invoke `munin` from any shell
**Description:** As a user, I want to install munin with `pipx install -e .` and call `munin --help` from any directory so that I can start using it with zero manual path wiring.

**Acceptance Criteria:**
- [ ] After `pipx install -e .` from the repo root, running `munin --help` in any directory prints a command list containing `remember`, `recall`, `projects`, `show`, `forget`, `stats`.
- [ ] Running `munin --version` prints a semver-shaped version string.
- [ ] Running `munin` with no args prints the help text and exits 0.

### US-002: Remember a thought from argument or stdin
**Description:** As a user, I want to store a thought either as a CLI argument or piped on stdin so that both interactive and scripted workflows work.

**Acceptance Criteria:**
- [ ] Running `munin remember "decided to use pgvector HNSW"` from inside a git repo stores a thought with `project` auto-set to the repo name and prints the new thought id.
- [ ] Running `echo "piped insight" | munin remember` stores a thought with content equal to the piped text.
- [ ] Running `munin remember` with no argument, no stdin, and no `--project` prints an error to stderr and exits non-zero.
- [ ] Running `munin remember "x" --project other --scope design --tag auth --tag security` stores a row with those fields populated.

### US-003: Recall thoughts with filters
**Description:** As a user, I want to query my memory by natural language and filter by project/scope so that I can find past decisions without scrolling through everything.

**Acceptance Criteria:**
- [ ] Running `munin recall "auth decision"` from inside a git repo prints a rich table (human mode) of up to 10 thoughts from the current project, ordered by similarity descending, with content truncated to ~120 chars.
- [ ] Passing `--project P1 --scope design --limit 5 --threshold 0.4` applies all four filters to the RPC call and the resulting table reflects them.
- [ ] Running the same query with `--json` prints a JSON array of result objects containing `id`, `content`, `project`, `scope`, `tags`, `similarity`, `created_at` (ISO-8601) to stdout.

### US-004: Show a single thought in full
**Description:** As a user, I want to fetch one thought by id and see its full content so that recall's truncated table doesn't leave me guessing.

**Acceptance Criteria:**
- [ ] Running `munin show <id>` with a valid id prints the full thought: id, project, scope, tags, metadata, full (untruncated) content, created_at, updated_at.
- [ ] Running `munin show <missing-id>` exits non-zero and prints a clear "not found" error to stderr.
- [ ] Running `munin show <id> --json` prints a single JSON object to stdout (no human decoration).

### US-005: List projects and basic stats
**Description:** As a user, I want to see which projects exist in the store and get a quick health/size readout so that I know what's in memory without running psql.

**Acceptance Criteria:**
- [ ] Running `munin projects` prints a table of `(project, count)` sorted by project name; `--json` prints the same data as an array.
- [ ] Running `munin stats` prints total thought count, total project count, size on disk (best-effort), and the embed server URL/reachability.
- [ ] When the database is unreachable, `munin projects` and `munin stats` exit non-zero with a clear error (not a python traceback).

### US-006: Forget a thought safely
**Description:** As a user, I want to hard-delete a thought by id with a confirmation step so that I don't accidentally remove memories while piping commands around.

**Acceptance Criteria:**
- [ ] Running `munin forget <id>` prompts `Delete thought <id>? [y/N]:` and only deletes on `y`/`yes`.
- [ ] Running `munin forget <id> --yes` skips the prompt and deletes immediately.
- [ ] Running `munin forget <missing-id>` exits non-zero with a "not found" error.
- [ ] After a successful delete, the id is gone — `munin show <id>` on the same id exits non-zero.

### US-007: Global `--json` and consistent error model
**Description:** As a script author, I want every command to support `--json` and to fail with stable exit codes so that I can compose munin into pipelines without regex-parsing human output.

**Acceptance Criteria:**
- [ ] Every command that produces data (`remember`, `recall`, `show`, `projects`, `stats`) supports `--json`, and the JSON output contains no ANSI colors or human decoration.
- [ ] Every command exits 0 on success, 1 on expected errors (not found, validation), and 2 on config/connection errors — documented in `--help` for the top-level command.
- [ ] When the embed server or database is unreachable, error messages name which component failed and suggest running the compose stack.

## 4. Functional Requirements

- **FR-1:** The CLI must live under `munin/cli/` as a Python package and expose an entry point `munin = "munin.cli.main:app"` in `pyproject.toml`.
- **FR-2:** The CLI must use `typer` for command definition and `rich` for tabular/colored output. `rich` is only imported in human-mode code paths so `--json` stays fast and dep-clean.
- **FR-3:** The CLI must auto-detect the current project via `core.scope.current_project()` when `--project` is not passed. If auto-detection fails and the command requires a project, the CLI must exit with a clear error.
- **FR-4:** `remember` must accept: positional `content` (optional), `--project`, `--scope`, `--tag` (repeatable), `--metadata` (KEY=VALUE, repeatable), and must read from stdin when content is omitted and stdin is not a TTY.
- **FR-5:** `recall` must accept: positional `query` (required), `--project`, `--scope`, `--limit` (default 10), `--threshold` (default 0.0), `--json`.
- **FR-6:** `show` must accept a positional `thought_id` and `--json`.
- **FR-7:** `forget` must accept `thought_id` and `--yes` (`-y`) to skip confirmation.
- **FR-8:** `projects` and `stats` must both support `--json`.
- **FR-9:** Errors must go to stderr. Data and human tables must go to stdout.
- **FR-10:** Exit codes: `0` success, `1` validation/not-found, `2` infrastructure error (db/embed unreachable, bad config).
- **FR-11:** The CLI must not reach into the database or HTTP layer directly — it calls `core.memory.*` and nothing else from infra. This keeps CLI/MCP surface parity trivial.
- **FR-12:** `pyproject.toml` adds: `typer>=0.12`, `rich>=13`. Dev additions: none beyond F-2's pytest.
- **FR-13:** Install flow documented in `README.md`: `pipx install -e .` from the repo root after `docker compose up -d`.
- **FR-14:** CLI smoke tests (subprocess-invoked) exist under `tests/cli/` and cover: help text, `remember` from arg, `remember` from stdin, `recall --json`, `show`, `forget --yes`, error paths for missing id and unreachable infra.

## 5. Non-Goals (Out of Scope)

- MCP server (F-4).
- Bulk import from jsonl/markdown (F-5).
- Shell completion installation (F-5).
- `munin doctor` health check command (F-5).
- `$EDITOR` fallback for `remember` input.
- Interactive TUI (e.g., textual) for browsing recall results.
- Remote/HTTP mode — CLI only talks to local db/embed via `core/`.
- Auth, user switching, multi-tenant awareness.
- `munin init` / config wizard.
- `munin update <id>` (in-place thought editing).
- `munin export` — dumping the store out to a file.
- Thought diff/merge tools.
- Rich markdown rendering of stored content in the terminal.

## 6. Design Considerations

- **Table layout for `recall`:** columns `#`, `sim`, `project/scope`, `content`. Index column lets the user reference by position if they want a follow-up `show` (but the id is the canonical handle).
- **Truncation:** 120 chars of content by default, add `…` suffix when truncated, no word-boundary magic.
- **Colors:** dim project/scope columns, bold the first line of content. Nothing gaudy.
- **`--no-color` / `NO_COLOR` env var:** honored via rich's built-in behavior.
- **Help text:** `--help` at the top level shows a one-line summary per command, grouped by verb. Each subcommand has its own `--help` with argument descriptions.

## 7. Technical Considerations

- **Entry point:** typer's recommended pattern — `app = typer.Typer()`, commands registered via `@app.command()`, invoked via `app()` in `__main__`.
- **Stdin detection:** `sys.stdin.isatty()` is `False` when piped; read `sys.stdin.read()` only in that case.
- **JSON mode:** implement via a single helper that takes a dataclass / list / dict and routes to either `rich.print_json` or `print(json.dumps(...))`. Consistency matters more than prettiness.
- **Timestamp format:** ISO-8601 with timezone, always. Human mode may abbreviate (e.g., `2025-10-12 14:03`), JSON mode never.
- **Error translation:** catch `core.memory.MuninError` subclasses at the CLI boundary, print a friendly one-line error, return the right exit code. Let genuine programmer errors (`TypeError`, etc.) bubble up with a traceback in development builds only.
- **Rich dependency:** keep it at module top-level import since startup time matters less than consistency. Measure if `munin recall` feels laggy — optimize only if real.
- **Test harness:** typer ships a `CliRunner` (actually Click's, reused). Use it for in-process tests; use subprocess tests only for the install path and for the stdin pipe case.

## 8. Success Metrics

- All 7 user stories' acceptance criteria pass.
- `pipx install -e .` → `munin remember` → `munin recall` → `munin forget` round-trip works from any directory against a running compose stack with no additional config.
- `--json` output from every command parses cleanly with `jq` and contains no ANSI sequences.
- Help text for every command is accurate and exit codes match FR-10.

## 9. Open Questions

- **Alias for `munin recall`:** should there be a shorter alias (`munin r`)? Leaning no — ambiguous with `remember`. Decide during implementation.
- **Default `--limit` source:** CLI default (10) vs config default (`core.config.default_limit`). Leaning config default with CLI flag override.
- **Stats scope:** "size on disk" is awkward to measure accurately inside postgres from a client — may be approximate (`pg_total_relation_size('thoughts')`). Acceptable.
- **Thought id format for CLI UX:** full UUIDs are long. Consider short-prefix matching later (not in v1).
- **Metadata parsing on `--metadata KEY=VALUE`:** how to handle values with `=` in them? Leaning split on first `=` only; document it.
