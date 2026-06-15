# munin Improvement Plan

Execution plan derived from `munin-improvement-matrix.md`, scoped to two owner decisions:

- **Dependency footprint: reranker-only.** Add one cross-encoder sidecar (bge-reranker-v2-m3) alongside the llama.cpp embed server. No generative/chat model. → Mem0-style write-time extraction, offline reflection/consolidation, and LLM-generated contextual retrieval are **out of scope** (they require a chat LLM).
- **Reindex: OK to reindex once.** Re-embedding ~5k thoughts is minutes. Batch the embedding-model upgrade + deterministic contextual embedding into a single re-embed pass in Phase 3.

**Explicitly excluded (scale-only over-engineering at ~5k rows):** halfvec/binary quantization, pgvectorscale/DiskANN, partitioning, partial-per-project indexes. Revisit at 100k+ rows.

---

## Scope at a glance

| Phase | Theme | New deps | Reindex | Outcome |
|---|---|---|---|---|
| 1 | Retrieval quality + ingest fixes | none | no | Recall measurably better, bugs fixed |
| 2 | Lifecycle + reranker | bge-reranker sidecar | no | Store stops rotting, top quality lever in |
| 3 | Model upgrade + contextual embedding | none | **yes (one pass)** | Best embeddings, context-aware vectors |

---

## Phase 1 — Retrieval quality, zero new dependencies

*Goal: make recall measurably better with in-DB / pure-Python changes only. Fully reversible. Establishes a measurable baseline using munin's own recall-hit data.*

### P1-1 — Hybrid search (dense + tsvector BM25, fused with RRF)
- **What:** Add a lexical leg alongside cosine. Generated `tsvector` column + GIN index. Rewrite `match_thoughts` as two CTEs (dense top-N, lexical top-N) fused by Reciprocal Rank Fusion (k≈60) on `id`.
- **Why:** munin content is code/decisions/errors full of exact identifiers (symbol names, error codes, file paths) that dense embeddings blur. Single best first move.
- **Files:** new `sql/006_hybrid_search.sql`, rewrite `sql/003_match_thoughts.sql`, `core/memory.py`.
- **Effort:** S–M. **Risk:** Low (in-DB, no new service).
- **Validate:** recall queries with exact tokens (error code, function name) return the right thought above paraphrases.

### P1-2 — Multi-signal ranking (relevance × recency × hit_count)
- **What:** Replace pure-cosine `ORDER BY` with a weighted score reusing already-stored `created_at`, `last_hit_at`, `hit_count`. Tunable weights in `config.toml`.
- **Why:** Activates dead telemetry (currently written every recall, never read). Surfaces fresh + historically-useful thoughts over stale exact matches.
- **Files:** folded into the `match_thoughts` rewrite, `core/config.py`.
- **Effort:** S. **Risk:** Low (weight tuning only).
- **Validate:** a frequently-hit recent thought outranks an equally-similar stale one.

### P1-3 — ef_search tuning + iterative index scans
- **What:** `SET LOCAL hnsw.ef_search = 100..200` and `SET LOCAL hnsw.iterative_scan = strict_order` (or relaxed) in `match_thoughts`. pgvector 0.8.2 idiomatic fix for metadata-filter + ANN starvation.
- **Why:** Project/scope equality filter against a shared HNSW index can starve results as the corpus grows. Safety net.
- **Files:** `match_thoughts` migration.
- **Effort:** S. **Risk:** Low (per-query, reversible).
- **Validate:** selective project filter still returns full top-k.

### P1-4 — MMR diversity pass
- **What:** Greedy post-rank in `core/` over the fused candidate set that penalizes near-duplicate results to broaden top-k.
- **Why:** Directly attacks the near-duplicate crowding that plagues recall today (exact-match dedup lets paraphrases accumulate).
- **Files:** `core/memory.py` (new ranking helper).
- **Effort:** S. **Risk:** Low (trades a little raw relevance for coverage; λ tunable).
- **Validate:** top-k over a duplicate-heavy project shows distinct thoughts, not 5 paraphrases of one.

### P1-5 — Ingest embedding fixes (bug cleanup)
- **What:** (a) move the fingerprint/unchanged-content skip **before** the embed call (currently `ingest.py:171` runs after `:210-216`, so unchanged re-ingest re-embeds everything). (b) Wire up the unused `embed_batch()` so ingest sends batched POSTs instead of one-per-chunk. (c) Reuse a single `httpx.Client` instead of opening one per call.
- **Why:** Pure performance + correctness. Large ingest speedup, no quality change.
- **Files:** `core/ingest.py`, `core/embed.py`.
- **Effort:** S–M. **Risk:** Low (internal refactor).
- **Validate:** re-ingesting an unchanged repo makes ~0 embed calls; fresh ingest batches chunks.

---

## Phase 2 — Lifecycle + reranker

*Goal: stop the store rotting; add the single biggest quality lever within reranker-only scope.*

### P2-1 — Semantic near-duplicate detection + merge on write
- **What:** After embedding a new thought, ANN-check in-project; if top hit ≥ ~0.95 cosine, NOOP/merge instead of insert. Conservative threshold.
- **Why:** Stops paraphrased duplicates accumulating and crowding top-k. The LLM-free subset of Mem0's update stage.
- **Files:** `core/memory.py` (remember path).
- **Effort:** S–M. **Risk:** Medium (false-merge — mitigate with high threshold + log merges).
- **Validate:** re-remembering a reworded existing fact does not create a new row.

