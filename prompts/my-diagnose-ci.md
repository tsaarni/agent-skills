---
description: Analyze failed GitHub Actions CI runs of a PR, or recent failures across a repo, and classify root causes (PR-caused, flaky, infra, ...).
argument-hint: "[PR number/URL | recent [branch] [workflow]] [owner/repo]"
---

# Analyze GitHub CI failures

Arguments: $ARGUMENTS

Shell variables may not persist between commands. Set them again in each command, or chain the commands with `&&`.

```bash
R=$(gh repo view --json nameWithOwner -q .nameWithOwner)    # or owner/repo from the arguments
D=/tmp/agent-output/${R/\//_}/ci/pr-$N                       # or .../ci/recent
rm -rf $D; mkdir -p $D
```

## Collect: PR

```bash
gh pr view $N -R $R --json title,body,headRefName,headRefOid,baseRefName,files,statusCheckRollup > $D/pr.json
gh pr diff $N -R $R > $D/pr.diff
SHA=$(jq -r .headRefOid $D/pr.json)
# Run IDs come from details_url, or from external_id for custom-app checks that delegate to workflow_run (e.g. Envoy)
RUNS=$(gh api "repos/$R/commits/$SHA/check-runs?per_page=100" --paginate --jq '.check_runs[] | select(.conclusion|IN("failure","timed_out","action_required")) | ((.details_url//"")|capture("runs/(?<i>[0-9]+)").i) // (.external_id|select(test("^[0-9]+$")))' | sort -u)
```

Fallback when RUNS is empty:
`gh run list -R $R -c $SHA -s failure --json databaseId -q '.[].databaseId'`

Base branch failures:
`gh run list -R $R -b $BASE -s failure -L 30 --json databaseId,workflowName,headSha,createdAt,event > $D/base-failures.json`

## Collect: recent

```bash
gh run list -R $R -s failure -L 10 [-b BRANCH] [-w WORKFLOW] --json databaseId,workflowName,displayTitle,headBranch,headSha,event,createdAt,url > $D/runs.json
RUNS=$(jq -r '.[].databaseId' $D/runs.json)
```

## Download failed job logs

```bash
for r in $RUNS; do (
  mkdir -p $D/$r
  gh run view $r -R $R --json name,workflowName,event,headBranch,headSha,createdAt,url,attempt,jobs > $D/$r/run.json
  for j in $(jq -r '.jobs[]|select(.conclusion=="failure").databaseId' $D/$r/run.json); do
    gh api --allow-escape-sequences repos/$R/actions/jobs/$j/logs | sed -E 's/^[^ ]*Z //; s/\x1b\[[0-9;]*m//g' > $D/$r/$j.log
  done ) & done; wait
```

Layout: `$D/{pr.json,pr.diff,base-failures.json,runs.json}`, `$D/<run_id>/run.json`, `$D/<run_id>/<job_id>.log`.

## Navigate

```bash
# failed jobs and steps: job_id, job name, failed step
jq -r '.jobs[]|select(.conclusion=="failure")|"\(.databaseId)\t\(.name)\t\([.steps[]|select(.conclusion=="failure").name]|join(", "))"' $D/*/run.json
# cheap error summary, often enough by itself
gh api repos/$R/check-runs/$JOB/annotations --jq '.[]|select(.annotation_level=="failure")|.message|gsub("\u001b\\[[0-9;]*m";"")'
# errors in a log; start here before reading large logs
grep -n -E '##\[error\]|--- FAIL|FAIL:|FAILED|panic:|Error:|exit code|timed out' $D/$r/$j.log | head -40
# workflow pass/fail ratio on base branch (flakiness signal)
gh run list -R $R -b $BASE -w "$WF" -L 50 --json conclusion -q 'group_by(.conclusion)|map({(.[0].conclusion):length})|add'
# failed jobs of another run (e.g. a base-failures entry)
gh run view $ID -R $R --json jobs -q '.jobs[]|select(.conclusion=="failure").name'
# other runs of the same workflow failing on other branches
gh run list -R $R -w "$WF" -s failure -L 30 --json databaseId,headBranch,createdAt
# known flaky test issues
gh search issues --repo $R "$TEST" --json url,state,title
# same commit: reruns and other attempts
gh run list -R $R -c $SHA --json databaseId,workflowName,conclusion,attempt
```

