# Feature: Harness instructions via munin MCP

## 1. Introduction / Overview

Today users manually type "update munin" at the end of useful sessions to persist
decisions, feedback, and context. This works in Claude Code but is fragile
(relies on user memory), coupled to one harness, and would drift if hardcoded
per harness.

This Feature makes munin itself the source of truth for lifecycle instructions.
The MCP server exposes prompts (`session_end_summary`, `session_start_context`)
that any harness can fetch via a generic "get prompt, run it" hook. The harness
no longer knows *what* to do at session end — it just asks munin.

The Feature also lays the foundation for future compaction by tracking recall
hits on each thought.

Source proposal: `docs/proposals/harness-instructions-via-mcp.html`.

## 2. Goals

- End-of-session summarization happens automatically without user intervention.
- Prompt definitions live in munin, versioned in-repo, editable as markdown.
- Session-start surfaces relevant prior thoughts before the agent begins work.
- Recall activity is tracked per thought so future compaction has data.
- Harnesses without native MCP-prompt hooks can fall back to a CLI shim.
- Claude Code running in this environment uses the new flow out of the box.

## 3. User Stories

### US-001: Fetch session-end instructions from munin
**Description:** As an agent harness, I want to fetch a `session_end_summary`
prompt from munin so that end-of-session behavior is defined once, centrally,
and stays consistent across tools.

**Acceptance Criteria:**
- [ ] An MCP client can call `getPrompt("session_end_summary")` against the
  munin server and receive a non-empty instruction string.
- [ ] The returned prompt instructs the model to emit 0–3 thoughts, includes
  the "what NOT to save" filter, and references `remember` as the tool to call.
- [ ] Editing the underlying markdown source file changes the next prompt
  response without a code rebuild.

### US-002: Fetch session-start context instructions
**Description:** As an agent harness, I want to fetch a `session_start_context`
prompt so that at session open the model is instructed to recall relevant prior
thoughts for the current project.

**Acceptance Criteria:**
- [ ] `getPrompt("session_start_context")` returns an instruction telling the
  model to call `recall` with project-appropriate queries (it suggests queries
  rather than auto-firing them).
- [ ] When invoked at session start in Claude Code, the agent produces recall
  calls scoped to the current project.

### US-003: Track recall hits on thoughts
**Description:** As a munin operator, I want every `recall` result to bump a
hit counter on the returned thoughts so that later compaction can flag cold
records.

**Acceptance Criteria:**
- [ ] Calling `recall` increments `hit_count` and updates `last_hit_at` for
  each returned thought.
- [ ] A newly created thought has `hit_count = 0` and `last_hit_at = NULL`.
- [ ] The `superseded_by` column exists on `thoughts` (nullable FK self-ref),
  unused by write paths in this feature but available for future compaction.

### US-004: CLI shim fallback for harnesses without MCP-prompt hooks
**Description:** As a harness integrator, I want a `munin hook <event>` CLI
command that prints the same instruction text as the MCP prompt so that any
harness with shell-command hooks can participate.

**Acceptance Criteria:**
- [ ] `munin hook session-end` prints the `session_end_summary` text to stdout
  and exits 0.
- [ ] `munin hook session-start` prints the `session_start_context` text to
  stdout and exits 0.
- [ ] An unknown event name exits non-zero with a usage message listing valid
  events.

### US-005: Wire Claude Code to use munin prompts
**Description:** As the Claude Code user on this machine, I want my settings
updated so that `SessionStart` and `SessionEnd` hooks fetch munin's
instructions automatically.

**Acceptance Criteria:**
- [ ] Starting a new Claude Code session in any project causes a `recall`
  suggestion flow scoped to that project.
- [ ] Ending a Claude Code session triggers review against munin's filter and
  produces 0–3 `remember` calls (zero when nothing qualifies).
- [ ] If `mcp_prompt` hook type is unavailable in Claude Code, the shell-out
  fallback using `munin hook <event>` produces the same behavior.

## 4. Functional Requirements