### P2-2 — Supersession / conflict handling (wire up `superseded_by`)
- **What:** On a conflicting near-duplicate, set `old.superseded_by = new.id`; filter `superseded_by IS NULL` in recall. Heuristic (similarity + recency), no LLM.
- **Why:** Kills "recall returns two contradictory thoughts." Wires up unused `005` scaffolding; clears logged tech debt.
- **Files:** `core/memory.py`, `sql/003_match_thoughts.sql` (WHERE clause).
- **Effort:** S (heuristic). **Risk:** Medium (wrongly retiring a correct older fact — soft only, reversible).
- **Validate:** updating a decision retires the old one from default recall but keeps it queryable.

### P2-3 — Bi-temporal validity (`valid_from` / `valid_to`)
- **What:** Additive columns for non-destructive supersession with queryable history; recall defaults to `valid_to IS NULL`.
- **Why:** Captures ~80% of Zep/Graphiti's staleness win without a graph store. Steal the temporal *primitive* only — no graph engine (per matrix recommendation).
- **Files:** new `sql/007_bitemporal.sql`, `core/memory.py`, `match_thoughts`.
- **Effort:** S–M. **Risk:** Low (additive columns + WHERE).
- **Validate:** superseded thought has `valid_to` set; default recall excludes it, history query includes it.

### P2-4 — Local cross-encoder reranker (bge-reranker-v2-m3)
- **What:** New sidecar service in the podman stack. Re-score top-50 hybrid candidates as (query, doc) pairs, keep top-8. Feature-flagged in `config.toml`.
- **Why:** Highest single quality lever once recall is good (~+5 to +15 NDCG@10). Mirrors the existing self-hosted llama.cpp pattern — still deterministic, no chat LLM.
- **Files:** `docker-compose.yml`/podman, `core/memory.py` (rerank stage between fusion and MMR), `core/config.py`.
- **Effort:** M. **Risk:** Medium (new local model; bounded latency, only on recall; flag off = no-op).
- **Validate:** with rerank on, top-1 precision improves on a held-out query set vs hybrid-only.

---

## Phase 3 — Embedding upgrade + contextual embedding (one reindex)

*Goal: best-quality vectors. Both items change embedded text → batch into a single re-embed pass (minutes at 5k rows; bump `maintenance_work_mem` + parallel workers for the one-off HNSW build).*

### P3-1 — Deterministic contextual embedding
- **What:** Prepend existing metadata (project / scope / tags / heading) to content **before** embedding, so the vector carries section/source context. Store raw content separately from embedded text. Heading is currently stored but NOT embedded.
- **Why:** Recovers most of Anthropic contextual-retrieval's gain at zero LLM cost (deterministic — fits reranker-only scope).
- **Files:** `core/ingest.py`, `core/embed.py`, possibly a `embedded_text` column.
- **Effort:** S. **Risk:** Low. **Depends on:** reindex (P3-3).

### P3-2 — Embedding model upgrade
- **What:** Swap nomic-embed-text-v1.5. **Recommended: EmbeddingGemma-300M** — 768-dim, mean pooling = near drop-in, no column migration, no pooling/normalize code changes. (Alternatives: Qwen3-Embedding-0.6B = bigger MTEB jump but needs last-token pooling + EOS + L2-norm + possible dim change; gte-modernbert if code recall is the priority — verify ModernBERT support in the pinned llama.cpp image.)
- **Why:** nomic-v1.5 is aging; EmbeddingGemma is clearly above it on MTEB at identical dim.
- **Files:** `docker-compose.yml` (model), `core/embed.py` (only if non-Gemma).
- **Effort:** M. **Risk:** Low–Medium (Gemma license check). **Depends on:** reindex (P3-3).

### P3-3 — One-pass reindex
- **What:** Re-embed all thoughts through the new model + contextual prepending, rebuild HNSW. One-off script.
- **Why:** Forcing function for P3-1 and P3-2; pay the cost once.
- **Files:** new `scripts/reindex.py` (or `munin reindex` CLI verb).
- **Effort:** S–M. **Risk:** Low (snapshot DB first; re-embed is deterministic and re-runnable).
- **Validate:** row count unchanged, all vectors non-null, recall sanity-checks pass post-reindex.

---

## Sequencing & dependencies

```
Phase 1  P1-1 ─ P1-2 ─ P1-3  (one match_thoughts rewrite + sql/006)
         P1-4  (depends on P1-1 candidate set)
         P1-5  (independent, parallelizable)

Phase 2  P2-1 ─ P2-2 ─ P2-3  (dedup → supersession → bitemporal, schema chain)
         P2-4  (reranker; slots between fusion and MMR — independent build)

Phase 3  P3-1 + P3-2 ──► P3-3  (both change embedded text → batch into one reindex)
```

- **No reindex, no new dep:** all of Phase 1, all of Phase 2 except the reranker sidecar.
- **One new dep:** P2-4 reranker sidecar (already approved).
- **One reindex:** Phase 3 only, batched.

## Open items still owner-gated
- Forgetting/decay job (soft-delete cold low-score rows) — **deferred, not in this plan.** Revisit after dedup+bitemporal land and we see real growth.
- Cross-project / global recall mode — **deferred, product decision.** Today project filter is hard equality.
