# Active Alerts

| Status | Alert | First Seen | Last Seen |
|--------|-------|------------|-----------|
| confirmed | psycopg_pool not pre-installed in dev env | 2026-04-12 | 2026-04-13 |
| confirmed | DB migrations not applied before worker verification | 2026-04-12 | 2026-04-13 |
| confirmed | ctx_batch_execute rejects JSON array args passed as strings | 2026-04-12 | 2026-04-13 |
| confirmed | Sprint stories pre-implemented before sprint runs | 2026-04-13 | 2026-04-17 |
| confirmed | Worker cannot write files outside project root | 2026-04-17 | 2026-04-17 |

---

## Retro: 2026-04-17 — takt/harness-instructions-via-mcp

### What Went Well
- 5/5 stories completed (passes=true), 0 blocked at the code level — fifth clean sprint in a row
- US-001 and US-002 shared a single implementation pass; linter built both prompts together, saving a full worker turn
- US-004 CLI shim was clean and minimal — reads markdown from installed package, no MCP server dependency; pattern is reusable for any future hook event
- US-003 (recall hit tracking) required a schema migration (hit_count, last_hit_at, superseded_by columns) and landed with zero follow-up fixes
- Worker caught its own displacement bug immediately (mypy name-defined on `all_passed`), self-corrected without human intervention

### What Didn't Go Well
- US-005 was structurally blocked: worker cannot write `~/.claude/settings.json` (outside project root); story passes=true only because it produced a complete patch and deferred execution to the session agent — acceptance criteria not auto-verified in-turn
- Session agent settings.json patch must be applied manually after takt completes — no automation path exists today; this is a recurring harness boundary
- US-002 found server already had partial implementation from a prior incomplete pass — work was duplicated/verified rather than net-new; not caught at sprint planning

### Patterns Observed
- Harness boundary (project-root file gate) is a recurring blocker pattern: US-005 hit it this sprint; will recur for any story that touches user-level config outside the repo
- Worker self-correction on tool errors (mypy catch, immediate fix) is reliable — no human intervention needed for simple displaced-line bugs
- Parallel story execution (US-001/US-002/US-003 same startTime) works when stories share a single implementation surface; dependency ordering in sprint.json correctly serialized US-004 and US-005

### Action Items
- [ ] [carried 5x] Add migration bootstrap script (`scripts/migrate.sh`) — Suggested story: Create scripts/migrate.sh that applies sql/NNN_*.sql in order against compose stack; wire into CI before integration tests
- [ ] [carried 5x] Document `pip install -e '.[dev]'` as required dev setup step — Suggested story: Add dev setup section to README (separate from user quickstart) covering psycopg, dev extras, and venv activation
- [ ] [carried 5x] Fix `ctx_batch_execute` arg format — Suggested story: Reproduce JSON array rejection, document correct invocation pattern, file upstream bug report if confirmed
- [ ] [carried 3x] Fix flaky `test_returns_none_when_no_git` in `tests/unit/test_recall.py` — Suggested story: isolate git-ancestor detection and mock it in the unit test
- [ ] [carried 3x] Fix `remember --json` null project — Suggested story: return resolved project name from `_remember` and thread through to CLI JSON output
- [ ] [carried 2x] Document `.mypy_cache` clear as required step after adding `[[tool.mypy.overrides]]` — add to Makefile `typecheck` target or contributor onboarding
- [ ] [carried 2x] Add sprint pre-implementation check — verify story not already implemented before queuing; Suggested story: add a `munin doctor --sprint` pre-flight that diffs sprint ACs against current codebase
- [ ] Resolve harness file-gate boundary for user-config stories — Suggested story: define a takt escape hatch (post-sprint hook or session-agent checklist) for stories that must write outside project root

### Metrics
- Stories completed: 5/5
- Stories blocked: 0 (1 deferred to session agent — US-005 settings.json patch)
- Total workbooks: 4 (US-003 had no workbook; completed in parallel pass)
- Story durations: small avg 251s (US-001 267s, US-002 267s, US-004 131s, US-005 339s); medium avg 267s (US-003 267s)
- Phase overhead: unavailable — retro start epoch not recorded
