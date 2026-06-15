# munin Improvement Decision Matrix

*A decision-oriented synthesis of munin's current retrieval/memory architecture against proven 2024–2026 agent-memory and vector-search techniques. Scoped to munin's design constraints: local-only, Postgres 16 + pgvector 0.8.2, nomic-embed-text-v1.5 (768-dim, llama.cpp), single `thoughts` table, Python core, MCP + CLI surface, ~5k thoughts across ~29 projects.*

---

## 1. Current State

munin today is a clean, deliberately "dumb DB" memory store. It works, but its retrieval and lifecycle are the simplest possible implementations, and several columns exist as scaffolding for features that were never built.

- **Retrieval is pure single-stage cosine similarity.** `match_thoughts` orders strictly by `embedding <=> query` and nothing else — no hybrid/lexical channel, no reranker, no recency, no importance, no diversity (`sql/003_match_thoughts.sql:39-40`). Exact-token queries (error codes, symbol names, file paths) that the embedding blurs have no lexical fallback to catch them.
- **No lifecycle dynamics whatsoever.** No consolidation, summarization, decay, TTL, or compaction. The store only grows. `hit_count` / `last_hit_at` are written on every recall but **never read back into ranking or eviction** (`memory.py:100-109`). `superseded_by` and `idx_thoughts_cold` exist purely as unused scaffolding "for future compaction" (`sql/005_hit_tracking.sql`).
- **Dedup is exact-match only.** `md5(content)` uniqueness per project (`sql/002`, `sql/004`). Any whitespace or wording difference produces a brand-new row, so paraphrased/near-duplicate thoughts accumulate forever and crowd top-k.
- **No conflict/contradiction handling.** Two thoughts saying opposite things both surface in recall ranked purely on similarity, with no supersession signal — even though `superseded_by` is sitting unused in the schema.
- **Writes are 100% manual.** Thoughts are created only by explicit `remember()` calls (or batch import/ingest). No hook, no transcript scraper. `session_end.md` is advisory only; if the agent skips `remember`, nothing persists.
- **Filtering is project-equality + optional scope-equality.** No tag filtering in recall, no cross-project pool, no hierarchical scope. The project filter is applied as a post/pre-filter against a single shared HNSW index — the classic "metadata filter + ANN" recall-degradation pattern as the table grows.
- **Embedding path is sync, one-chunk-at-a-time, uncached.** `embed_batch()` exists but has no production callers; ingest fires one HTTP POST per chunk and opens a fresh `httpx.Client` per call. Worse, the unchanged-content fingerprint check runs **after** the embed call (`ingest.py:171` vs `:210-216`), so re-ingesting an unchanged repo still embeds every chunk.
- **Storage is unoptimized but fine at scale.** Full `vector(768)` float32, HNSW at pgvector defaults (m=16, ef_construction=64), `ef_search` never tuned. At ~5k rows the entire index fits in RAM many times over; embedding latency dominates, not vector search.

The headline: munin is a solid storage substrate with **retrieval quality and memory lifecycle as the two real gaps.** Most pure-performance levers (quantization, DiskANN, partitioning) are irrelevant at current scale and only matter at 100k–1M+ rows.

---

## 2. Improvement Matrix

Effort: **S** = hours, **M** = days, **L** = weeks / new dependency. "Impact at munin scale (~5k rows)" is called out explicitly where a technique only pays off at scale.

