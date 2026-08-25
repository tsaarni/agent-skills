---
name: memory
description: Use when persisting knowledge, recording findings, capturing ideas, or recalling what was previously learned. Activate when user asks to remember, save, look up, or review past work.
---

# Agent Memory Protocol

Persistent, compounding memory. Capture once, update in place, never re-derive.

## Rules

1. Writing / updating memory records
- Use dense caveman syntax to maximize information density per token

2. User communication
- Translate stored knowledge into clean, concise, simplified technical English
- Do not expose raw caveman syntax to the user

## Structure

```
~/.agents/memory/
├── log.md
└── records/
    └── <Name>.md
```

## log.md

One line per entry. DO NOT touch existing entries, append-only (newest last).

Pipe-delimited: `datetime | project | description | page`.

```
2026-07-27T14:30 | contour | Debugged hot-restart race condition | ContourHotRestart.md
2026-07-20T09:15 | keycloak | Added LDAP federation config | KeycloakLdapFederation.md
2026-07-20T09:15 | - | Idea: could skills use sub-agents? |
```

Use `-` as project for unattached entries. Page field empty if no page was written.

## Records

All files in `records/`. Naming: `CamelCase.md` wiki-style, covering the topic.

Examples:
- `ContourHotRestart.md`
- `KeycloakLdapFederation.md`
- `EnvoyTlsCertRotation.md`
- `SubAgentIdea.md`

Frontmatter:
```yaml
---
tags: [contour, envoy, xds, troubleshooting, tls]
summary: Root cause of hot-restart race condition
created: 2026-07-27
updated: 2026-07-27
---
```

Content is markdown but otherwise there isn't any expectations or conventions for the formatting of content.

## Tags

Starter set, extend freely.

Type: `idea`, `troubleshooting`, `howto`, `reference`, `decision`, `obsolete`, ...
Domain: `tls`, `kubernetes`, `performance`, `auth`, `build`, `networking`, ...
Project: `contour`, `keycloak`, `openbao`, `vault`, `envoy`, ...

## Context loading

Run at session start. Shows summaries of the 10 most recently touched records (deduplicated):

```bash
cd ~/.agents/memory && tac log.md | awk -F' \\| ' '$4 ~ /./ && !s[$4]++{print $4}' | head -10 | xargs -I{} rg "^summary:" records/{}
```

```bash
# List all records with summaries
rg "^summary:" records/

# Find records by tag
rg "^tags:.*tls" records/

# Full text search
rg "race condition" records/

# Recent activity
tail -20 log.md

# Activity for a project
rg "\| contour \|" log.md

# Activity in date range
rg "^2026-07" log.md
```

## Write discipline

Every modification to records requires a log entry. No exceptions.

1. Write/update/delete/rename the record
2. Append to `log.md`

Log entry examples:
```
2026-07-27T14:30 | contour | Debugged hot-restart race condition | ContourHotRestart.md
2026-07-28T10:00 | contour | Updated with fix verification | ContourHotRestart.md
2026-08-01T16:45 | contour | Deleted, no longer relevant | ContourHotRestart.md
2026-08-01T16:45 | contour | Renamed from ContourHotReload.md | ContourHotRestart.md
```
