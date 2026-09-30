# Session Start Context — {project}

You are starting a new session in project **{project}**.

Before doing any implementation work, recall relevant prior context from munin memory.
Suggested queries (run one or more based on your current task):

- `recall("recent decisions {project}")` — architecture choices, known issues, gotchas
- `recall("current work in progress {project}")` — unfinished tasks, active investigations
- `recall("conventions {project}")` — coding style, tooling, workflow rules

Other projects are reachable too: call `list_projects` to see them, then pass `project=` to `recall` or `remember` (e.g. `recall("query", project="other-repo")`). Without `project=`, calls use **{project}**.

> Do NOT auto-fire these queries. Use them as guidance — pick the ones relevant to the task at hand.
> After recalling, note any important context in your working memory before proceeding.
