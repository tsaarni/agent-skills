---
name: pr-failure-analyzer
description: Analyze failed GitHub Actions workflows and logs on a Pull Request, diagnose root causes, and classify each failure as PR-caused, flaky, infrastructure, pre-existing, or external dependency.
---

# GitHub PR Workflow Failure Analyzer

Analyze failed GitHub Actions workflows on a Pull Request, diagnose root causes, and classify each failure.

## Input

You are given a `context.md` file (provided as prompt context) containing:
- Repository name, PR number, branch info
- PR info (title, body, changed files)
- Failed run and check results
- Base branch recent failure history
- A manifest of downloaded log files (local file paths) to read
- Path to the PR diff file

The log files and diff have been pre-downloaded by `scripts/analyze-pr-failures.sh`. Read them from disk as needed — do NOT re-download via `gh` CLI.

---

## Step 1. Gather Context

### 1a. Read Learnings

If `ci-analysis/learnings.md` exists in the current directory, read it.

### 1b. Review Provided Context

From `context.md` (already in your prompt), note:
- The **changed files** list
- The **failed runs** and their workflow names
- The **check results** (which checks failed, passed, or were skipped)
- The **base branch recent failures** (to identify pre-existing failures)

### 1c. Read the PR Diff

Read the diff file listed under "PR Diff" in `context.md`.

## Step 2. Read Log Files

Read each log file listed under "Downloaded Log Files" in `context.md`. These are pre-downloaded per-job logs from failed workflow runs.

**Log format notes:** `gh run view --log` output has lines tab-separated as `{job_name}\t{step_name}\t{log_line}` and typically contains ANSI color codes.

## Step 3. Analyze Each Failure

For every failed job/step, extract:
- The **error message** (compiler error, test failure, timeout, infrastructure error, etc.)
- The **failing file and line number** if present
- The **test name** if it's a test failure

To locate errors in logs, search for patterns like:
`FAIL`, `ERROR`, `TIMEOUT`, `panic`, `assert`, `Exception`, `FATAL`, `exit code`, `error:`

Cross-reference against the codebase:
- Search the local repo for the failing file/function/test
- Use the PR diff to check if the PR modified the failing code or its direct dependencies
- If the failure references a file NOT in the changed files list, flag this

For test failures, search GitHub issues for the test name:
```bash
gh search issues --repo {owner}/{repo} "{test_name}"
```
A match (open or recently closed) strongly indicates a known-flaky test.

## Step 4. Classify Each Failure

Assign one of these categories:

| Category | Criteria |
|---|---|
| **PR-caused** | Error is in code modified by this PR, or in code directly dependent on PR changes |
| **Flaky test** | Known-flaky (passes on re-run, or fails on main too), or non-deterministic (race, timeout, resource exhaustion with no code cause) |
| **Infrastructure** | Runner failed to start, network error, docker pull failure, OOM killed by runner, disk full, external service outage |
| **Pre-existing** | Failure also reproduces on the base branch (use the base branch failure history from `context.md`) |
| **External dependency** | Caused by upstream dependency update, flaky external service, or network call to third-party |

To distinguish flaky from real:
- Check if the same workflow passed on a recent re-run of this PR (visible in check results)
- Check if the same workflow fails on the base branch recently (visible in base branch failures)

## Step 5. Produce Report and Update Learnings

### Output Format

```
## PR #{number} — CI Failure Analysis

### Run: {workflow_name} (ID: {run_id})

**Job:** {job_name}
**Step:** {step_name}
**Category:** {PR-caused | Flaky test | Infrastructure | Pre-existing | External dependency}

**Error:**
{concise error message, max ~10 lines}

**Failing location:** `{file}:{line}` (if applicable)

**PR relevance:**
- [ ] File modified in this PR
- [ ] Dependency of modified code
- [ ] Unrelated to PR changes

**Evidence:**
{Brief explanation of why you classified it this way. Reference specific files,
diff hunks, or base-branch failure history.}

---
(repeat for each failure)
```

End with an overall summary:
- Count of failures by category (PR-caused / flaky / infrastructure / pre-existing )
- Recommended action: "Fix required", "Re-run should suffice", or "Investigate infra"

### Update Learnings

Update `ci-analysis/learnings.md` with any new findings. Only add generic, reusable knowledge (no PR numbers or dates). If nothing new was learned, make no change.

#### Learnings File Structure

```markdown
# CI Learnings: {owner}/{repo}

## Workflow Structure
<!-- How workflows are organized, which jobs run in parallel, which are required,
     which are informational-only, typical job naming conventions. -->

## Log Formats
<!-- How to read the logs for this project: prefixes, timestamps, noise to skip,
     where actual errors appear. -->

## Effective Log Search Patterns
<!-- Regex or keyword patterns that reliably find the root cause in this project's logs. -->

## Known Flaky Tests
<!-- Tests that are inherently unreliable. Include the workflow/job and flakiness pattern. -->

## Failure Patterns
<!-- Common failure signatures and what they mean. E.g. "exit code 137 in docker steps = OOM". -->

## Classification Hints
<!-- Project-specific rules that override default classification logic. -->
```

---

## Rules

- Be precise. Quote exact error lines from logs.
- Do NOT speculate without evidence. If uncertain, say so.
- Search broadly for root causes: transitive dependencies, generated files, build config changes.
- Focus on the FIRST error — cascading failures after the first are usually noise.
- Apply the learnings from `ci-analysis/learnings.md`: use known log search patterns, treat listed tests as flaky, apply classification hints directly.
- Always update `learnings.md` after analysis. Keep entries concise and actionable — no PR-specific details.
- Do NOT re-download logs or PR data — everything is pre-downloaded. Read files from disk.
