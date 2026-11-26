---
name: memory
description: "Save and recall knowledge and tasks across sessions. Use when the user asks to remember, save, look up past work, or manage tasks, including recurring ones."
---

# Agent Memory Protocol

Persistent, compounding memory and tasks. Capture once, update in place, never re-derive.

## Rules

1. Writing / updating memory records
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
2026-11-25T22:05 | contour | Added task (open, due 2026-12-01) | records/ContourHotRestart.md
```

Use `-` as project for unattached entries. Always prefix column 4 with `records/` (empty if no file was written). Older entries may point to `tasks/`; leave them as is.

## Records

All files in `records/`. Naming: `CamelCase.md` wiki-style, covering the topic.

Examples:
- `ContourHotRestart.md`
- `KeycloakLdapFederation.md`
- `EnvoyTlsCertRotation.md`
- `SubAgentIdea.md`

Frontmatter (all four fields required):
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

A task is a record with a `status:` field. Everything in Records applies (naming, `created`/`updated`, tags, attachments, logging). Remove `status:` to turn a task back into a plain record.

Examples:
- `ContourHotRestart.md` (knowledge record that also tracks the fix)
- `MaintainDependencies.md`
- `RenewTlsCerts.md`

Frontmatter:
```yaml
---
tags: [contour, envoy, troubleshooting, tls]
summary: Root cause of hot-restart race condition; fix pending
status: open
start: 2026-11-27
due: 2026-12-01
priority: high
created: 2026-07-27
updated: 2026-11-25
---
```

Fields:
- **Required** (same as every record): `summary`, `tags`, `created`, `updated`, plus:
  - `status`: `open` | `in-progress` | `waiting` | `done` | `cancelled`
- **Optional** (omit rather than leave empty):
  - `start`: `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM` (local time) — when it becomes actionable. Omit if actionable now
  - `due`: same format — hard external deadline; omit if there is no real deadline
  - `repeat`: `daily` | `weekly` | `biweekly` | `monthly` | `yearly` | `<N>d` | `<N>w` — `monthly` keeps the day of month; requires `start`
  - `priority`: `high` | `low` (omit when normal)

Scheduling (computed from frontmatter by the context loading query, never stored):
- Overdue: active (`open`, `in-progress`, `waiting`) and `due` passed
- Now: `open` or `in-progress`, and `start` reached or absent
- Waiting: `waiting` (blocked on someone or something), and `start` reached or absent — not actionable, report separately
- Soon (only with a horizon `UNTIL`): active, `start` in the future, and `start` or `due` on or before `UNTIL`
- Upcoming: active and `start` in the future (beyond `UNTIL`, if set); appears under Now (or Waiting) once `start` is reached

Creating a task:
1. Pick a `CamelCase.md` name; if a record on the topic already exists, add `status:` to it instead of creating a new file
2. Write the frontmatter: required fields, `status: open`, and only the optional fields that apply
3. Put the details, steps, and links in the body
4. Log `Created task (open[, start ...][, due ...][, repeat ...])`, or `Added task (...)` when adding `status:` to an existing record

Creating a recurring task: set `start` to the first occurrence and `repeat` to the interval. Add `due` only if each occurrence has a hard deadline; it advances together with `start`.

```yaml
---
tags: [github, dependencies, maintenance]
summary: Merge dependabot PRs, update repos without dependabot
status: open
start: 2026-11-26
repeat: weekly
created: 2026-11-26
updated: 2026-11-26
---
```

Completed on 2026-11-26 → `start: 2026-12-03`. Completed late on 2026-12-12 → `start: 2026-12-17` (stays on the weekly cadence; skipped runs are not owed).

Rules:
- Frontmatter is the only source of current state; logs are history only, never needed to compute state
- Completing a non-repeating task: set `status: done`, log `COMPLETED: <optional note>`
- Completing a repeating task: advance `start` (and `due`, if set) by `repeat` from their previous values, repeating until `start` is in the future (missed runs do not pile up); keep `status: open`; log `COMPLETED: next start <date>`
- Tasks only: appointments and meetings do not belong here
- Prefer `status: cancelled` over deleting task files
- Link related records in the body: `[ContourHotRestart](ContourHotRestart.md)`

## Attachments

Store non-text or supplementary files in `attachments/<RecordName>/` (tasks included). The directory name matches the record filename without `.md`.

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

Run at session start. Shows summaries of the 10 most recently touched records (deduplicated, active tasks excluded since they are listed separately) and all active tasks grouped as overdue / now / waiting / upcoming (high priority first). Groups are always relative to the real current time:

```bash
# 10 most recently touched records (active tasks excluded; done/cancelled tasks included)
cd ~/storage/memory 2>/dev/null && rg -I "" logs/ 2>/dev/null | sort | awk -F' [|] ' '$4 ~ /^records\// {a[++n]=$4} END {for (i=n; i>0; i--) if (!s[a[i]]++) print a[i]}' | xargs -r rg -j1 --no-messages --files-without-match "^status: (open|in-progress|waiting)$" | head -10 | xargs -r rg -j1 -H --no-messages "^summary:"

