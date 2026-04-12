# Feature: F-5 — Quality of Life

## 1. Introduction / Overview

F-1 through F-4 ship a working memory store with CLI and MCP surfaces. F-5 turns it into something pleasant to live with every day: bulk import so you can seed the store from existing notes, `munin doctor` for self-diagnosis when something breaks, persistent logs so failures are debuggable after the fact, and shell completion so commands are discoverable without `--help`. This Feature adds no new memory primitives — everything here wraps, inspects, or formats the primitives that already exist.

## 2. Goals

- Bulk-seed the store from external sources (jsonl, markdown folders) without hand-typing every thought.
- Self-diagnosis command that catches the common breakages (db down, embed down, bad config, missing migrations) before the user has to spelunk.
- Persistent, rotating log file so post-mortem debugging doesn't depend on scrollback.
- Shell completion for bash/zsh/fish so subcommands, flags, and (where feasible) values autocomplete.
- Documentation updates so new users go from `git clone` to "working CLI + MCP" without reading source.

## 3. User Stories

### US-001: Import thoughts from a jsonl file
**Description:** As a user, I want to run `munin import <file.jsonl>` and have each line ingested as a thought so that I can seed the store from existing notes or scripts without manual entry.

**Acceptance Criteria:**
- [ ] Running `munin import thoughts.jsonl` reads the file line by line and calls `remember` for each, with content, project, scope, tags, and metadata taken from the JSON fields on that line.
- [ ] Rows missing `content` are skipped with a warning; rows missing `project` fall back to the current git project (same rule as `munin remember`).
- [ ] At the end of the run, a summary prints: `Imported: N | Skipped: M | Failed: K` and the command exits 0 if at least one row imported successfully and no unrecoverable errors occurred.

### US-002: Import thoughts from a markdown folder
**Description:** As a user, I want to point munin at a folder of `.md` files and have each file become a thought (with frontmatter driving metadata) so that I can turn an existing notes folder into memory without converting to jsonl first.

