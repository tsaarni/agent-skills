---
name: gh-activity
description: "Script for bulk GitHub PR/issue analysis that plain gh can't do cheaply: whose turn and how long across many PRs, PRs waiting on me across all repos, repo PR backlog/merge-speed stats, a user's contributions over a period (impact summary), checking/merging/rebasing my pending dependabot PRs. Not for single-PR or routine gh operations."
---

`G` = `~/work/agent-skills/scripts/gh-activity/gh-activity`. Read-only. `G <cmd> --help` for flags.

## Commands

| Need | Command |
|---|---|
| PRs waiting on me (review requested, re-review, my PRs needing action/merge, stuck bot PRs I'm asked on) | `G status --mine [--top=5] [repos]` → `mine=review_requested\|re-review\|author` |
| top N of anything | `--top=N` (`--limit` = fetch size, not result size; keep default) |
| PR triage in repos (CI, conflicts, reviews, whose turn) | `G status [--author=<login\|@me\|app/dependabot>] [--state=open\|merged\|closed\|all] [--since=7d] <repos>` (`--since` = merged/closed date with `--state=merged\|closed`, else created) |
| stale / oldest | `--sort=waiting` / `--sort=age` (server-side) |
| my pending dependabot PRs: check / list / merge / rebase | follow [dependabot.md](dependabot.md) (repo list + workflow) |
| dependabot PRs waiting in other project(s): mergeable, stuck, risky | `G status --author=app/dependabot [--body=1500] <repos>` → `turn=merge` ready, `(bot_pr)` stuck (rebase/recreate), body = summary + release notes (bot noise stripped) |
| repo stats: backlog, idle, opened vs merged | `G status --stats --limit=0 <repos>` (1 API point) |
| merge speed | `G status --state=merged --since=30d --stats --limit=100 <repo>` → `time_to_merge` |
| flaky CI, turn distribution (open PRs) | `G status --stats --limit=100 <repo>` → `fetched …` lines |
| review load / top reviewers+authors | `G status --state=all --since=30d --stats --limit=100 <repo>` → `top reviewers`, `top authors` |
| contributions over period | `G history [--author=<login>] [--period=7d\|2026-09\|q3\|2026\|A..B] [--role=author\|reviewer\|commented] [--type=pr\|issue] [--include-bots] [repos]` |
| what user did in period incl. on older items ("last 60 days" = previous 60 calendar days) | add `--date=activity` (user authored/reviewed/commented within period; `acted=` date) |
| details of many PRs/issues incl. a user's reviews + inline comments, and what references them (reverts, follow-ups, upstream) | `G inspect <url\|owner/repo#N>… [--author=any\|<login>]` (stdin ok, ~1 point/10 items); one PR without inline comments → plain `gh pr view` |

## Impact summary (performance review, update, nomination)

1. `G history --period=<p> [--author=<u>] --date=activity`. From `focus:` split repos into primary / upstream / personal. Maintenance load: rerun with `--include-bots --role=reviewer`, report as one line ("reviewed N dependency PRs").
2. Pick 10–20 items: authored features/fixes/security, reviews and discussions with high `c=`; skip version bumps, lint, release chores (count them instead).
3. `G inspect <urls…> [--author=<u>]`: `files` → subsystems touched; `closes` + `referenced_by` → full arcs (diagnosis → fix → revert/follow-up → upstream/downstream adoption), follow them across repos; `review`/`inline`/`comment` → bugs caught, fixes verified, design input. `--max-body=0` if key text is cut; `gh pr diff` only if body is empty.
4. Frame for the audience (review / nomination / manager update). Write by theme/arc (problem → fix → outcome), not by repo or date. Separate merged vs open; note reverts. Link every claim `[repo#N](url)`. State scope: period, repos, that direct commits outside PRs aren't counted.

## Output

- Check header first: `matched` vs `shown` (`truncated` → raise `--limit` / narrow), `bots_hidden`, `ranges`. Big repo → start with `--stats --limit=0`.
- `turn=author|reviewers(<who>)|merge|maintainers|ci reason=… waiting=Nd [policy=label_missing]`
  - reasons: `draft conflicts ci_failed dco_missing ci_running changes_requested feedback_from_X approved_by_X_needs_more awaiting_review awaiting_re-review approved ready`; `(bot_pr)` suffix = bot PR, maintainers must act.
  - heuristic: last commit date, comments, reviews, thread replies; bot comments and `@dependabot`/`/cmd` ignored.
- `ci=STATE checks=N passed failed=N[names] pending=N[names] skipped`: label/DCO-only failures are not `ci_failed`; `skipped>0` after failure = tests never ran.
- `merge_state` CLEAN/BLOCKED/BEHIND/DIRTY; `mergeable=UNKNOWN` (rechecked once) → rerun later.
- `reviews: … requested=[…]`; `hidden(codeowner)` = team invisible to token.
- `threads: unresolved (outdated) resolved`, `closes: #N`, `last_activity …`, header `merge_methods=`.
- Closed/merged PRs: no `turn`, `state=merged@date open_for=Nd`.
- `--mine` `waiting` for review requests = since author's last push/comment (or my last review), not since the request.
- `me=<login>` in headers = authenticated user.
- inspect `referenced_by: #N(pr:merged closes-this) title | …` = items linking to this one.
- `--stats` counts whole repo (ignore `--since/--state/--no-draft`); `ci_pending` overcounts. `idle_over_30d` = no update at all; differs from `waiting` (time in current turn). `fetched …` lines cover only fetched PRs.
- history: `- role:type:state #N date c=<comments> [by=<author>] title [labels] url`; role author > reviewer > commented; period = item creation date unless `--date=activity` (user's own action date).
- Defaults: status `--limit` 30 (`--mine` 100), history 365d. `note: server timeout, retrying` = normal.
