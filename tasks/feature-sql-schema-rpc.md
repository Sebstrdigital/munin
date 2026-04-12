# Feature: F-1 — SQL Schema & RPC Primitives

## 1. Introduction / Overview

Establish the authoritative SQL layer munin is built on: a `thoughts` table with vector embeddings, supporting indexes, dedup via content fingerprint, and two RPC functions — `match_thoughts()` for filtered vector recall and `upsert_thought()` for dedup-aware insert. This Feature produces a fully manually-testable SQL contract (via `psql`) that every downstream surface (core module, CLI, MCP) will wrap without modification. No Python is written in this Feature.

## 2. Goals

- A stable SQL contract downstream features can depend on without churn.
- Vector similarity search scoped by `project` and (optionally) `scope`.
- Idempotent ingestion: the same content ingested twice results in one row, not two.
- All behavior verifiable from `psql` alone — no Python, no CLI.
- Migration files numbered and ordered so a fresh database reaches the same state via `psql -f sql/*.sql`.

## 3. User Stories

### US-001: Thoughts table and core indexes exist
**Description:** As a developer building munin, I want a `thoughts` table with the agreed columns and indexes so that downstream code has a stable schema to target.

**Acceptance Criteria:**
- [ ] After running `sql/001_schema.sql`, `\d thoughts` in psql shows columns: `id` (uuid pk), `content` (text), `embedding` (vector(768)), `project` (text), `scope` (text nullable), `tags` (text[]), `metadata` (jsonb), `content_fingerprint` (text), `created_at` (timestamptz), `updated_at` (timestamptz).
- [ ] A btree index exists on `(project, scope)` and an HNSW index exists on `embedding` using cosine distance.
- [ ] Inserting a row without `updated_at` and then updating it causes `updated_at` to advance automatically (trigger works).

### US-002: Duplicate content is rejected or merged via fingerprint
**Description:** As a user of munin, I want the store to reject or merge duplicate content so that I don't end up with the same thought twice when a workflow re-ingests it.

**Acceptance Criteria:**
- [ ] After running `sql/002_fingerprint.sql`, a unique partial index exists on `content_fingerprint` scoped per `project`.
- [ ] Inserting two rows with the same content into the same project produces exactly one row in `thoughts`, with `updated_at` advanced on the second call.
- [ ] Inserting the same content into two different projects produces two rows (dedup is project-scoped, not global).

### US-003: Filtered vector recall via `match_thoughts()`
**Description:** As a downstream consumer, I want a single RPC that performs vector similarity search filtered by project and scope so that I don't have to assemble queries by hand in every language.

**Acceptance Criteria:**
- [ ] Calling `SELECT * FROM match_thoughts(query_embedding, project := 'P1', scope := NULL, match_limit := 5, similarity_threshold := 0.0)` returns the top 5 most similar rows where `project = 'P1'`, ordered by descending similarity.
- [ ] Passing a non-NULL `scope` further restricts results to that scope only.
- [ ] Passing `similarity_threshold := 0.5` excludes any row whose similarity score is below 0.5.
- [ ] When no rows match the filter, the function returns zero rows (does not error).

### US-004: Dedup-aware ingestion via `upsert_thought()`
**Description:** As a downstream consumer, I want a single RPC that handles dedup-on-insert so that every surface (CLI, MCP) uses the same insert semantics.

**Acceptance Criteria:**
- [ ] Calling `upsert_thought(content, embedding, project, scope, tags, metadata)` on new content inserts a row and returns its `id`.
- [ ] Calling the same function with identical content in the same project updates the existing row's `updated_at`, `tags`, and `metadata` and returns the *same* `id`.
- [ ] The function computes `content_fingerprint` internally — callers never set it directly.

### US-005: End-to-end manual smoke test from psql
**Description:** As the author of this Feature, I want a reproducible psql session that ingests three rows across two projects and recalls them with different filters so that I can prove the contract works before any Python is written.

**Acceptance Criteria:**
- [ ] A documented psql snippet (in `sql/README.md` or `docs/f1-smoke.md`) ingests three thoughts across projects `P1` and `P2` using hand-written dummy 768-dim vectors.
- [ ] Running the snippet's recall queries returns the expected rows: `project = 'P1'` filter returns only P1 rows; `project = 'P2'` returns only P2 rows.
- [ ] Re-running the ingestion block is idempotent — row count does not grow on the second run.

## 4. Functional Requirements

