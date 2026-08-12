---
Description: Persistent memory wiki at `~/.agents/memory/`. 
---

# Agent Memory Protocol

Persistent, compounding memory. Write once, update, never re-derive.
Knowledge files live here.

## Startup

Read in order:
1. `hot.md` — current state and next steps
2. `inbox.md` — surface open tasks/ideas to user
3. `index.md` → specific pages — only if hot.md isn't enough
4. `rg` — last resort

## Write discipline

Every write to a knowledge page requires 3 edits, no exceptions:
1. Write the page
2. Update `index.md` with a one-line summary
3. Append to `log.md`: `## [YYYY-MM-DD] operation | Description`

log.md is append-only. inbox.md and log.md are not indexed.

## Inbox

Zero-friction capture. Format:

```
## Open tasks
- [ ] 2026-07-27 — Fix Envoy hot-restart → [notes](sessions/2026-07-27-envoy.md)

## Open ideas
- [ ] 2026-07-20 — Could pi skills use sub-agents?

## Done / promoted
- [x] 2026-07-15 — Update contour → [projects/contour.md](projects/contour.md)
```

Links to longer pages optional but encouraged. Never silently remove open items — ask first.

## Frontmatter

All content pages (not log.md, index.md):
```yaml
---
tags: [contour, envoy]
created: 2026-07-27
updated: 2026-07-27
status: draft | stable | superseded
superseded_by: some-page.md  # when status: superseded
---
```

Files: `snake-case.md`. Sessions: `sessions/YYYY-MM-DD-topic.md`. Projects: `projects/<name>.md`.

## Session end

1. Rewrite `hot.md` — current state, next steps, blockers. Under ~500 words.
2. Append session summary to `log.md`: `## [YYYY-MM-DD] session | topic`
3. Optionally write `sessions/YYYY-MM-DD-topic.md` for significant sessions.

## Lint

| Check | Action |
|-------|--------|
| index.md entries → missing files | Fix or remove |
| Files not in index.md | Add |
| superseded without superseded_by | Add link |
| Contradictions | Flag `⚠️ CONTRADICTION:` with both claims |
| hot.md stale | Rewrite |
| Inbox open items > 2 weeks no activity | Ask if still relevant |
| Inbox Done section > ~20 items | Archive old entries |
