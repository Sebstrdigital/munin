You are wrapping up a coding session. Review what happened and decide what is worth remembering for future sessions.

## Your task

Call the `remember` tool **0–3 times** — only for thoughts that will genuinely help a future session pick up where this one left off.

## What TO save

- Architectural decisions made and the reasoning behind them
- Non-obvious discoveries (e.g. a surprising constraint, a tricky bug and its root cause)
- The current state of in-progress work (what's done, what's next)
- Agreed conventions or patterns adopted for this project

## What NOT to save

- Anything already obvious from reading the code or docs
- Transient state (test output, temporary errors, "I ran X and it worked")
- Summaries of work that is fully merged and stable — the code speaks for itself
- Generic observations that apply to every project (e.g. "Python is used here")

## Format

Each call to `remember` should be a single, self-contained thought that someone (or a future agent) can act on without additional context. Be specific. Include file paths or function names where relevant.

If nothing meaningful happened this session, call `remember` zero times — that is the correct answer.
