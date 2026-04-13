# Active Alerts

| Status | Alert | First Seen | Last Seen |
|--------|-------|------------|-----------|
| confirmed | psycopg_pool not pre-installed in dev env | 2026-04-12 | 2026-04-13 |
| confirmed | DB migrations not applied before worker verification | 2026-04-12 | 2026-04-13 |
| confirmed | ctx_batch_execute rejects JSON array args passed as strings | 2026-04-12 | 2026-04-13 |
| potential | Sprint stories pre-implemented before sprint runs | 2026-04-13 | 2026-04-13 |

---

## Retro: 2026-04-13 — takt/bulk-ingest

### What Went Well
- 4/4 stories completed, 0 blocked — fourth clean sprint in a row
- Sequential dependency chain (US-001→US-002→US-003→US-004) executed cleanly; US-003 correctly consumed US-001 and US-002 outputs without rework
- Consistent design: all new modules (manifest.py, chunker.py, ingest.py) reused existing patterns — `MuninError`, `MuninConfig` dataclass shape, logging from config.py
- Fingerprint dedup via `upsert_thought()` MD5 content_fingerprint worked out-of-the-box; US-003 needed zero schema changes
- US-004 was already fully implemented before sprint ran — verification passed without code changes (`src/munin/cli/main.py:384-416`, `src/munin/core/ingest.py:37-132`)

### What Didn't Go Well
- US-004 pre-implementation was not caught at sprint planning — sprint included a story that was already done; wasted a worker slot
- No blockers, but no action items from prior retros addressed — four consecutive sprints have not touched migration bootstrap, dev setup docs, or ctx_batch_execute format

### Patterns Observed
- Workers consistently reuse established module patterns without prompting — dataclass shape, error types, logging — strong codebase coherence
- Hierarchical project values (`dua-cs-agent/backend`) worked through the entire stack (manifest → ingest → upsert_thought) without special handling — good schema flexibility
- US-004 pre-implementation is first occurrence of a sprint story being superseded by prior work; worth watching for recurrence

### Action Items
- [ ] [carried 4x] Add migration bootstrap script (`scripts/migrate.sh`) — Suggested story: Create scripts/migrate.sh that applies sql/NNN_*.sql in order against compose stack; wire into CI before integration tests
- [ ] [carried 4x] Document `pip install -e '.[dev]'` as required dev setup step — Suggested story: Add dev setup section to README (separate from user quickstart) covering psycopg, dev extras, and venv activation
- [ ] [carried 4x] Fix `ctx_batch_execute` arg format — Suggested story: Reproduce JSON array rejection, document correct invocation pattern, file upstream bug report if confirmed
- [ ] [carried 2x] Fix flaky `test_returns_none_when_no_git` in `tests/unit/test_recall.py` — Suggested story: isolate git-ancestor detection and mock it in the unit test
- [ ] [carried 2x] Fix `remember --json` null project — Suggested story: return resolved project name from `_remember` and thread through to CLI JSON output
- [ ] [carried 1x] Document `.mypy_cache` clear as required step after adding `[[tool.mypy.overrides]]` — add to Makefile `typecheck` target or contributor onboarding
- [ ] Add sprint pre-implementation check — before story is queued, verify it is not already implemented; Suggested story: add a `munin doctor --sprint` pre-flight that diffs sprint ACs against current codebase

### Metrics
- Stories completed: 4/4
- Stories blocked: 0
- Total workbooks: 4
- Story durations: small avg 128s (US-001 130s, US-002 125s); medium avg 323s (US-003 510s, US-004 135s)
- Phase overhead: unavailable — retro start epoch not recorded
