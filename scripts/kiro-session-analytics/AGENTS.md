# Kiro Session Analytics: Agent Guide

Read-only tool for questions about Kiro CLI session history: credit spend, what was done when, and which session ran a command or touched a file. Never parse `~/.kiro/sessions` files by hand; run:

```bash
cd <path-to>/kiro-session-analytics
go run . <command> [options]
```

## Terms

- **Session**: one conversation with Kiro. Identified by an id (use the first 8 characters). It belongs to one workspace.
- **Turn**: one user prompt and everything Kiro does in response until it stops (tool calls, then the final answer). Turns are numbered from 1 within a session (`#12` is the 12th). Credits, model and duration are recorded per turn, so most reports count turns.
- **Tool call**: one action Kiro takes during a turn, such as running a shell command, reading or writing a file, or calling an MCP tool.
- **Credits**: Kiro's usage unit, summed over the turns.
- **Request**: one call to the model within a turn. A turn with tool calls makes several (`REQ/TURN`, `CREDITS/REQ`). Logged by v3 only.
- **Workspace**: the directory the session was started in.
- **Active time** (`journal`, `habits`): the sum of turn durations. Time spent waiting for the user is not counted.
- **Engine**: `v3` is the current Kiro CLI storage format and `v2` the older one. Both are read; you do not need to choose.

## Commands

| Command | What it does |
|---|---|
| `spend` | Where the credits went. One table grouped by `--by workspace` (default), `model`, `agent`, `day` or `session`, with credits, turns and credits per turn, then the 5 most expensive turns. `--by model` adds `REQ/TURN` and `CREDITS/REQ`, which split a model's cost into steps per turn and price per step. `--by session` also shows the last-updated time and workspace. Default period: last 7 days. |
| `habits` | When you work and how session length affects cost. Fixed report: turns, credits and active time per local hour and per weekday (bar scaled to turns; quiet hours inside the active range are listed as `-`); sessions bucketed by number of turns in steps of 5, up to the longest session, with credits, requests per turn and credits per request; credits per request by turn number for each model (`-` = fewer than 3 turns). The session length tables use v3 sessions only. Default period: last 30 days. |
| `journal` | Work log per day and workspace: active time, credits, sessions, edited files, commits, pushes, PR URLs. Default period: today. |
| `find` | Turns matching `--file PATH` (read or written, matched by path suffix), `--cmd TEXT` (shell command), `--text TEXT` (prompt or response) or `--output TEXT` (tool output, slow). Case-insensitive substrings. Several options combine with AND. Default period: last 7 days. |
| `show ID#TURN` | One turn: prompt, tool calls, response. Tool output is cut to `--output-lines N` lines per call (default 6, 0 = none). |
| `show ID` | Table of the turns in a session. |

`ID` is a session id prefix (4+ characters) as printed by `spend --by session`, `journal` and `find`. Hits are printed as `sess <id> #<turn>`, which is the argument for `show`.

## Time and workspace options

`spend`, `habits`, `journal` and `find` accept one of:
`--today`, `--yesterday`, `--this-week`, `--last-week`, `--this-month`, `--last-month`, `--days N`, or a range with `--since` and `--until` (`"YYYY-MM-DD"` or `"YYYY-MM-DD HH:MM"`, local time).

The same commands accept `--workspace /path` (includes subdirectories).

## Examples

```bash
go run . spend --this-week
go run . spend --days 30 --by model
go run . spend --days 30 --by session
go run . habits
go run . journal --yesterday
go run . find --file route.go --days 60
go run . find --cmd 'kind create cluster'
go run . show 5437257e#12
```

## Notes

- The tool reports credits only. Do not convert credits to dollars in general: each tier includes a credit allowance, so only credits beyond it (overage) cost extra money.
  - Tiers: Kiro Pro 1,000 credits, Pro+ 2,000, Pro Max 5,000, Power 10,000.
  - Overage is currently $0.04 per credit past the tier allowance.
  - Estimated overage cost = max(0, credits used in the billing period - tier allowance) * $0.04.
  - To estimate, ask the user for their tier and when the billing period starts, then run `spend` for that period. Without both, report credits only.
- A turn counts toward the day it started.
- Turns with no logged usage are reported as "no usage data" and are not counted in credit totals.
- v2 credit totals are too low: `/clear` discards the stored credits of every earlier turn in the session, and v2 subagent sessions never store any. Those turns are still listed, as "no usage data". For exact billing totals, use Kiro's own usage page. v3 sessions keep the credits of every turn.
- `find --output` reads all tool output; give it a time window when possible.
- Secrets in printed text are masked.
