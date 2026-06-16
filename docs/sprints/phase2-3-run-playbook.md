# Phase 2+3 Autonomous Run — Control Playbook

Single source of truth for the unattended Phase 2+3 run. If context is summarized or the
session restarts mid-run, RE-READ this file + `sprint.json` + munin recall before continuing.
Owner approved this flow on 2026-06-16 and went offline (gym). Drive it to completion.

## Owner decisions (locked)
- **Scope:** Phase 2 + Phase 3, end to end (7 stories P2-1..P3-3 in `sprint.json`).
- **Merge:** AUTO-MERGE `takt/munin-phase2-3` → `main` when ALL final gates pass. Hard gate:
  merge only if (final adversarial review clean) AND (full pytest green) AND (eval no-regression).
  Any red → STOP, write `debug-active.md`, do NOT merge.
- **Eval gate:** YES. Build a curated query→expected-thought eval set (~15-20 pairs from real
  munin content). Capture BASELINE now on the current nomic system BEFORE any code change.
  Gate Phase 3 on no-regression + measurable lift after reindex.
- **Lifecycle flags:** dedup / supersession / bitemporal land default-ON; reranker degrades
  gracefully to hybrid-only if sidecar unreachable.

## Execution model
Orchestrator-driven (main model decomposes + gates; worker agents implement). Workers:
`builder` for stories, `heavy` for gnarly/cross-cutting, `skeptic` for adversarial gates,
`deep-review` skill for the review runs. All DB work against `munin_test` (prod guard live).