- FR-1: munin MCP server registers prompts named `session_end_summary` and
  `session_start_context` via FastMCP's prompt primitive.
- FR-2: Prompt bodies are loaded from markdown files under
  `src/munin/mcp/prompts/` at request time (editable without redeploy).
- FR-3: `session_end_summary` text enforces: cap of 3 thoughts per session,
  explicit "what NOT to save" filter (code patterns, file paths, git history,
  debugging solutions, ephemeral task state), allowed categories (user facts,
  feedback rules, project state with *why*, references), and the rule that
  zero thoughts is a valid outcome.
- FR-4: `session_start_context` suggests recall queries based on project and
  cwd but does not auto-execute them.
- FR-5: SQL migration adds `hit_count INTEGER NOT NULL DEFAULT 0`,
  `last_hit_at TIMESTAMPTZ NULL`, and `superseded_by UUID NULL REFERENCES
  thoughts(id)` to the `thoughts` table, numbered in order after the current
  latest migration.
- FR-6: The `recall` code path performs a single `UPDATE ... SET hit_count =
  hit_count + 1, last_hit_at = now()` over the returned thought IDs after the
  select, in the same transaction.
- FR-7: `munin hook <event>` CLI subcommand reads prompt markdown directly
  (not via the MCP server) so it works even if the server is down.
- FR-8: Claude Code settings at `~/.claude/settings.json` are updated to fire
  `SessionStart` and `SessionEnd` hooks that resolve to the munin prompts,
  using `mcp_prompt` hook type if supported, else `command` with the CLI shim.

## 5. Non-Goals (Out of Scope)

- Milestone hooks (`PostCommit`, `PostPRMerged`) — defer until session length
  becomes a problem.
- Server-side scheduled compaction, near-duplicate clustering, and automatic
  thought deletion — foundation only, no runtime.
- Auto-firing recall queries at session start (suggestions only, model
  decides).
- Wiring opencode, Cursor, or Codex harnesses — follow-up feature. The CLI
  shim is built so those integrations are straightforward later.
- Versioned prompt protocol / client capability negotiation — single version
  shipped inline.
- Any UI for viewing or editing prompts.

## 6. Technical Considerations

- FastMCP's `@mcp.prompt()` decorator is the expected integration point. If it
  doesn't support returning a plain string, a minimal adapter is fine.
- Markdown files live next to the Python code (`src/munin/mcp/prompts/`) and
  are packaged with the wheel.
- Migration follows the `sql/NNN_name.sql` convention from `CLAUDE.md`.
- `hit_count` update must not add user-visible latency to `recall` — single
  batched `UPDATE` keyed on the returned IDs is sufficient.
- Claude Code hook type `mcp_prompt` may not exist yet. If absent, implement
  the `command` fallback and note the upgrade path.
- The CLI shim reads markdown from the installed package to stay independent
  of the running MCP server.

## 7. Success Metrics

- Zero manual "update munin" invocations needed in a one-week evaluation
  window across typical sessions.
- munin thought growth stays within curated range (≤3 thoughts per session
  average) after automation.
- `recall` p50 latency unchanged within noise after hit-count tracking lands.
- At least one full round-trip (SessionStart recall suggestion → ad-hoc
  remembers → SessionEnd summary → thoughts persisted) observed working end to
  end in Claude Code on this machine.

## 8. Open Questions

- Does the installed Claude Code version support an `mcp_prompt` hook type
  natively? If not, confirm `command`-style fallback works for `SessionStart`
  and `SessionEnd` specifically.
- Should `session_start_context` inspect git root / project name to tailor
  suggested queries, or pass the responsibility to the model? Current plan:
  model handles, prompt hints at what signals to use.
- Where should the `munin hook` command write telemetry (if any) for the
  evaluation week? Current plan: none beyond existing log file.

## 9. Related Context

- Proposal HTML: `docs/proposals/harness-instructions-via-mcp.html`
- Proposal markdown: `docs/proposals/harness-instructions-via-mcp.md`
- Follow-up: opencode harness wiring (same CLI shim, different hook config).
