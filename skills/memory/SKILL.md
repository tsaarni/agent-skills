---
name: memory
description: "Save and recall knowledge, tasks, and schedule across sessions. Use when the user asks to remember, save, look up past work, or manage tasks and schedule."
---

# Agent Memory Protocol

Persistent, compounding memory, tasks, and schedule. Capture once, update in place, never re-derive.

## Rules

1. Writing / updating memory records and tasks
- Use dense caveman syntax to maximize information density per token

2. User communication
- Translate stored knowledge into clean, concise, simplified technical English
- Do not expose raw caveman syntax to the user

## Structure

```
~/storage/memory/
├── logs/
│   └── <hostname>.md
├── records/
│   └── <Name>.md
├── tasks/
│   └── <Name>.md
└── attachments/
    └── <Name>/
        └── <file>
```

Create the directories and `logs/$(hostname -s).md` on first write if they do not exist.

## logs/\<hostname\>.md

One line per entry in `logs/$(hostname -s).md`. DO NOT touch existing entries, append-only (newest last).

Pipe-delimited: `datetime | project | description | path`.

```
2026-07-20T09:15 | keycloak | Added LDAP federation config | records/KeycloakLdapFederation.md
2026-07-20T09:15 | - | Idea: could skills use sub-agents? |
2026-07-27T14:30 | contour | Debugged hot-restart race condition | records/ContourHotRestart.md
2026-11-25T22:05 | contour | Created task (open, due 2026-12-01) | tasks/ContourHotRestartFix.md
```

Use `-` as project for unattached entries. Always prefix column 4 with `records/` or `tasks/` (empty if no file was written).

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

Content is markdown but otherwise there aren't any expectations or conventions for the formatting of content.

## Tasks

All files in `tasks/`. Naming: `CamelCase.md`, covering the task or scheduled event.

Examples:
- `ContourHotRestartFix.md`
- `RenewTlsCerts.md`
- `DentistAppointment.md`

Frontmatter:
```yaml
---
summary: Fix hot-restart race condition
status: open
start: 2026-11-27
due: 2026-12-01
priority: high
tags: [contour, tls]
---
```

Fields:
- **Required**:
  - `summary`: one-line task or event title
  - `status`: `open` | `in-progress` | `waiting` | `done` | `cancelled`
- **Optional**:
  - `start`: `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM` — when to begin / when it becomes actionable (tasks), or event start (events)
  - `due`: `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM` — hard external deadline (tasks only; omit if there is no real deadline)
  - `end`: `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM` — inclusive end date/time (fixed-span events, appointments, or trips only; never use for tasks)
  - `repeat`: recurrence interval (e.g. `weekly`, `monthly`, `30d`)
  - `priority`: `high` | `low` (omit when normal)
  - `tags`: `[contour, tls]` (same conventions as records)

Rules:
- Do not store `created`, `updated`, or `completed` in task frontmatter — the append-only log tracks those timestamps
- Link to related memory records in the markdown body using relative links: `[ContourHotRestart](../records/ContourHotRestart.md)`
- When completing a repeating task, advance `start`/`due`/`end` by `repeat`, keep `status: open`, and log the completion
- Prefer `status: cancelled` over deleting task files

## Attachments

Store non-text or supplementary files in `attachments/<RecordName>/`. The directory name matches the record filename without `.md`.

Examples:
- `attachments/ContourHotRestart/flamegraph.svg`
- `attachments/KeycloakEventSystem/admin-console-error.png`
- `attachments/KeycloakEventSystem/event-listener-config.yaml`
- `attachments/OpenBaoRaftJoin/reproduce-race.sh`

Reference from records using relative markdown links:
```markdown
![error screenshot](../attachments/KeycloakEventSystem/admin-console-error.png)
[config](../attachments/KeycloakEventSystem/event-listener-config.yaml)
```

Rules:
- Create the attachment directory only when needed, not preemptively
- When deleting a record, also delete its attachment directory
- When renaming a record, also rename its attachment directory

## Tags

Starter set, extend freely.

Type: `idea`, `troubleshooting`, `howto`, `reference`, `decision`, `obsolete`, ...
Domain: `tls`, `kubernetes`, `performance`, `auth`, `build`, `networking`, ...
Project: `contour`, `keycloak`, `openbao`, `vault`, `envoy`, ...

## Context loading

Run at session start. Shows summaries of the 10 most recently touched records (deduplicated) and all active tasks:

```bash
# 10 most recently touched records
cd ~/storage/memory 2>/dev/null && rg -I "" logs/ 2>/dev/null | sort | awk -F' [|] ' '$4 ~ /^records\// {a[++n]=$4} END {for (i=n; i>0; i--) if (!s[a[i]]++) print a[i]}' | xargs -r rg -j1 -Hs "^summary:" | head -10

# Active tasks (open, in-progress, waiting)
cd ~/storage/memory 2>/dev/null && rg -l "^status: (open|in-progress|waiting)$" tasks/ 2>/dev/null | sort | xargs -r rg -j1 -HsN --no-heading "^(summary|status|start|end|due|repeat|priority|tags):"
```

```bash
# List all records with summaries
rg "^summary:" records/

# Find records or tasks by tag
rg "^tags:.*tls" records/ tasks/

# Full text search
rg "race condition" records/ tasks/

# Active tasks with dates (schedule / deadlines)
rg -l "^status: (open|in-progress|waiting)$" tasks/ | sort | xargs -r rg -j1 -HN --no-heading "^(summary|start|due|end):"

# Recent activity (records + tasks)
rg -I "" logs/ | sort | tail -20

# Recent task activity
rg -I "\| tasks/" logs/ | sort | tail -20

# Activity for a project
rg -I "\| contour \|" logs/ | sort

# Activity in date range
rg -I "^2026-07" logs/ | sort
```

## Write discipline

Every modification to records, tasks, or attachments requires a log entry. No exceptions.

1. Write/update/delete/rename the record, task, or attachment (bump `updated:` when editing a record)
2. Append to `logs/$(hostname -s).md` with the prefixed path (`records/<Name>.md` or `tasks/<Name>.md`)
   - When deleting a record or task file, prefix the description with `DELETED:` (use `DELETED:` **only** when deleting a record or task file, never when deleting attachments)
   - When renaming a record or task, log two lines: a `DELETED:` entry for the old path and a new entry for the new path
   - When updating a task's status or dates, note the change in the description

Log entry examples:
```
2026-07-27T14:30 | contour | Debugged hot-restart race condition | records/ContourHotRestart.md
2026-07-28T10:00 | contour | Updated with fix verification | records/ContourHotRestart.md
2026-08-01T16:45 | contour | DELETED: no longer relevant | records/ContourHotRestart.md
2026-08-01T16:45 | contour | DELETED: renamed to ContourHotRestart.md | records/ContourHotReload.md
2026-08-01T16:45 | contour | Renamed from ContourHotReload.md | records/ContourHotRestart.md
2026-08-02T11:20 | contour | Added flamegraph attachment | records/ContourHotRestart.md
2026-11-25T22:05 | contour | Created task (open, due 2026-12-01) | tasks/ContourHotRestartFix.md
2026-11-26T09:15 | contour | Status -> in-progress | tasks/ContourHotRestartFix.md
2026-11-27T15:00 | contour | Status -> done | tasks/ContourHotRestartFix.md
```
