# Harness instructions via munin MCP

**Status:** Proposal — carried over from OdelEnergy session 2026-04-17
**Goal:** Stop asking agents to manually "update munin". Have agents pull instructions *from* munin so the same behavior works across every harness (Claude Code, Cursor, Codex, custom agents).

---

## Problem

Today the user types "update munin" at end of useful sessions. Works, but:

- Relies on user remembering
- Coupled to Claude Code only
- Behavior duplicated if we write it as hardcoded hooks in each harness

Hardcoding a `SessionEnd` hook per harness works but creates drift. Instructions evolve — we want one source of truth.

## Proposed architecture

Expose prompts + resources from the munin MCP server. Any harness with a generic "fetch prompt, run it" hook can plug in.

### New MCP primitives on munin

| Primitive | Name | Purpose |
|---|---|---|
| Prompt | `session_end_summary` | Returns an LLM prompt that asks the model to review the session and emit 0–3 munin thoughts. Model then calls `munin remember` with each. |
| Prompt | `session_start_context` (optional) | Returns a prompt that instructs the model to call `recall` with relevant queries based on the current project + cwd before starting work. |
| Resource | `munin://instructions/session-end` | Plain-text fallback for harnesses that can't read MCP prompts. |
| Resource | `munin://instructions/cadence` | Defines when the harness should call each primitive (session-start, session-end, milestone). |

### Why prompts over tools

Tools = the agent decides when to call. Prompts = the harness (hook) injects a specific instruction block. We want the latter for lifecycle triggers — it's deterministic, not left to the model.

### Curation-preserving summary

The `session_end_summary` prompt must enforce the filter from `~/.claude/CLAUDE.md` "What NOT to save":

> Code patterns, file paths, git history, debugging solutions, ephemeral task state — DO NOT save. Only save: user facts, feedback rules, project state with *why*, references. Cap: 3 thoughts per session. If nothing qualifies, save zero.

That keeps munin curated even when automated.

---

## Lifecycle — when each call fires

```
┌──────────────────────────────────────────────────────────────────┐
│                       HARNESS LIFECYCLE                          │
└──────────────────────────────────────────────────────────────────┘

[SessionStart]
    │
    │ (harness hook)
    ▼
┌─────────────────────────────────┐
│ mcp.getPrompt(                  │      ──► returns instruction:
│   "session_start_context")      │          "call recall() with
│                                 │           these queries: …"
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│ mcp.callTool("recall",          │      ──► vector search, top-k
│   query="…", project=auto)      │          thoughts injected
└─────────────────────────────────┘
    │
    ▼
──────────── [Session active] ────────────
    │
    │ ad-hoc during work:
    ▼
┌─────────────────────────────────┐
│ mcp.callTool("recall", …)       │      ◄─ model decides
│ mcp.callTool("remember", …)     │      ◄─ model decides (user
│                                 │          said "remember X" OR
│                                 │          non-obvious decision)
└─────────────────────────────────┘
    │
    ▼
[Milestone events — optional]
    │
    │ (harness hook, e.g. PostCommit, PostPRMerged)
    ▼
┌─────────────────────────────────┐
│ mcp.getPrompt(                  │
│   "milestone_summary",          │      ──► 0–1 thought
│   event="pr_merged")            │
└─────────────────────────────────┘
    │
    ▼
[SessionEnd]
    │
    │ (harness hook)
    ▼
┌─────────────────────────────────┐
│ mcp.getPrompt(                  │      ──► instruction:
│   "session_end_summary")        │          "review transcript,
│                                 │           emit 0–3 thoughts"
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│ Model produces 0–3              │
│ mcp.callTool("remember", …)     │      ──► written to DB
└─────────────────────────────────┘
    │
    ▼
[Background — optional, cron-style]
    │
    ▼
┌─────────────────────────────────┐
│ munin compact / review          │      ──► merges or deprecates
│ (scheduled, server-side,        │          stale thoughts;
│  not harness-driven)            │          "supersedes" links
└─────────────────────────────────┘
```

### Call taxonomy (GET / UPDATE / POST mental model)

| Intent | MCP primitive | Example |
|---|---|---|
| GET context | `callTool("recall", …)` | "what do we know about OdelEnergy?" |
| GET instructions | `getPrompt(name)` | session-end behavior |
| POST thought | `callTool("remember", …)` | save decision |
| UPDATE / DELETE | `callTool("forget", id)` + re-remember, or server-side compact | supersede stale |
| INTROSPECT | `callTool("list_projects" / "stats")` | dashboards |

---

## Bloat concerns — revisited

Earlier Slack thread: should we auto-write everything to munin?

**No.** Munin value is *signal-to-noise on recall*. Auto-dumping defeats curation. Mitigations baked into this proposal:

1. `session_end_summary` caps at 3 thoughts, with explicit "what NOT to save" filter.
2. Harness invokes the prompt; model does the filtering. Same brain that writes is the brain that judges.
3. Server-side compaction stays manual/scheduled, not session-triggered. Only revisit if `recall` noise gets high.

Scheduled compaction (cron) can:
- Flag thoughts with `hit_count = 0` after N days → candidate for deletion
- Cluster near-duplicates (cosine similarity > 0.9 within same project) → merge with supersedes link
- Never delete automatically — always propose, let human approve

---

## Implementation sketch

### munin side (this repo)

1. Add `prompts/` module under `src/munin/mcp/`.
2. Register prompts with FastMCP:
   ```python
   @mcp.prompt()
   def session_end_summary() -> str:
       return Path("prompts/session_end.md").read_text()
   ```
3. Markdown files under `src/munin/mcp/prompts/` are the editable source of truth.
4. Add migration 00X for `hit_count`, `last_hit_at`, `superseded_by` columns on `thoughts` (compaction support).

### Claude Code side (one-time setup, then portable)

`~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionEnd": [{
      "matcher": "",
      "hooks": [{
        "type": "mcp_prompt",
        "server": "munin",
        "prompt": "session_end_summary"
      }]
    }],
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "mcp_prompt",
        "server": "munin",
        "prompt": "session_start_context"
      }]
    }]
  }
}
```

Note: `type: "mcp_prompt"` does not exist in Claude Code yet. If unavailable, fall back to `type: "command"` that shells out to a small script (`munin hook session-end`) which the munin CLI implements — reads the prompt from the MCP server and stdouts it back.

---

## Open questions

1. Does Claude Code support `mcp_prompt` as a hook type? If not, add a CLI shim `munin hook {event}` that fetches the prompt and prints it — harness just runs that.
2. Milestone triggers — useful or noise? Start without them, add only if sessions regularly go long enough to lose context before `SessionEnd`.
3. Should `session_start_context` auto-fire `recall` queries, or just suggest them and let the model decide? Suggest first — agent autonomy preserved, token cost controlled.
4. Versioning of prompts — ship them in the repo, tag migrations with version, let server pick based on client capability.

---

## Next steps (when we resume in munin)

1. Spike `session_end_summary` prompt as a markdown file + FastMCP wiring. Test from Claude Code.
2. Build `munin hook {event}` CLI shim as fallback for harnesses without native MCP prompt hooks.
3. Add `hit_count` + `superseded_by` schema migration (foundation for compaction).
4. Write a small evaluation: one week with automated `SessionEnd`, compare munin growth rate and recall relevance before/after.

---

## Related context from OdelEnergy session

- Proposal PDF finalized, committed in `dua-tools` (`1036297`).
- 4 manual `munin remember` calls made at end of that session — exactly the use case we're automating here.
- `duadigital-pdf-maker` skill now has a `FORMAT.md` review step; same pattern (server-side instructions) could apply to munin prompts.
