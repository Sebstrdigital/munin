# Active Alerts

| Status | Alert | First Seen | Last Seen |
|--------|-------|------------|-----------|
| confirmed | psycopg_pool not pre-installed in dev env | 2026-04-12 | 2026-04-12 |
| confirmed | DB migrations not applied before worker verification | 2026-04-12 | 2026-04-12 |
| confirmed | ctx_batch_execute rejects JSON array args passed as strings | 2026-04-12 | 2026-04-12 |

---

## Retro: 2026-04-12 — takt/quality-of-life

### What Went Well
- 6/6 stories completed, 0 blocked — third clean sprint in a row
- Wave 1 (4 parallel workers all touching `main.py`) produced zero merge conflicts — re-read-before-edit pattern fully reliable under load again
- US-003 `doctor` used `ThreadPoolExecutor` with per-check timeouts and `executor.shutdown(wait=False)` — clean concurrent pattern without overengineering
- mypy --strict maintained across all 6 stories; US-002 surfaced and applied `[[tool.mypy.overrides]]` fix for `python-frontmatter` correctly
- US-005 caught and fixed Optional return type issue from `get_completion_class` in stride — no rework needed post-merge

### What Didn't Go Well
- US-002 hit stale `.mypy_cache` silently ignoring new pyproject.toml override — required manual `rm -rf .mypy_cache`; this footgun is not documented anywhere
- `test_returns_none_when_no_git` surfaced again in US-001 and US-004 workbooks as "pre-existing" — acknowledged but still not fixed (carried from sprint 2)
- Three `[carried 2x]` action items from sprint 2 went unaddressed for the third sprint — now escalated to confirmed alerts

### Patterns Observed
- Workers consistently document pre-existing issues in `knownIssues` but don't fix them — good hygiene, but nothing moves items to resolution without an explicit story
- Any new untyped third-party lib requires a pyproject.toml `[[tool.mypy.overrides]]` entry + `.mypy_cache` clear — this pattern is repeating and not yet in contributor docs
- Wave timestamps are shared across all stories in a wave — per-story granularity is lost within waves; all wave 1 stories show identical 410s duration

### Action Items
- [ ] [carried 3x] Add migration bootstrap script (`scripts/migrate.sh`) — Suggested story: Create scripts/migrate.sh that applies sql/NNN_*.sql in order against compose stack; wire into CI before integration tests
- [ ] [carried 3x] Document `pip install -e '.[dev]'` as required dev setup step — Suggested story: Add dev setup section to README (separate from user quickstart) covering psycopg, dev extras, and venv activation
- [ ] [carried 3x] Fix `ctx_batch_execute` arg format — Suggested story: Reproduce JSON array rejection, document correct invocation pattern, file upstream bug report if confirmed
- [ ] [carried 1x] Fix flaky `test_returns_none_when_no_git` in `tests/unit/test_recall.py` — Suggested story: isolate git-ancestor detection and mock it in the unit test
- [ ] [carried 1x] Fix `remember --json` null project — Suggested story: return resolved project name from `_remember` and thread through to CLI JSON output
- [ ] Document `.mypy_cache` clear as required step after adding `[[tool.mypy.overrides]]` — add to Makefile `typecheck` target or contributor onboarding

### Metrics
- Stories completed: 6/6
- Stories blocked: 0
- Total workbooks: 6
- Story durations: small avg 337s (n=2, fastest 263s US-006, slowest 410s US-005); medium avg 373s (n=4, fastest 263s US-002, slowest 410s US-001/003/004)
- Phase overhead: 664s (retro triggered ~11 min after last story)