# Active tasks: overdue / now / waiting / [soon] / upcoming
# Optional horizon: UNTIL=YYYY-MM-DD[THH:MM] adds a "soon" group for tasks starting or due by then
cd ~/storage/memory 2>/dev/null && awk -v now="$(date +%FT%R)" -v until="${UNTIL:-}" '
BEGIN { if (until != "" && until !~ /T/) until = until "T23:59" }
FNR==1 { fm=0; split("", f); if (FILENAME !~ /^records\/[^.\/]+\.md$/) nextfile }
/^---$/ { if (++fm==2) emit(); next }
fm==1 && match($0, /^[a-z]+: /) { f[substr($0,1,RLENGTH-2)] = substr($0,RLENGTH+1) }
function emit(   g, x, out) {
  if (f["status"] !~ /^(open|in-progress|waiting)$/) return
  if (("due" in f) && f["due"] < substr(now,1,length(f["due"]))) g="1 overdue"
  else if (("start" in f) && f["start"] > substr(now,1,length(f["start"]))) {
    if (until != "" && (f["start"] <= substr(until,1,length(f["start"])) || (("due" in f) && f["due"] <= substr(until,1,length(f["due"]))))) g="4 soon"
    else g="5 upcoming"
  }
  else if (f["status"] == "waiting") g="3 waiting"
  else g="2 now"
  out = g " " (!("priority" in f) ? 2 : f["priority"]=="high" ? 1 : 3) " " FILENAME ": " f["summary"]
  for (x in f) if (x ~ /^(status|start|due|repeat|priority)$/) out = out " | " x "=" f[x]
  print out
}' records/*.md | sort | cut -d" " -f2,4-
```

Task questions → run the Active tasks command above, then:
- "What is pending / what should I do today?" → report `overdue` + `now`, then `waiting` separately (blocked, with what it waits on from the body)
- "What is coming up this week / by Friday?" → run with `UNTIL=<last day of period>` and report `overdue` + `now` + `soon`; `overdue` always means late as of today
- "What am I waiting on?" → report `waiting`
- "What is the next occurrence of X?" → its `start`
- Repeating tasks need no special handling: after completion `start` holds the next occurrence, so they show as `upcoming` until that day and `now` from then on until completed again

Other queries (run from `~/storage/memory`):

```bash
# List all records with summaries
rg "^summary:" records/

# Find records by tag
rg "^tags:.*tls" records/

# Full text search
rg "race condition" records/

# All tasks with status, including done and cancelled
rg -H "^status:" records/

# Tasks for a topic / tag
rg -l "^status:" records/ | xargs -r rg -l "^tags:.*contour"

# Task history (created, status changes, completions)
rg -I "\| records/MaintainDependencies.md$" logs/ | sort

# Last completion of a task
rg -I "\| COMPLETED:.*\| records/MaintainDependencies.md$" logs/ | sort | tail -1

# Recently completed tasks
rg -I "\| COMPLETED:" logs/ | sort | tail -20

# Recent activity
rg -I "" logs/ | sort | tail -20

# Activity for a project
rg -I "\| contour \|" logs/ | sort

# Activity in date range
rg -I "^2026-07" logs/ | sort
```

## Write discipline

Every modification to records (tasks included) or attachments requires a log entry. No exceptions.

1. Write/update/delete/rename the record or attachment (bump `updated:`)
2. Append one line to `logs/$(hostname -s).md` with the prefixed path `records/<Name>.md`; make sure the file ends with a newline before and after appending (`echo "..." >> logs/$(hostname -s).md`)
   - Reserved prefixes start the description, are UPPERCASE with a colon, and are never used for anything else (queries depend on them):
     - `DELETED:` **only** when deleting a record file (never for attachments)
     - `COMPLETED:` **only** when completing a task
   - When renaming a record, log two lines: a `DELETED:` entry for the old path and a new entry for the new path
   - Attachment changes are logged against the owning record
   - When updating a task's status or dates, note the change in the description

Log entry examples:
```
2026-07-27T14:30 | contour | Debugged hot-restart race condition | records/ContourHotRestart.md
2026-07-28T10:00 | contour | Updated with fix verification | records/ContourHotRestart.md
2026-08-01T16:45 | contour | DELETED: no longer relevant | records/ContourHotRestart.md
2026-08-01T16:45 | contour | DELETED: renamed to ContourHotRestart.md | records/ContourHotReload.md
2026-08-01T16:45 | contour | Renamed from ContourHotReload.md | records/ContourHotRestart.md
2026-08-02T11:20 | contour | Added flamegraph attachment | records/ContourHotRestart.md
2026-11-25T22:05 | contour | Added task (open, due 2026-12-01) | records/ContourHotRestart.md
2026-11-26T09:15 | contour | Status -> in-progress | records/ContourHotRestart.md
2026-11-27T15:00 | contour | COMPLETED: fix merged | records/ContourHotRestart.md
2026-11-28T10:00 | - | COMPLETED: next start 2026-12-05 | records/MaintainDependencies.md
```
