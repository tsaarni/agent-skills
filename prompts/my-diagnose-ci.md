---
description: Analyze failed GitHub Actions CI runs of a PR, or recent failures across a repo, and classify root causes (PR-caused, flaky, infra, ...).
argument-hint: "[PR number/URL | recent [branch] [workflow]] [owner/repo]"
---

# Analyze GitHub CI failures

Arguments: $ARGUMENTS

Shell variables may not persist between commands. Set them again in each command, or chain the commands with `&&`.

```bash
R=$(gh repo view --json nameWithOwner -q .nameWithOwner)    # or owner/repo from the arguments
D=${TMPDIR:-/tmp}/agent-output/${R/\//_}/ci/pr-$N            # or .../ci/recent
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
