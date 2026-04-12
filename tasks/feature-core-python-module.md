# Feature: F-2 — Core Python Module

## 1. Introduction / Overview

Build the Python `core/` package that both the CLI (F-3) and MCP server (F-4) wrap. This is the single source of truth for memory operations: database connection pooling, embedding generation, project scope detection, config loading, and the `remember`/`recall`/`list_projects`/`show`/`forget` memory verbs. When F-2 ships, the munin memory model is fully usable from a Python REPL — no CLI, no MCP needed. Downstream surfaces become thin adapters that translate user input into calls against this module.

## 2. Goals

- A cohesive `core/` package that can be imported and driven from a Python REPL to prove end-to-end memory flow works.
- Zero duplication between future CLI and MCP surfaces — every memory operation lives here once.
- Deterministic config resolution: TOML file + environment overrides, with sane defaults for a localhost docker-compose setup.
- Project auto-detection: calling `remember` from inside a git repo tags the thought with the repo name without the caller specifying it.
- Integration tests that run against the real postgres + llama.cpp stack, not mocks.

## 3. User Stories

### US-001: Config resolution loads defaults, TOML, and env
**Description:** As a munin user, I want config to work out of the box with the docker-compose defaults but be overridable via TOML and env vars so that I can point at a different database or embed server without editing code.

**Acceptance Criteria:**
- [ ] With no config file and no env vars set, `core.config.load()` returns defaults pointing at `postgresql://munin:munin@localhost:5433/munin` and `http://localhost:8088`.
- [ ] When `~/.config/munin/config.toml` exists with a `db_url` value, that value wins over the default.
- [ ] When `MUNIN_DB_URL` env var is set, it wins over both the TOML file and the default.

### US-002: Database connection pool and embed client are reusable primitives
**Description:** As a developer consuming `core/`, I want db and embed access via single-entry functions so that I never open raw connections or HTTP sessions by hand.

**Acceptance Criteria:**
- [ ] Calling `core.db.get_pool()` twice in the same process returns the same pool instance (singleton per config).
- [ ] Calling `core.embed.embed("hello")` against a running llama.cpp server returns a list of 768 floats.
- [ ] Calling `core.embed.embed_batch(["a", "b", "c"])` returns three embedding vectors in input order.

### US-003: `remember` ingests a thought with auto-detected project
**Description:** As a user, I want to call `remember("some insight")` from inside a repo and have munin auto-tag the thought with the current git project so that I don't have to name the project every time.