- **FR-1:** The database schema must live in numbered SQL files under `sql/` (`001_schema.sql`, `002_fingerprint.sql`, `003_match_thoughts.sql`, `004_upsert_thought.sql`), applied in filename order.
- **FR-2:** The `embedding` column must be `vector(768)` — the nomic-embed-text-v1.5 output dimension.
- **FR-3:** The vector index must use HNSW with cosine distance (`vector_cosine_ops`). IVFFLAT is explicitly not used in v1 — revisit only if row count crosses ~500k (see Open Questions).
- **FR-4:** The `scope` column must be plain `TEXT` (nullable). Hierarchical paths (`ltree`) are not used in v1 — revisit if product decisions need path-prefix queries.
- **FR-5:** `match_thoughts()` must accept arguments `(query_embedding vector(768), project text, scope text DEFAULT NULL, match_limit int DEFAULT 10, similarity_threshold float DEFAULT 0.0)` and return rows ordered by cosine similarity descending.
- **FR-6:** `match_thoughts()` must return similarity as `1 - (embedding <=> query_embedding)` so higher is better (consistent with OB1 convention).
- **FR-7:** `upsert_thought()` must compute `content_fingerprint` as `md5(content)` internally. (SHA is not needed — collision risk is negligible at this scale and md5 is faster.)
- **FR-8:** `forget` semantics: hard delete via `DELETE FROM thoughts WHERE id = $1`. No tombstone column, no soft-delete filter.
- **FR-9:** An `updated_at` trigger must advance `updated_at` on every row update automatically.
- **FR-10:** All SQL must apply cleanly against a fresh `pgvector/pgvector:pg16` database that only has `CREATE EXTENSION vector` pre-run.

## 5. Non-Goals (Out of Scope)

- Python code, CLI, or MCP server (future features).
- Configurable embedding dimension (768 is hard-coded v1).
- Soft-delete / tombstone / audit log columns.
- Cross-project recall / joining thoughts across projects in one query.
- Full-text search (`tsvector`, `pg_trgm`) alongside vector search.
- Retention policy, TTL, automatic cleanup.
- Row-level security, auth, multi-tenant isolation beyond the `project` column.
- IVFFLAT index (tracked as open question, not built).
- `ltree` hierarchy for scope (tracked as open question, not built).
- Data migration / schema evolution tooling (Alembic, sqitch, etc.).
- Performance benchmarks (deferred to F-2).

## 6. Design Considerations

None — pure SQL, no UI.

## 7. Technical Considerations

- **pgvector version:** 0.8.2 (already running in docker-compose, confirmed). HNSW requires pgvector ≥ 0.5.
- **Distance operator:** cosine (`<=>`). `vector_cosine_ops` on the HNSW index.
- **Similarity score:** returned as `1 - distance` so 1.0 = identical, 0.0 = orthogonal, consistent with OB1.
- **Fingerprint:** `md5(content)` — not cryptographic, collision-resistant enough for dedup at expected scale.
- **Dummy embeddings for testing:** use simple handwritten 768-element arrays (e.g., `array_fill(0.1, ARRAY[768])::vector`) in smoke tests — avoids needing the llama.cpp server for pure-SQL verification.
- **Migration application:** `psql -U munin -d munin -f sql/001_schema.sql && ...` — no migration runner yet. A future feature may add one, or reuse an existing tool.
- **HNSW parameters:** leave at pgvector defaults (`m = 16`, `ef_construction = 64`) for v1. Tune only if recall quality proves inadequate in F-2.

## 8. Success Metrics

- All 5 user stories' acceptance criteria pass in a single psql session against a fresh database.
- Re-running `sql/*.sql` against an already-migrated database produces no errors (idempotent DDL via `IF NOT EXISTS` where possible).
- Smoke test session is short enough (< 50 lines) to paste into a terminal for manual verification.

## 9. Open Questions

- **Index strategy revisit trigger:** At what row count does HNSW stop being a good fit and IVFFLAT become preferable? Working assumption: revisit at ~500k thoughts or when build time becomes a problem. Tracked, not acted on.
- **Scope shape revisit trigger:** When would `ltree` become worth the complexity? Working assumption: only if we start querying by path prefixes frequently (e.g., `project/area/subarea/*`). Tracked, not acted on.
- **`scope` NULL semantics in `match_thoughts()`:** Passing `scope := NULL` means "any scope" (not "scope IS NULL"). Confirmed — will be documented in the function comment.
- **Fingerprint scope:** Currently per-project. Should it also key on `scope`? Decision: no for v1 — keep dedup at project granularity. Revisit if users report wanting same content in multiple scopes under one project.