## Gotchas

- Know which commit was tested. `pull_request` workflows test a merge of the PR onto the base branch as it was when the run started, so a failure can come from a base-branch change the PR's own branch doesn't have yet. `workflow_run` workflows run on the latest commit of the default branch, not the PR commit. In both cases, check what the base branch looked like at that point before classifying a failure as PR-caused.
- Runs started by `workflow_run` are recorded under the default branch, not the PR. `gh run list -c <PR sha>` doesn't find them, and run lists for the default branch include such runs from every PR, so they don't show the default branch's health. If the project posts its own check runs on PR commits (`app.slug` is not `github-actions`), take the run ID from `external_id` or `details_url` as in Collect. Use the same app's check runs on recent default-branch commits for the branch's real history:
  ```bash
  APP=$(gh api repos/$R/check-runs/$CHECK_RUN_ID --jq .app.id)   # any of the app's check runs
  for s in $(gh api "repos/$R/commits?sha=$BASE&per_page=10" --jq '.[].sha'); do
    gh api "repos/$R/commits/$s/check-runs?app_id=$APP" --jq ".check_runs[]|\"$s \(.name) \(.conclusion)\""
  done
  ```
- A summary job (e.g. `complete`, `status`, `all-checks`) fails whenever another job fails and has no error of its own. Skip it.
- Logs print the inputs of each action step, which can contain words like `ERROR` or `FAILED`. A grep match there is not an error. Check the annotations first.
- Builds and tests may run outside the GitHub runner (Bazel remote execution, BuildBuddy, EngFlow, Buildkite). File paths in the log, such as `test.log`, are on the remote machine and can't be fetched. Use the test output printed in the job log, and report the external link if there is one.
- Rerunning depends on the user's access. Users with write access can run `gh run rerun <run_id> --failed`. Others need a bot command if the repo has one (see CONTRIBUTING.md, or comments like `/retest` on other PRs), or a maintainer. Recommend the rerun; don't trigger it yourself.
- A failing test may come from another repository (a fetched dependency, an examples repo). Search issues in that repository too.

## Analysis

For each failed job, find the **first** error. Later errors are usually a cascade. Quote the exact error line and note the file:line and test name. For a PR, check the diff to see whether the PR touched the failing code, its dependencies, generated files or build config. Search the local repo for the failing test or function. Search issues for failing test names: an open or recently closed match strongly suggests the test is flaky. Don't speculate without evidence. Use **Uncertain** when the evidence is missing or contradictory.

Categories:
- **PR-caused**: the error is in code modified by the PR, or in code that depends on it directly.
- **Flaky test**: non-deterministic (race, timeout, resource exhaustion with no code cause), fails on base or other branches too, or passes on rerun. If the PR is trying to fix that flakiness, still classify the failure as Flaky test and say so.
- **Infrastructure**: the runner didn't start, network or docker pull failure, OOM (exit 137), disk full, external service outage.
- **Pre-existing**: the same job also fails on the base branch.
- **Regression** (recent mode): starts at a specific commit or time and reproduces consistently.
- **External dependency**: an upstream dependency update, or a flaky third-party service.
- **Uncertain**

In recent mode, focus on patterns: the same job or test failing across runs and branches, one workflow failing consistently, systemic infrastructure problems, and the point in time where a regression started.

## Report

```
## PR #N: CI Failure Analysis     (recent: ## Recent CI Failures: owner/repo, with a Summary and Patterns section first)

### Run: <workflow> (ID: <run_id>)
**Job:** <job>   **Step:** <step>   **Category:** <category>
(recent: also Branch, Commit, Date, Event)
**Error:**
<exact error lines, up to ~10>
**Failing location:** `file:line`
**PR relevance:** File modified in this PR | Dependency of modified code | Related infrastructure modified by PR | Unrelated to PR changes
**Evidence:** <why: diff hunks, base failure history, issue URL (open/closed), other runs>
```

End with the count of failures per category and a recommended action: Fix required, Re-run should suffice, or Investigate infra. In recent mode, list what needs investigation, what is safe to ignore and what needs infra attention.