## Validate → Test → Verify (every story + every fix, in this order)
1. **Validate:** ruff + mypy clean (0 NET-NEW; pre-existing ~46 ruff + flaky
   `test_returns_none_when_no_git` are known debt, don't block). Migrations apply via
   `scripts/migrate.sh` (MUNIN_PG_DB=munin_test) with row-count preserved.
2. **Test:** full pytest against `munin_test`, real output captured. No green-claim without it.
3. **Verify (live runtime, not static):** stack `podman compose up -d` healthy; hit real
   endpoints (embed `/embedding` → assert 768-dim; reranker `/reranking` returns scores);
   recall sanity set returns expected thoughts. Static review never substitutes for this.

## Gate sequence
```
STEP 0  Eval baseline  → build eval set, score on CURRENT system (nomic+hybrid+MMR), save baseline.json
PHASE 2 build P2-1→P2-2→P2-3→P2-4 (each: validate→test→verify)
  REVIEW run  (1x, deep-review on Phase 2 diff)
  FIX run     (1x, fix findings → validate→test→verify)
PHASE 3 build P3-1→P3-2→P3-3 (each: validate→test→verify)
  NOTE: P3-2 (model swap) and P3-3 (reindex) run BACK-TO-BACK. Phase 3 is NOT done until
        reindex completes. Do NOT run recall-quality verify in the mixed-embedding window.
  REVIEW run  (1x, deep-review on Phase 3 diff)
  FIX run     (1x → validate→test→verify; if a fix touches embed/ingest/contextual surface,
              RE-BACKUP + RE-REINDEX before re-verify)
FINAL   adversarial review across Phase 1+2+3 (full branch vs main; weight new surface +
        integration seams; Phase 1 already passed deep-review, don't re-litigate).
        Up to 3 review→fix cycles. Each fix → validate→test→verify (+ re-reindex if embed
        surface touched). After cycle 3 if still red → STOP + breadcrumb, no merge.
MERGE   only if all gates green → merge to main. Then leave note: human must reload session
        so stdio munin-mcp picks up new memory.py.
```

## Hard safety rails
- Fresh `pg_dump -Fc` into `backups/` before EVERY prod-mutating step (reindex; any final-cycle
  fix that writes prod). Keep all dumps. Pre-run dump already at
  `backups/munin-pre-phase23-20260616-073311.dump` (prod=4884 rows).
- 🔴 Mixed-embedding: after P3-2, existing rows are nomic vectors, new are Gemma — same 768 dim,
  DIFFERENT space = silently wrong. P3-2→P3-3 atomic; no recall-quality claim until reindex done.
- New migrations start at `sql/010` (006-009 taken by Phase 1).
- Models already staged + load-validated (`models/embeddinggemma-300M-Q8_0.gguf`,
  `models/bge-reranker-v2-m3-Q8_0.gguf`) — do NOT re-download.
- NEVER point tests at prod `munin`. NEVER force-green. NEVER merge with red.
- Halt-don't-thrash: per-phase = 1 review + 1 fix; final = ≤3 cycles. Exhausted → STOP +
  `debug-active.md` with exact state (branch, last green commit, failing gate, findings).

## State log (append as the run progresses)
- 2026-06-16: playbook created, decisions locked, deps pre-staged.
- 2026-06-16: STEP 0 done. Eval harness at tests/eval/ (eval_set.json 20 pairs, score.py, baseline_nomic.json). BASELINE (nomic): recall@1=0.15 @5=0.45 @10=0.60 MRR=0.269. 8/20 total misses at k=10. Commit 4d7e092. Post-reindex re-score: `python tests/eval/score.py --out tests/eval/post_reindex_gemma.json`. Bar to beat: no regression + lift.
- 2026-06-16: Owner granted FULL autonomy mid-run (gym). Decisions delegated to orchestrator. Drive Phase 2+3, all gates, fixes, auto-merge to main, relaunch, leave summary+proof.
- 2026-06-16: P2-1 done. Commit 701f1d1. New config keys: remember_dedup_enabled (default True), remember_dedup_threshold (default 0.95). ANN dedup in remember() path via pgvector cosine. Live verify: cosine=0.9877 pair correctly skipped, row count stayed 1. pytest: 240 passed, 1 known-flaky (test_returns_none_when_no_git). ruff+mypy clean.
- 2026-06-16: P2-2 done. Commit 53b595b. New config keys: remember_supersede_enabled (default True), remember_supersede_threshold (default 0.80). Supersession logic in remember() path: similarity in [0.80, 0.95) → insert new, set old.superseded_by = new.id. sql/010_supersession.sql rewrites match_thoughts with WHERE superseded_by IS NULL on both dense and lexical legs + partial index idx_thoughts_not_superseded. Live verify: old.superseded_by set correctly, old excluded from recall, show(old_id) still returns row. pytest: 240 passed, 1 known-flaky. ruff+mypy clean. Dedup-vs-supersession boundary: dedup gate (>=0.95) runs first and returns early; supersession handles [0.80, 0.95) similarity band. P2-3 note: bitemporal migration sql/011 extends match_thoughts again — same DROP-first re-runnable pattern applies; also set valid_to when superseded_by is set.
- 2026-06-16: P2-3 done. Commit 1b2de17. New migration sql/011_bitemporal.sql: ADD COLUMN valid_from (NOT NULL DEFAULT now()), valid_to (nullable); rewrites match_thoughts with AND valid_to IS NULL on both legs. Supersession UPDATE now stamps valid_to=now() alongside superseded_by. recall() gains include_history param + recall_include_history config flag (default False); history path uses direct SELECT bypassing match_thoughts, returns all rows including expired, no hit_count bump. New config key: recall_include_history (env MUNIN_RECALL_INCLUDE_HISTORY). 256 tests pass; ruff+mypy clean; row count preserved (5/5 back-filled). P2-4 note: recall() signature now include_history=bool|None; match_thoughts returns same 10-col shape (id,content,project,scope,tags,metadata,similarity,created_at,updated_at,score) — reranker inserts between RRF fusion and MMR inside the normal (non-history) path.
- 2026-06-16: P2-4 done. Commit e01d37d. New service llama-rerank (port 8089, bge-reranker-v2-m3-Q8_0.gguf, --reranking). Endpoint: POST /reranking {"query":str,"documents":[str]} → {"results":[{"index":int,"relevance_score":float}]}. Rerank stage inserted between RRF fusion and MMR in recall(). New config keys: recall_rerank_enabled (default True — degrades safely), rerank_url (default http://localhost:8089), recall_rerank_top_n (default 50). Default ON rationale: sidecar unreachable → graceful degrade to hybrid-only with warning, never crashes. Live verify: correct thought lifted rank 3→2 vs hybrid-only; reranker scores confirmed (correct doc score +2.29 vs red-herrings at -8 to -11). pytest: 264 passed, 1 known-flaky (test_returns_none_when_no_git). ruff+mypy clean. Note for P2-review: test_hybrid_recall.py::test_hit_count_recent_thought_ranks_above_stale needed recall_rerank_enabled=False added to its config (same reason MMR was already disabled there — isolates the hybrid signal under test).
- 2026-06-16: P2-fix done. Commit a9a1884. Fixes: (1) ANN lifecycle filter (superseded_by IS NULL AND valid_to IS NULL); (2a/2b/2c) rerank degrade fence hardened — empty results, malformed JSON, missing keys, WriteTimeout all raise MuninRerankUnavailable; (3) supersession upper-bound unconditional (cosine < dedup_threshold always required); (4) upsert+UPDATE atomic in one transaction; (5) module-level httpx.Client with structured Timeout; (6) rerank_score on ThoughtResult, MMR uses it when present; (7) history path confirmed returns before rerank block (line 273-281); (8) 4 new unit tests for failure modes; (12) assert "valid_to" in update_sql; warn-once rate-limit. ruff+mypy clean. pytest: 268 passed, 1 known-flaky (test_returns_none_when_no_git). Live verify: (a) superseded rows excluded from ANN — chain intact; (b) reranker down → hybrid degrade, warn-once fires once only; (c) reranker ON → correct doc rank 0, rerank_score populated and flows into MMR.
- 2026-06-16: P3-1 done. Commit 4de5620. build_embed_text() added to embed.py — deterministic prefix format: "project: X\nscope: Y\ntags: a, b\nheading: Z\n\n<content>". Wired into ingest.py Pass 2 (embed_batch receives prefixed texts; raw chunk.content stored) and memory.py remember() (embed() receives prefixed text; raw content stored). heading param added to remember(); falls back to metadata['heading']. NO sql/012 column (prefix fully reconstructable from stored columns at P3-3 reindex). 11 new unit tests in test_embed_context.py. pytest: 240 passed, 1 known-flaky (test_returns_none_when_no_git). ruff+mypy clean. Live verify: embedder called with "project: scratch-p3-1\nscope: storage\ntags: db, vector\nheading: ## Storage\n\n<raw>"; stored content = raw (no prefix in DB). P3-3 reindex note: call build_embed_text(row.content, project=row.project, scope=row.scope, tags=row.tags, heading=row.metadata.get('heading')) for each row to reconstruct identical embed input.
- 2026-06-16: P3-2 done. Commit below. docker-compose.yml llama-embed now serves embeddinggemma-300M-Q8_0.gguf (--embedding --pooling mean --ctx-size 4096). ctx-size bumped from 2048 to 4096 (flag accepted) but model hard cap is 2048 tokens — handled by reindex truncation at 3900 chars. Probe: vector length=768, model=embeddinggemma-300M-Q8_0.gguf confirmed. ruff+mypy clean.
- 2026-06-16: P3-3 done. scripts/reindex.py: backup → load → build_embed_text → embed_batch (32/batch, truncate >3900 chars for 74 rows with dense technical content) → autocommit UPDATE per batch → rebuild idx_thoughts_embedding_hnsw. Result: 4885 rows before = 4885 after, 0 NULL embeddings, 489.7s. Backup: backups/munin-pre-reindex-munin-20260616-011413.dump (20,599,307 bytes). Two bugs hit and fixed: (a) psycopg3 outer-transaction rollback swallowed UPDATE — fixed with autocommit=True; (b) Gemma 2048-token hard limit hit at ctx-size batch regardless of flag — fixed with 3900-char truncation in script. Live recall sanity: munin/pgvector/dedup/gemma queries return coherent results, sim scores 0.3-0.6, zero mixed-space artifacts. Justify: ctx-size 4096 flag needed to avoid 500→400 on batch; truncation is embed-only (raw content unchanged). ruff+mypy clean.
- 2026-06-16: EVAL GATE PASS. nomic baseline → Gemma post: recall@1 0.15→0.50, @5 0.45→0.65, @10 0.60→0.70, MRR 0.269→0.570. NO regression, strong lift. tests/eval/post_reindex_gemma.json. CAVEAT/BUG: rerank sidecar TIMES OUT on /reranking (10s read timeout too tight for 50 docs on CPU) → degrades to hybrid, so post numbers are hybrid-only (rerank would likely lift further). FIX in P3-fix: raise rerank read timeout / lower top_n / warm-up. Prod count 4885 = legit (incl 2 of orchestrator's own session memories), no test pollution.
- 2026-06-16: P3-fix done. Fixes: (A1-A3) shared truncate_for_embed() in embed.py — MAX_EMBED_CONTENT_CHARS=2600 (CJK-safe: 2048t × 1.3 chars/t), content truncated BEFORE prefix assembled so prefix is never consumed; wired into build_embed_text() used by remember() + ingest.py + reindex.py. Old 3900-char manual truncation in reindex.py removed. (A4) docker-compose.yml --ctx-size 4096 → 2048 (matches model hard cap). (B5) COSINE ops confirmed: HNSW index uses vector_cosine_ops, match_thoughts uses <=> operator; measured L2 norm = 1.000000 on 3 Gemma embedding samples → unit-norm already, --normalize NOT needed, not added. (C6) recall_rerank_doc_chars=512 cap in memory.py recall block; config key + MUNIN_RECALL_RERANK_DOC_CHARS env. (C7) recall_rerank_top_n default 50→25. (C8) rerank.py read timeout 10s→30s. (D9) WARNING log added at reindex.py start. ruff+mypy clean. pytest: 291 passed, 1 known-flaky. RE-REINDEX: backup backups/munin-pre-reindex2-20260616-094210.dump (20,791,951 bytes). 4885 rows → 4885 rows, 0 NULLs, 451.4s. RE-SCORE: recall@1=0.55, @5=0.60, @10=0.70, MRR=0.587. Lift vs prior hybrid-only post (0.50/0.65/0.70/0.570): recall@1 +5%, MRR +1.7% — reranker now active. No regression vs baseline (0.15/0.45/0.60/0.269). Reranker latency: 25 docs×512chars=3.26s, 50 docs×512chars=6.52s — both under 30s timeout.
- 2026-06-16: final-fix-cycle1 done. Commit 36d47e6. All 17 items fixed: F4 MMR leapfrog (discard unreranked tail from MMR working set — mixed scales inverted reranker); F2 list_projects lifecycle filter; F6 supersession race guard (AND superseded_by IS NULL); B1 show() + Thought dataclass extended with valid_from/valid_to/superseded_by; MCP M1/M2/M3 (recall include_history, remember heading, fused_score+rerank_score in response); M4 CLI --heading + _extract_heading() + JSONL "heading" key; M5 compose depends_on postgres; S1/S2 rerank.py dead flag removed + atexit httpx cleanup; sql/008 DROP-first; eval score.py --model arg + _check_live_thoughts (superseded_by IS NULL only — valid_to missing on prod); F1 threshold empirical: Gemma+prefix dup range 0.91-0.94, distinct-same-prefix 0.59-0.79, gap=0.13 → KEEP 0.95/0.80; ABLATION rerank-OFF 0.55/0.65/0.80/0.605 vs rerank-ON 0.70/0.90/0.95/0.792 → KEEP enabled=True; post_final eval: recall@1=0.70 @5=0.90 @10=0.95 MRR=0.792 (baseline 0.15/0.45/0.60/0.269). pytest: 311 passed, 1 known-flaky. ruff+mypy clean.
- RUN PROGRESS: [x] P2-1 [x] P2-2 [x] P2-3 [x] P2-4 [x] P2-review [x] P2-fix [x] P3-1 [x] P3-2 [x] P3-3 [x] P3-review [x] P3-fix [x] final-adversarial-cycle1 [ ] merge [ ] relaunch [ ] re-score+proof
