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
- 2026-06-16: playbook created, decisions locked, deps pre-staged, baseline pending.