| Improvement | Category | What it does | Impact on Speed | Impact on Quality | Effort | Risk | Proven by |
|---|---|---|---|---|---|---|---|
| **Hybrid search: dense + tsvector BM25 fused with RRF** | Retrieval | Adds a lexical leg (`to_tsvector` GIN) alongside cosine, fuses ranked lists with Reciprocal Rank Fusion (k≈60) in one CTE-based query | Neutral (one extra in-DB CTE; sub-ms at 5k) | **High** — catches exact identifiers/error codes/paths dense embeddings blur | S–M | Low — in-DB, no new service; tsvector write overhead trivial | Anthropic Contextual Retrieval (BM25 fusion: top-20 failure 5.7%→4.9%); OpenSearch/Superlinked RRF NDCG gains |
| **Cross-encoder reranking (bge-reranker-v2-m3, local)** | Retrieval | Re-scores top-50 candidates as (query, doc) pairs through a local cross-encoder, keeps top-8 | Slower (tens–low-hundreds ms CPU for 50 pairs); bounded, only on recall | **Highest single quality lever** — +5 to +15 NDCG@10; ~34% further failure reduction on top of hybrid | M | Medium — adds a local model dependency; feature-flag it | Anthropic (failure 2.9%→1.9% with rerank); MTEB/BEIR cross-encoder deltas |
| **Multi-signal ranking: relevance × recency × importance × hit_count** | Retrieval / Memory-dynamics | Replaces pure cosine ORDER BY with weighted score; reuses already-stored `created_at`, `last_hit_at`, `hit_count` | Neutral (SQL math over candidate set) | **High** — surfaces fresh + historically-useful thoughts over stale exact matches | S | Low — weight tuning only; all signals already collected | Generative Agents (Park 2023); reused in MemGPT/Mem0/LangGraph |
| **MMR diversity re-ranking** | Retrieval | Greedy post-rank that penalizes near-duplicate results to broaden top-k coverage | Negligible (pure Python over fetched candidates) | Medium — directly fixes munin's near-duplicate-crowding failure mode | S | Low — trades a little raw relevance for coverage | Carbonell & Goldstein; built into LangChain/LlamaIndex/OpenSearch |
| **hnsw.ef_search tuning (100–200) via SET LOCAL** | Storage-perf | Raises query-time candidate list so selective project filters don't starve results | Negligible at 5k (single-digit ms) | Medium — restores recall on filtered queries | S | Low — reversible per-query | pgvector README (the first HNSW tuning step) |
| **Iterative index scans (`hnsw.iterative_scan`, pgvector 0.8)** | Storage-perf | Auto-rescans more of the index when the project/scope filter discards most hits — the idiomatic 0.8 fix for filter+ANN | Slightly higher latency when filter is selective; negligible at 5k | Medium–High as table grows; the correct answer to munin's filtering problem | S | Low — `strict_order` is safe; already on 0.8.2 | pgvector 0.8.0 README (Iterative Index Scans) |
| **Semantic near-duplicate detection + merge on write** | Memory-dynamics | After embedding a new thought, ANN-check in-project; if top hit ≥~0.95, NOOP/merge instead of insert | One extra ANN query per write | Medium–High — stops duplicate accumulation, improves recall precision | S–M | Medium — false-merge risk; mitigate with conservative threshold | Industry-standard hygiene; cheap subset of Mem0 update stage |
| **Supersession / conflict handling (wire up `superseded_by`)** | Memory-dynamics | On conflicting near-duplicate, set `old.superseded_by = new.id`; filter `superseded_by IS NULL` in recall | Neutral | High — kills "recall returns two contradictory thoughts" | S (heuristic) / M (LLM-judged) | Medium — wrongly retiring a correct older fact | Mem0 DELETE/UPDATE ops; Zep edge invalidation |
| **Bi-temporal validity (`valid_from`/`valid_to`)** | Memory-dynamics | Non-destructive supersession with queryable history; recall filters `valid_to IS NULL` by default | Neutral | Medium — captures ~80% of the staleness win without a graph DB | S–M | Low — additive columns + WHERE clause | Zep/Graphiti bitemporal model (arXiv 2501.13956) |
| **Decay / forgetting job (Ebbinghaus, recency+frequency)** | Memory-dynamics | Scheduled batch soft-deletes/archives cold low-score rows; refresh-on-retrieval already half-built | Neutral (offline) | Medium — bounds growth, keeps recall fresh | M | Medium — over-aggressive prune deletes rare-but-valuable knowledge; soft-delete + dry-run first | MemoryBank, FadeMem (2601.18642); munin already has `idx_thoughts_cold` |
| **Deterministic contextual embedding (prepend project/scope/tags/heading)** | Embedding | Prepend existing metadata to content before embedding so the vector carries section/source context (heading is currently stored but NOT embedded) | Neutral | Medium — recovers most of contextual-retrieval's gain at zero LLM cost | S | Low — store raw content separately from embedded text | Anthropic Contextual Retrieval (cheap deterministic variant) |
| **LLM-generated contextual retrieval (bulk ingest only)** | Embedding | LLM writes a 50–100 token situating blurb per chunk before embedding | Slower write (1 LLM call/chunk, prompt-cache the parent doc) | High for bulk-ingested long sources; weak for short self-contained thoughts | M–L | Medium — write-time cost/latency; needs generative model | Anthropic (failure 5.7%→3.7% embeddings-only, →2.9% +BM25) |
| **Embedding upgrade: EmbeddingGemma-300M (same 768 dim)** | Embedding | Drop-in better embedder, **no schema change** (768 native, mean pooling like nomic) | Modestly slower CPU embed (300M vs 137M) | High — clearly above nomic-v1.5 on MTEB at identical dim | M | Low–Medium — Gemma license; one reindex for consistency | Google EmbeddingGemma (#1 MTEB <500M, Sep 2025) |
| **Embedding upgrade: Qwen3-Embedding-0.6B (best quality)** | Embedding | SoTA-per-size; ~+8 MTEB pts over nomic, strong code retrieval | Slower CPU embed (0.6B); Matryoshka-truncatable | **Highest embedding quality jump** | M | Medium — needs last-token pooling, EOS append, manual L2 norm, dim change → reindex | Qwen3-Embedding (arXiv 2506.05176); MTEB EN 0.6B=70.70 vs nomic ~62 |
| **Embedding upgrade: gte-modernbert-base (code-heavy)** | Embedding | 149M, 768 dim, 8K ctx, best code-retrieval-per-byte | Same class as nomic (no slowdown) | High specifically on code recall (CoIR 79.31) | M | Low–Medium — verify ModernBERT support in pinned llama.cpp image | Alibaba gte-modernbert-base |
| **halfvec (fp16) storage** | Storage-perf | Halves vector + index size, negligible recall loss | Faster graph traversal at scale | **None at 5k** (~7 MB saved); first lever only at 100k+ rows | S | Low — column/expression-index migration | pgvector 0.7 README; Neon "use halfvec" |
| **Batched + cached + connection-reused ingest embedding** | Embedding / Storage-perf | Wire up the dead `embed_batch()`, reuse the httpx client, move fingerprint skip **before** embed | Large ingest speedup (N POSTs → N/32; skip no-op re-embeds) | Neutral (quality unchanged) | S–M | Low — internal refactor | munin source: `embed_batch` unused, fingerprint-after-embed bug |
| **Reflection / consolidation (offline per-project summaries)** | Memory-dynamics | Cluster a project's thoughts over HNSW, LLM-synthesize a `scope='reflection'` summary, link sources | Neutral (offline batch) | Medium–High — fewer fragments, lower recall token cost | M–L | Medium — lossy/hallucinated summaries; keep raw rows | Generative Agents reflection; Zep/Graphiti community summaries |
| **Write-time LLM extract-and-update (ADD/UPDATE/DELETE/NOOP)** | Architecture / Memory-dynamics | Mem0-style: LLM extracts atomic facts and self-consolidates against existing memories on write | Slower write (LLM round-trip; deferrable/async) | **Highest end-to-end lifecycle win** — +26% accuracy, −90% tokens vs full-context | L | High — new generative-model dependency, non-determinism on write path; opt-in flag | Mem0 (arXiv 2504.19413); LOCOMO 92.5%, LongMemEval 94.4% |
| **Temporal knowledge graph (Graphiti/Zep-style)** | Architecture | Entity/relation graph with bitemporal edge validity | N/A | High for multi-hop/temporal reasoning | L | High — contradicts "dumb DB, single table"; needs graph store | Zep (arXiv 2501.13956); steal bitemporal *primitive*, not the graph |
| **pgvectorscale DiskANN / labeled filtering / partitioning** | Storage-perf | Disk-based ANN + in-index filtered search at huge scale | Dramatic at 10M–50M vectors | **None at 5k** — pure over-engineering now | L | High — Rust extension, Intel-macOS build gap, DDL per project | Timescale benchmark (50M vectors); Microsoft Filtered-DiskANN |

---

## 3. Quick Wins vs Bigger Bets

### (a) Quick wins — high impact, low effort, no new dependency
All of these are in-DB or pure-Python, fully local, and individually shippable:

- **Hybrid search + RRF in one SQL function** — the single best first move. munin's content is code/decision text full of exact identifiers; the lexical leg directly fixes the biggest blind spot. tsvector generated column + GIN index in a new `sql/006`, rewrite `match_thoughts` as two CTEs joined by RRF on `id`. *(Anthropic Contextual Retrieval.)*
- **Multi-signal ranking** — reuse `hit_count`/`last_hit_at`/`created_at` that are already written but ignored. One SQL ORDER BY change converts dead telemetry into ranking signal. *(Generative Agents.)*
- **`ef_search` tuning + iterative index scans** — two `SET LOCAL` lines in `match_thoughts` future-proof recall against project-filter starvation as the corpus grows. Idiomatic pgvector 0.8.2 (already installed).
- **MMR diversity pass** — pure Python over fetched candidates; directly attacks the near-duplicate-crowding that plagues recall today.
- **Local cross-encoder reranker (bge-reranker-v2-m3)** — slightly more effort (a sidecar model) but the highest single quality lever; mirrors the existing self-hosted llama.cpp pattern. Feature-flag in `config.toml`.
- **Ingest embedding fixes** — wire up `embed_batch()`, reuse the client, and move the fingerprint skip before embed. Pure performance/correctness cleanup with no quality risk.

### (b) Medium bets — meaningful change, moderate effort
- **Semantic near-duplicate dedup + supersession** — the LLM-free subset of Mem0. After embedding, ANN-check in-project; merge above ~0.95, set `superseded_by` on conflict, exclude superseded rows from recall. Wires up the unused `005` scaffolding and kills the duplicate/contradiction problems already logged as tech debt. *(Mem0 update stage; Dataquest thresholds.)*
- **Deterministic contextual embedding** — prepend project/scope/tags/heading to content before embedding (heading is stored but currently NOT embedded). Most of contextual-retrieval's gain at zero LLM cost.
- **Embedding model upgrade** — EmbeddingGemma-300M is the lowest-friction win (same 768 dim, same pooling, no schema change). Qwen3-0.6B is the bigger quality jump but forces pooling/EOS/normalize changes + a reindex. gte-modernbert if code recall is the priority. *(All MTEB-validated, all GGUF-ready.)*
- **Bi-temporal validity columns** — `valid_from`/`valid_to` capture ~80% of Zep's staleness win without a graph engine.

### (c) Big bets — architectural, new dependency, high risk
- **Write-time LLM extract-and-update (Mem0)** — the biggest behavioural change and the biggest payoff (+26% accuracy, −90% tokens), but introduces a generative-model dependency munin doesn't have and non-determinism on the write path. Make it opt-in (`extract=true`).
- **Offline reflection/consolidation** — periodic per-project summarization. Read-only against current schema but needs a generative model and a schedule; natural second phase after dedup lands.
- **Temporal knowledge graph (Graphiti/Zep)** — explicitly *not recommended as architecture* (contradicts "dumb DB, single table"). Borrow the bitemporal *primitive* only.
- **pgvectorscale / DiskANN / partitioning** — do not adopt. Zero benefit at 5k rows; revisit only at 1M+ thoughts.

---

## 4. Recommended Sequencing

Optimizing for the owner's stated goals (speed + efficiency + quality) and respecting the "dumb DB, single table, local-only" design.

### Phase 1 — Retrieval quality, zero new dependencies (ship first)
*Goal: make recall measurably better with in-DB / pure-Python changes only.*
1. **Hybrid search + RRF** (`sql/006` tsvector + GIN, rewrite `match_thoughts`).
2. **Multi-signal ranking** folded into the same function (activate `hit_count`/recency).
3. **`ef_search` + iterative scans** in the same migration (recall safety net).
4. **MMR diversity** in `core/` over the hybrid candidate set.
5. **Ingest embedding fixes** (batch, client reuse, fingerprint-before-embed) — independent, parallelizable.

Rationale: highest quality-per-effort, fully reversible, no model or infra changes, and they compose (hybrid → rerank-ready candidate set → MMR). Establishes a baseline to measure everything else against using munin's own recall-hit data.

### Phase 2 — Lifecycle + selective heavier levers
*Goal: stop the store rotting; add the one hardware-class quality lever worth it.*
1. **Semantic dedup + supersession** (wire up `superseded_by`, exclude superseded from recall). Heuristic first, no LLM.
2. **Bi-temporal columns** + recall default `valid_to IS NULL`.
3. **Local cross-encoder reranker**, feature-flagged — slot it between the hybrid CTE and MMR.
4. **Deterministic contextual embedding** — *note the dependency:* this changes embedded text and therefore **requires a reindex**; batch it together with any embedding-model decision.

Rationale: dedup/supersession attack the logged tech-debt directly and are mostly schema-already-present. The reranker is the biggest remaining quality lever once recall is good. Group anything that touches embedded text so you pay the reindex cost once.

### Phase 3 — Model + generative lifecycle (decide deliberately)
1. **Embedding model upgrade** — pick one: EmbeddingGemma (no reindex needed for dim, but reindex for consistency) vs Qwen3-0.6B / gte-modernbert (dim/pooling change → mandatory reindex). **This is the forcing function for a full reindex**; sequence Phase-2 contextual-embedding to land in the same reindex.
2. **Write-time extraction (Mem0-style)** and **offline reflection/consolidation** — both need a generative model. Adopt only if you're willing to add a local chat model (Ollama/llama.cpp) alongside the embed server. Keep extraction opt-in to preserve cheap manual writes.

**Explicitly deferred indefinitely:** halfvec/binary quantization, pgvectorscale/DiskANN, partitioning, partial-per-project indexes — all scale-only, all over-engineering at 5k rows. Revisit at 100k+ (halfvec/ef tuning) or 1M+ (DiskANN).

**Dependency summary:**
- Embedding-model change **and** deterministic/LLM contextual embedding → both invalidate existing vectors → **batch into one reindex** (minutes at 5k rows; use `maintenance_work_mem` + parallel workers for the one-off build).
- Reranker, extraction, reflection → all require a **generative model dependency** munin currently lacks → one decision gates all three.
- Dedup/supersession/bitemporal/multi-signal ranking → **no reindex, no new dependency** → safe early.

---

## 5. Open Questions for the Owner

1. **Reindex tolerance.** A model upgrade (or any change to embedded text, e.g. contextual prepending) means re-embedding all ~5k thoughts. It's cheap today (minutes), but do you want to commit to it now, or stay on nomic-v1.5 and capture quality purely through hybrid + rerank (no reindex)? If reindexing, batch the model change and contextual-embedding change together.

2. **Generative-model dependency.** Reranker (optional model), write-time extraction (Mem0), and reflection/consolidation all require running an LLM alongside the embed server. Are you willing to add a local chat model (Ollama / llama.cpp) to the podman stack, or must munin stay embeddings-only? This single decision gates the three biggest lifecycle features.

3. **Auto-capture vs manual writes.** Writes are 100% manual today and `session_end.md` is advisory. Do you want munin to stay a passive store (agent decides what to remember), or move toward extraction-on-write / hooked capture — accepting non-determinism and cost on the write path?

4. **Forgetting policy.** Are you comfortable with a decay/compaction job that soft-deletes cold, low-score thoughts? Where's the line between "bounded, fresh store" and "never lose a rare-but-valuable decision"? (Recommend soft-delete + dry-run before any hard prune.)

5. **Stay flat, or allow graph-shaped memory?** The bitemporal *primitive* (valid_from/valid_to) fits the single table cleanly. A full Graphiti/Zep entity graph does not and contradicts the "dumb DB" principle. Confirm we steal the temporal idea but **never** add a graph store.

6. **Cross-project recall.** Today the project filter is hard equality — a query can't span related projects or a global pool. Is single-project scoping a deliberate product stance, or a limitation worth relaxing (e.g. an opt-in multi-project / global recall mode)?