**Acceptance Criteria:**
- [ ] Running `munin import <folder> --format markdown` walks the folder, ingesting each `.md` file as one thought where the body becomes `content` and YAML frontmatter (if present) populates `project`, `scope`, `tags`, `metadata`.
- [ ] Files without frontmatter still import successfully, using the current git project as `project` and empty `tags`/`metadata`.
- [ ] Re-running the same import is idempotent — row count does not grow, though `updated_at` may advance for changed files (via F-1's dedup).

### US-003: `munin doctor` self-diagnosis
**Description:** As a user, I want a single command that checks every dependency munin has so that when something is broken I get a checklist of what's wrong and what to do about it.

**Acceptance Criteria:**
- [ ] Running `munin doctor` prints a checklist of checks (config loaded, db reachable, db schema present, embed server reachable, embed dim matches config, log file writable) with a pass/fail mark next to each.
- [ ] Each failing check prints a one-line remediation hint (e.g., "run `docker compose up -d`" for db unreachable, "apply `sql/*.sql`" for missing schema).
- [ ] `munin doctor` exits 0 when all checks pass, non-zero when any fail; `--json` prints the same results as a structured object.

### US-004: Persistent logging to `~/.local/state/munin/munin.log`
**Description:** As a user debugging a flaky run, I want munin to write structured logs to a rotating file so that I can inspect what happened after the fact without re-running with `-v`.

**Acceptance Criteria:**
- [ ] After any `munin` CLI or `munin-mcp` invocation, the file `~/.local/state/munin/munin.log` contains at least an `INFO`-level entry for the command/tool that ran, including timestamp, level, module, and message.
- [ ] Running any command with `--verbose` (CLI) or setting `MUNIN_LOG_LEVEL=DEBUG` (any surface) additionally writes DEBUG-level entries.
- [ ] When the log file reaches 10 MB it rotates (up to 5 backups retained as `munin.log.1` … `munin.log.5`); the rotation itself never throws and never breaks a running command.
- [ ] Existing CLI stdout/stderr behavior is unchanged — file logging is additive, not a replacement.

### US-005: Shell completion for bash, zsh, and fish
**Description:** As a user, I want tab-completion for `munin` subcommands and flags in my shell so that I can discover options without reading `--help`.

**Acceptance Criteria:**
- [ ] Running `munin completion install --shell zsh` (and `bash` / `fish`) writes or prints a completion script for the active shell, following typer's built-in completion generation.
- [ ] After installing and sourcing the script, typing `munin <TAB>` in a fresh shell session lists every subcommand; typing `munin recall --<TAB>` lists every `recall` flag.
- [ ] `munin completion install --help` documents where the script is written on disk per shell and how to activate it if the user prefers to install manually.

### US-006: Updated README with end-to-end install flow
**Description:** As a new user, I want a README that walks me from `git clone` to "working CLI + MCP" in a few minutes so that I don't have to piece the flow together from feature docs.

**Acceptance Criteria:**
- [ ] `README.md` contains a "Quickstart" section with these steps in order: clone, `docker compose up -d`, apply `sql/*.sql`, `pipx install -e .`, run `munin doctor`, run a first `remember` + `recall`, add to `.mcp.json` for Claude Code and Cursor.
- [ ] Each step is copy-pasteable as a shell command (no "edit file X to add Y" without showing the exact content).
- [ ] The Quickstart finishes by pointing to `docs/decisions.md`, `docs/plan.md`, and `tasks/epic-munin-v1.md` for deeper reading.

## 4. Functional Requirements

- **FR-1:** `munin import <path>` must detect format automatically when possible: `.jsonl` → jsonl mode, directory → markdown mode. `--format {jsonl,markdown}` overrides autodetect.
- **FR-2:** The jsonl schema is `{content: str, project?: str, scope?: str, tags?: list[str], metadata?: object}` — one JSON object per line. Unknown keys are ignored; invalid JSON lines print a warning and are skipped.
- **FR-3:** The markdown import reads YAML frontmatter (`---` delimited at the top of the file). Frontmatter keys map to `project`, `scope`, `tags`, `metadata`. The rest of the file is `content`.
- **FR-4:** Import must process rows sequentially in v1 (no concurrent `remember` calls). Batching embeddings via `core.embed.embed_batch` is allowed and encouraged if it preserves row-level error reporting.
- **FR-5:** `munin doctor` must perform at minimum these checks: config loads without error, db connection opens, `thoughts` table exists, `vector` extension installed, `match_thoughts` and `upsert_thought` functions exist, embed server HTTP reachable, embed server returns a vector of the configured dimension, log directory exists and is writable.
- **FR-6:** `munin doctor` exit code: `0` all pass, `1` one or more checks failed.
- **FR-7:** Logging must use stdlib `logging`. A `RotatingFileHandler` writes to `~/.local/state/munin/munin.log` (path resolved via `platformdirs` or an equivalent) with `maxBytes=10_000_000` and `backupCount=5`.
- **FR-8:** The log directory is created on first use (via `mkdir -p` equivalent); failure to create it emits a single stderr warning and falls back to logging only to stderr — it must not crash the command.
- **FR-9:** Log format: `%(asctime)s %(levelname)s %(name)s %(message)s`. Default level `INFO`, overridable by `MUNIN_LOG_LEVEL` env var or `--verbose` CLI flag (sets DEBUG).
- **FR-10:** Shell completion uses typer's built-in completion generator. `munin completion install --shell {bash,zsh,fish}` writes to the standard location per shell (documented in `--help`) or prints the script to stdout if `--stdout` is passed.
- **FR-11:** `README.md` must be kept in sync with this Feature's Quickstart, and this file (`feature-quality-of-life.md`) is the source of truth for the sequence.
- **FR-12:** Every new CLI subcommand (`import`, `doctor`, `completion`) honors the existing `--json` / exit code conventions from F-3 where applicable (`doctor --json`, `import --json` for the summary).

## 5. Non-Goals (Out of Scope)

- OB1 migration adapter (user does not run OB1).
- `munin export` — writing the store back out to jsonl/markdown.
- In-place thought editing (`munin update`).
- Retention / TTL / scheduled cleanup jobs.
- Cron or systemd unit files.
- Web UI / dashboard.
- Metrics export (Prometheus, OTEL).
- Interactive config wizard (`munin init`).
- Self-update (`munin upgrade`).
- Backup / restore helpers — `pg_dump` / `pg_restore` remain the advised path, documented briefly in README.
- Encryption at rest.
- Multi-user migration tooling.
- Log shipping to external services.
- Auto-capture of shell commands or agent conversations.
- Incremental / resumable imports (v1 is one-shot; re-running is safe via dedup).

## 6. Design Considerations

- **`doctor` output:** one-line-per-check checklist with `✓` / `✗` (or `OK` / `FAIL` when `NO_COLOR` is set), remediation hint inline on failure. Keep width reasonable so it reads in a narrow terminal.
- **Import progress:** for long imports, a simple `rich.progress` bar showing `N/Total` is acceptable; do not print a line per imported row in human mode. `--verbose` may print per-row.
- **Completion install UX:** prefer printing exactly the one-line shell command the user should run (e.g., `eval "$(munin --show-completion zsh)"` style) over writing to shell rc files behind their back.

## 7. Technical Considerations

- **Platformdirs:** `~/.local/state/munin/` on Linux, macOS equivalent via `platformdirs.user_state_path("munin")`. Avoid hand-rolling platform detection — add `platformdirs>=4` to deps if not already pulled transitively.
- **Frontmatter parser:** `python-frontmatter` is the obvious choice. Small, well-known, no runtime footprint concerns.
- **Import performance:** for 10k rows the bottleneck is embedding throughput, not db inserts. Batch embeds via `core.embed.embed_batch` (default batch 32 from F-2). Measure on realistic input before adding concurrency.
- **Doctor checks must be cheap:** total runtime under ~1 second on a healthy system. Each check has a 2-second timeout; timing out counts as a failure with hint "check <service> is responsive."
- **Log rotation races:** `RotatingFileHandler` is not safe across processes. This is acceptable because in v1 we have exactly one CLI process or one MCP server at a time. If multi-process logging becomes an issue later, revisit with `concurrent-log-handler` or a per-process filename.
- **Typer completion quirk:** typer's completion generator works but its install UX varies across shells; test on at least zsh (user's default) and bash (common) during F-5.