**Acceptance Criteria:**
- [ ] Calling `core.memory.remember("hello world")` from inside a git repo inserts a row whose `project` equals the repo directory name.
- [ ] Calling `remember` with an explicit `project="other"` overrides auto-detection.
- [ ] Calling `remember` twice with the same content in the same project results in exactly one row in the database (dedup via F-1's `upsert_thought`).

### US-004: `recall` returns filtered vector search results
**Description:** As a user, I want `recall("what did I decide about auth?")` to return the most similar past thoughts filtered to my current project so that I don't have to paginate through hundreds of unrelated memories.

**Acceptance Criteria:**
- [ ] Calling `core.memory.recall("<query>")` from inside a git repo returns up to `limit` (default 10) thoughts scoped to the current project, ordered by descending similarity.
- [ ] Passing `scope="design"` further restricts results to rows where `scope = 'design'`.
- [ ] Passing `threshold=0.5` omits any result below that similarity score.
- [ ] Each returned result exposes `id`, `content`, `project`, `scope`, `tags`, `metadata`, `similarity`, and `created_at` as a typed object (dataclass or pydantic model).

### US-005: `list_projects`, `show`, and `forget` round out the CRUD surface
**Description:** As a user, I want to see which projects have thoughts, fetch a single thought by id, and delete a thought so that the memory store is fully manageable from code.

**Acceptance Criteria:**
- [ ] `core.memory.list_projects()` returns a list of `(project, thought_count)` tuples for every distinct project in the store.
- [ ] `core.memory.show(thought_id)` returns the full thought object for a valid id, and `None` for a missing id.
- [ ] `core.memory.forget(thought_id)` hard-deletes the row and returns `True`; calling it on a missing id returns `False`.

### US-006: Integration tests prove end-to-end flow against the real stack
**Description:** As a maintainer, I want integration tests that exercise the full stack (pg + llama.cpp) so that I catch contract drift between core and infra before CLI/MCP pile on top.

**Acceptance Criteria:**
- [ ] Running `pytest tests/integration/` against the running docker-compose stack executes tests that remember, recall, show, and forget real thoughts end-to-end (no mocks of pg or the embed server).
- [ ] The integration suite truncates the `thoughts` table between tests so runs are deterministic and re-runnable.
- [ ] When the embed server is unreachable, tests fail with a clear error message rather than hanging.

## 4. Functional Requirements

- **FR-1:** The `core/` package must expose (at minimum) `core.config`, `core.db`, `core.embed`, `core.scope`, and `core.memory` modules.
- **FR-2:** `core.config.load()` must resolve config in precedence order: env vars → `~/.config/munin/config.toml` → built-in defaults.
- **FR-3:** Config keys (initial set): `db_url`, `embed_url`, `embed_dim` (default 768), `default_limit` (default 10).
- **FR-4:** `core.db` must use `psycopg` v3 synchronous API with a `ConnectionPool` sized from config (default min=1 max=4).
- **FR-5:** `core.embed` must use `httpx` synchronous client and call `POST {embed_url}/v1/embeddings` with body `{"input": <str or list[str]>}`, returning `list[float]` or `list[list[float]]` depending on input type.
- **FR-6:** `core.embed.embed_batch` must accept any-length list and batch internally if a configurable max batch size is exceeded (default batch size 32). Callers never need to batch manually.
- **FR-7:** `core.scope.current_project()` must detect the current git repo by walking up from `cwd` looking for `.git`, returning the containing directory name. If no `.git` is found, return `None`.
- **FR-8:** `core.memory.remember(content, *, project=None, scope=None, tags=None, metadata=None)` must: embed the content, resolve project (arg → `scope.current_project()` → raise if still `None`), call `upsert_thought` RPC, return the thought id.
- **FR-9:** `core.memory.recall(query, *, project=None, scope=None, limit=None, threshold=0.0)` must: embed the query, resolve project same way, call `match_thoughts` RPC, return a list of `ThoughtResult` dataclasses.
- **FR-10:** `core.memory.list_projects()` must execute `SELECT project, COUNT(*) FROM thoughts GROUP BY project ORDER BY project`.
- **FR-11:** `core.memory.show(thought_id)` must `SELECT * FROM thoughts WHERE id = $1` and return a `Thought` dataclass or `None`.
- **FR-12:** `core.memory.forget(thought_id)` must `DELETE FROM thoughts WHERE id = $1 RETURNING id` and return a bool indicating whether a row was removed.
- **FR-13:** All public functions must have type hints precise enough for `mypy --strict` to pass on `core/`.
- **FR-14:** Integration tests must live under `tests/integration/` and rely on the already-running docker-compose stack (no spin-up inside tests in v1). A dedicated test database/schema name is used so dev data isn't touched.
- **FR-15:** `pyproject.toml` must pin direct deps: `psycopg[binary,pool]>=3.2`, `httpx>=0.27`, `tomli>=2.0` (for Python <3.11 fallback not needed since we target 3.11+), and dev deps: `pytest`, `pytest-xdist`, `ruff`, `mypy`.

## 5. Non-Goals (Out of Scope)

- CLI entry point, shell invocation, `--json` flag (F-3).
- MCP server, FastMCP wrapping, Claude Code / Cursor integration (F-4).
- Async variants of db/embed/memory (sync only in v1).
- Alternative embedding providers (OpenAI, Voyage, in-process fastembed) — nomic via llama.cpp only.
- Bulk import (`jsonl`, markdown folder) — F-5.
- `munin doctor` health check command — F-5.
- Shell completion — F-5.
- Logging to `~/.local/state/munin/munin.log` — F-5 (core uses stdlib `logging` but does not configure file handlers).
- Auth, row-level security, multi-user isolation.
- In-memory or on-disk embedding cache.
- Migration runner (DDL is manually applied via `psql -f sql/*.sql` after F-1).
- TTL / retention jobs.
- Benchmarks against corpus size (informal only).

## 6. Design Considerations

None (pure library code, no UI).

## 7. Technical Considerations

- **Python version:** 3.11+ (uses `tomllib` from stdlib, PEP 695 type params where helpful).
- **Connection pooling:** `psycopg_pool.ConnectionPool` is the standard. Size kept small (1–4) because this is local single-user tooling.
- **Embed HTTP timeouts:** 30s default, configurable. Retry on transient 5xx with exponential backoff (max 2 retries).
- **Batching:** llama.cpp `/v1/embeddings` accepts `input` as either a string or a list of strings. Code must handle both so single-item and batched calls share a path.
- **Dataclasses vs pydantic:** use stdlib `dataclasses` for returned models. Pydantic adds a dep without meaningful value inside a library; MCP/CLI layers can serialize as needed.
- **Scope detection walks up the tree:** look at `cwd`, then parent, until we hit a `.git` directory or the filesystem root. Cache per-process.
- **Integration tests share the dev compose stack:** Use a throwaway schema (`test_munin`) or table suffix to avoid clobbering real thoughts. Alternatively, use a separate `pgdata` via a `docker-compose.test.yml` override — prefer schema isolation unless tests prove flaky.
- **Module boundary hygiene:** `core/` must not import from `cli/` or `mcp/` (enforced by review, optionally by an import-linter rule in F-3/F-4).
- **Error model:** define a small exception hierarchy — `MuninError`, `MuninConfigError`, `MuninEmbedError`, `MuninDBError` — so downstream surfaces can translate cleanly.

## 8. Success Metrics

- All 6 user stories' acceptance criteria pass via `pytest tests/integration/` against the running compose stack.
- `mypy --strict core/` is clean.
- `ruff check` is clean.
- A Python REPL session (`python -c "from munin.core import memory; memory.remember('hello'); print(memory.recall('hello'))"`) works end-to-end from inside a git repo with no additional setup beyond `docker compose up -d`.

## 9. Open Questions

- **Package root name:** ship as `munin.core` (top-level `munin` package) or just `core` in a flat layout? Leaning `munin.core` — cleaner for pyproject entry points and MCP server naming.
- **Embedding cache:** deferred. If repeated `recall` of identical queries becomes hot, add a small LRU on `(query_hash, dim) → vector`. Tracked, not built.
- **Test isolation strategy:** schema-per-test-run vs truncate-between-tests vs docker-compose.test.yml. Start with TRUNCATE in a test-only schema; escalate only if flaky.
- **Default limit value:** 10 feels right for interactive use, might want 5 for MCP (context budget) — configurable in TOML so both surfaces can pick their own default.
- **`tags` / `metadata` shape at the Python boundary:** `tags: list[str]`, `metadata: dict[str, Any]` — confirm during implementation that json round-trips cleanly through psycopg without manual encoding.
