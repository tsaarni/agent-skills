# Dependabot PR maintenance

Workflow for "check / list / merge dependabot PRs" across my repos. `G` = `~/work/agent-skills/scripts/gh-activity/gh-activity`.

## Repos

Private list, not in this repo: section A of `~/storage/memory/tasks/MaintainDependencies.md`. Read it first; missing → ask the user for repos.

## Rules
- Talk w/o step numbers (user doesn't see this file). Refer to PRs by session number.
- Act (approve, merge, comment, label) only on explicit user request naming the PR(s) ("merge 1", "rebase 8", "merge 1–7"). That request = OK; AI runs cmd, shows exact cmd in reply. Ambiguous → ask.
- Only PR text ever posted = bare dependabot cmd. Approve w/o `--body`. Never own prose, never AI identity (no "Claude"/"AI"/"Generated with"/Co-Authored-By anywhere).
- Never bypass protection: no `--admin`, no `--auto`/auto-merge, no force, no dismissing reviews, unless user explicitly asks.
- Any cmd fails → stop that PR (and rest of batch in same repo), report error verbatim, propose next step.
- Session numbers fixed: re-listing keeps old numbers; new PRs get next free numbers.
- Every reply that shows PR state (list, after merge/rebase/recreate, re-check) = full list in the List format below, never tables or partial lists. Merged PR stays in its group, same line, number struck: `~~N~~ ✔ merged · **<dep>** … · [<repo>#<num>](<url>)`. PR whose state changed → move to the new group, keep its number. Commands run go above the list, in a code block.

## List (start of session / "list" / "refresh")
1. `G status --author=app/dependabot --body=1500 <repos from list A...>` (~3–8 API pts). Header `matched` must = `shown` (else `--limit`).
2. Analyze body: what changes for us, breaking changes, security fix, multi-version jump; grouped PR → risky members; big go.mod/lock diff → `gh pr diff N -R R`.
3. Present grouped (format below) + **Suggested:** order.

```
### ✅ Ready to merge (N)
1. **<dep>** <old> → <new> · <few words: what changes, e.g. CI only / test lib / bug fixes / + pulls X> [· 🔒 security fix] · [<repo>#<num>](<url>) · <age>d
### 🔁 Stuck on conflicts (N)
2. **<dep>** … · [<repo>#<num>](<url>) · <age>d · <what was tried> → <proposed action>
### ⚠️ Needs work (N)
3. **<dep>** … · <risk reason> · [<repo>#<num>](<url>) · <age>d · ❌ <failed checks> → <proposed action>
**Suggested:** <e.g. merge 1–7 (contour one at a time) → retry 8 → diagnose 9>
```
- Ready = CI ✅ + no conflicts + not high risk (`turn=merge` or `turn=reviewers reason=awaiting_review`). Stuck = `conflicts(bot_pr)`. Needs work = `ci_failed*`, ⚠️ high risk (breaking changes, multi-version jump, core dep: Envoy/Go toolchain/k8s/Gateway API). CI running = `turn=ci`. Omit empty groups. Group by `reason=`, not `turn=`: `turn=maintainers` with me in `requested=` (stuck bot PR) → Stuck if `conflicts(bot_pr)`, Needs work if `ci_failed(bot_pr)`.
- `policy=label_missing` alone ≠ failure (still Ready). PR as markdown link `[<repo>#<num>](<url>)`, `<repo>` short name when unambiguous. Markers only when relevant (🔒 ⚠️ ❌). Note on line only if it differs from group meaning.

## "merge N" (per PR, in order given)
1. Re-check fresh: `G status --author=app/dependabot <repo>` (that repo only). Ready only if ALL:
   - PR still open, not draft; `ci=SUCCESS` (no failed/pending; label-only policy ok); `mergeable=MERGEABLE`; `merge_state` CLEAN or BLOCKED-by-review only;
   - head unchanged since listed (`last_commit` same) — else re-analyze body, tell user what changed, ask again.
   - `mergeable=UNKNOWN` → wait few s, recheck once. `BEHIND` (base moved, up-to-date required) → offer `@dependabot rebase`, don't merge.
   - Not ready → stop, report why (1 line), propose action.
2. PR in ⚠️ group → confirm once ("#N is high risk: <reason>. Merge anyway?"), proceed only on yes.
3. Approve: `gh pr review N -R R --approve` — always, skip only if `reviews: approved=[me]` already.
4. Merge: `gh pr merge N -R R --<method>` — `squash` if in header `merge_methods=`, else first allowed.
5. Verify: `gh pr view N -R R --json state,mergedAt --jq .state` = MERGED. Report `✔ N merged (<repo>#<num>)` + full list.
6. Same repo has more dependabot PRs → re-check them (step 1) before next merge; report newly conflicting/BEHIND ones.

## "merge N–M" / several
- Run "merge N" for each, in list order; different repos independent; same repo strictly one at a time with re-check between.
- Next PR in same repo turned BEHIND/conflicts → post nothing automatically; list it under Stuck, ask.
- End: summary line `merged: … · skipped: … (why)` + full list + updated Suggested.

## "rebase N" / "recreate N"
1. Re-check state (stuck still true, PR open).
2. `gh pr comment N -R R --body "@dependabot rebase"` (or `recreate`). Rebase ignored before / PR >30d → suggest recreate.
3. Verify after ~1 min: `gh api repos/R/issues/comments/<id>/reactions` has dependabot 👍, and/or new head commit. No reaction after few min → report "ignored", suggest recreate / manual bump.
4. New commit → CI reruns; tell user to recheck later (offer "list" again).

## "diagnose N" (CI failed)
- Read-only: `~/work/agent-skills/prompts/my-diagnose-ci.md` (PR N, repo R). Report class (PR-caused / flaky / infra) + evidence. `skipped>0` = tests never ran.
- Propose: flaky/infra → rerun (`gh run rerun <id> -R R --failed`, with OK); PR-caused → manual fix PR, or `@dependabot ignore this major version` (closes PR). No code changes w/o request.

## Other
- `policy=label_missing`: maintainers label `gh pr edit N -R R --add-label X` only on request.
- Contour release-branch bumps (not covered by dependabot) → contour-maintenance skill.

## Gotchas

- Dependabot cmd syntax: listed in PR body "Dependabot commands and options" (`rebase`, `recreate`, `ignore this major version|minor version|dependency`). 👍 reaction = accepted.
- Dependabot stops auto-rebasing PRs open >30d.
- Refresh list A (in the memory task): `gh-activity history --period=365d --role=reviewer --include-bots` → repos with dependabot PRs reviewed; plus own repos with `.github/dependabot.yml`.