## 8. Success Metrics

- All 6 user stories' acceptance criteria pass.
- Running `munin doctor` against a broken environment (db stopped) shows exactly which check failed and a useful hint — verified manually.
- Importing a 1,000-line jsonl file completes without manual intervention and produces 1,000 rows (assuming all unique content).
- Tab-completing `munin <TAB>` in zsh after install lists all subcommands including `import`, `doctor`, `completion`.
- A new user following the README Quickstart from a clean machine reaches a working `munin recall` in under 10 minutes (informal, no stopwatch required).

## 9. Open Questions

- **`doctor` check for "migrations applied":** how do we reliably detect that `sql/*.sql` have been applied, given there is no migration runner in v1? Candidate: check that `thoughts` table exists *and* has the `content_fingerprint` column. Tracked; settle during implementation.
- **Rotation size / count:** 10MB × 5 is a guess. If logs stay small in practice, reduce to 5MB × 3. Not worth tuning until we have real data.
- **Markdown frontmatter key for `metadata`:** nested dict (`metadata: {source: cli}`) or flat keys under a `meta:` namespace? Leaning nested dict — cleaner, matches jsonl schema.
- **Shell completion for thought ids:** out of scope for v1 (would require calling into the db on every TAB). Worth revisiting if users actively want it.
- **Should `munin import` support `--dry-run`?** Leaning yes — cheap to add, very useful when vetting a new file. Treat as nice-to-have; add if time permits.
