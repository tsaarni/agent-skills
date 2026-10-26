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

Run at session start. Shows summaries of the 10 most recently touched records (deduplicated):

```bash
cd ~/storage/memory 2>/dev/null && rg -I "" logs/ 2>/dev/null | sort | awk -F' [|] ' '$4 ~ /./ {p[++n]=$4} END {for (i=n; i>0; i--) if (!s[p[i]]++) print "records/"p[i]}' | xargs -r rg -j1 -Hs "^summary:" | head -10
```

```bash
# List all records with summaries
rg "^summary:" records/

# Find records by tag
rg "^tags:.*tls" records/

# Full text search
rg "race condition" records/

# Recent activity
rg -I "" logs/ | sort | tail -20

# Activity for a project
rg -I "\| contour \|" logs/ | sort

# Activity in date range
rg -I "^2026-07" logs/ | sort
```

## Write discipline

Every modification to records or attachments requires a log entry. No exceptions.

1. Write/update/delete/rename the record (and its attachments if applicable)
2. Append to `logs/$(hostname -s).md`
   - When deleting a record `records/<Name>.md`, prefix the description with `DELETED:` (use `DELETED:` **only** when deleting a record file, never when deleting attachments)
   - When renaming a record, log two lines: a `DELETED:` entry for the old filename and a new entry for the new filename

Log entry examples:
```
2026-07-27T14:30 | contour | Debugged hot-restart race condition | ContourHotRestart.md
2026-07-28T10:00 | contour | Updated with fix verification | ContourHotRestart.md
2026-08-01T16:45 | contour | DELETED: no longer relevant | ContourHotRestart.md
2026-08-01T16:45 | contour | DELETED: renamed to ContourHotRestart.md | ContourHotReload.md
2026-08-01T16:45 | contour | Renamed from ContourHotReload.md | ContourHotRestart.md
2026-08-02T11:20 | contour | Added flamegraph attachment | ContourHotRestart.md
```
