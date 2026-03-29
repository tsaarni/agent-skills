Your task is to analyze failed GitHub Actions workflows on a Pull Request, diagnose root causes, and classify each failure. Follow the structured steps below to produce a comprehensive analysis report and update the CI learnings for this repository.

---

## Step 1. Gather Context

### 1a. Read Learnings

If `{{ ctx.basedir }}/learnings.md` exists, read it.

### 1b. Review Collected Data

Review the files listed in **Collected Data** below.

## Step 2. Read Log Files

Read each log file listed under **Failed Runs** below.

**Log format notes:** One file per failed job. Each line is `<job_name>\tUNKNOWN STEP\t<timestamp> <log_line>`. The step name in logs is always `UNKNOWN STEP`. To get actual step names and which step failed, use the `jobs.json` listed under each run below:
```bash
jq '.[] | select(.conclusion=="failure") | {job: .name, steps: [.steps[] | {name, conclusion}]}' <run_dir>/jobs.json
```
Lines may contain ANSI color codes.

## Step 3. Analyze Each Failure

For every failed job/step, extract:
- The **error message** (compiler error, test failure, timeout, infrastructure error, etc.)
- The **failing file and line number** if present
- The **test name** if it's a test failure

Cross-reference against the codebase:
- Search the local repo for the failing file/function/test
- Use the PR diff to check if the PR modified the failing code or its direct dependencies

For test failures, search GitHub issues for the test name:
```bash
gh search issues --repo {{ ctx.repo }} "{test_name}"
```
A match (open or recently closed) strongly indicates a known-flaky test. Record the full issue URL and its open/closed status for use in the report and `learnings.md`.

## Step 4. Classify Each Failure

Assign one of these categories:

| Category | Criteria |
|---|---|
| **PR-caused** | Error is in code modified by this PR, or in code directly dependent on PR changes |
| **Flaky test** | Known-flaky (passes on re-run, or fails on main too), or non-deterministic (race, timeout, resource exhaustion with no code cause) |
| **Infrastructure** | Runner failed to start, network error, docker pull failure, OOM killed by runner, disk full, external service outage |
| **Pre-existing** | Failure also reproduces on the base branch (check the base branch failure history) |
| **External dependency** | Caused by upstream dependency update, flaky external service, or network call to third-party |
| **Uncertain** | Evidence is insufficient or contradictory to determine the cause |

To distinguish flaky from real:
- Check if the same workflow passed on a recent re-run of this PR (check suite results in pull-request.json):
  ```bash
  jq '.commits.nodes[0].commit.checkSuites.nodes[] | select(.conclusion != null and .workflowRun.workflow.name != null) | {workflow: .workflowRun.workflow.name, conclusion, checks: [.checkRuns.nodes[] | {name, conclusion}]}' <output_dir>/pull-request.json
  ```
- Check if the same job fails on the base branch recently (base-branch-failures.json)
- If the PR is specifically attempting to fix the flaky behavior that caused the failure (e.g., adding wait conditions, fixing race conditions), classify as **Flaky test**, not PR-caused. Note in the evidence that the failure demonstrates the pre-existing flakiness the PR aims to address.

## Step 5. Produce Report and Update Learnings

### Output Format

```
## PR #<number> — CI Failure Analysis

### Run: <workflow_name> (ID: <run_id>)

**Job:** <job_name>
**Step:** <step_name>
**Category:** <PR-caused | Flaky test | Infrastructure | Pre-existing | External dependency | Uncertain>

**Error:**
<concise error message, max ~10 lines>

**Failing location:** `<file>:<line>` (if applicable)

**PR relevance:** <One of: "File modified in this PR", "Dependency of modified code",
"Related infrastructure modified by PR", or "Unrelated to PR changes".
Add brief explanation if nuanced.>

**Evidence:**
<Brief explanation of why you classified it this way. Reference specific files,
diff hunks, or base-branch failure history.>

---
(repeat for each failure)
```

End with an overall summary:
- Count of failures by category
- Recommended action: "Fix required", "Re-run should suffice", or "Investigate infra"

### Update Learnings

Update `{{ ctx.basedir }}/learnings.md` with any new findings. Only add generic, reusable knowledge (no PR numbers or dates). If nothing new was learned, make no change.

#### Learnings File Structure

```markdown
# CI Learnings: <owner>/<repo>

## Workflow Structure
<!-- Which workflows/jobs exist, which are required vs informational, parallel structure. -->

## Log Analysis
<!-- Project-specific log patterns, error signatures, effective search strategies.
     E.g. "Go test failures appear on lines matching '--- FAIL:'". -->

## Known Flaky Tests
<!-- Tests that fail intermittently. Include workflow/job, flakiness pattern,
     GitHub issue URL if tracked (e.g., https://github.com/owner/repo/issues/NNN),
     and whether the issue is open or closed. -->

## Failure Patterns
<!-- Recurring failure signatures, what they mean, and how to classify them.
     E.g. "exit code 137 in docker steps → Infrastructure (OOM)". -->
```

---

## Rules

- All data is pre-downloaded under `{{ ctx.basedir }}/{{ ctx.pr_number }}/`. Do NOT re-download. You MAY use `gh` for lookups not covered by the collected data (e.g., `gh search issues`).
- For large logs (>50KB), search for error patterns first rather than reading the entire file:
  ```bash
  grep -n -E 'FAIL|FAILED|panic|Error:|TIMEOUT|exit code|error:' <logfile> | head -30
  ```
- Focus on the FIRST error — cascading failures after the first are usually noise.
- If a failure references a file NOT in the changed files, flag it.
- Be precise. Quote exact error lines from logs.
- Do NOT speculate without evidence. If uncertain, classify as **Uncertain**.
- Search broadly for root causes: transitive dependencies, generated files, build config changes.
- Apply learnings from `{{ ctx.basedir }}/learnings.md`: use known log search patterns, treat listed tests as flaky, apply classification hints.
- Always update `learnings.md` after analysis. Keep entries concise and actionable — no PR-specific details.

---

# Collected Data

## Overview

- Repository: {{ ctx.repo }}
- PR: [#{{ ctx.pr_number }}](https://github.com/{{ ctx.repo }}/pull/{{ ctx.pr_number }}) — {{ ctx.pr_title }}
- Head: {{ ctx.head_sha }} ({% if ctx.head_owner and ':' not in ctx.pr_branch %}{{ ctx.head_owner }}:{{ ctx.pr_branch }}{% else %}{{ ctx.pr_branch }}{% endif %} → {{ ctx.base_branch }})
- Output dir: `{{ ctx.basedir }}/{{ ctx.pr_number }}/`

## Collected Files

| File | Description |
|---|---|
| `{{ ctx.basedir }}/{{ ctx.pr_number }}/pr.diff` | Full PR diff |
| `{{ ctx.basedir }}/{{ ctx.pr_number }}/pull-request.json` | PR metadata. Key paths: `.body` (description), `.files.nodes[]` (changed files with `.path`, `.additions`, `.deletions`), `.commits.nodes[0].commit.checkSuites.nodes[]` (check results with `.conclusion`, `.workflowRun.workflow.name`, `.checkRuns.nodes[]` for failed checks) |
| `{{ ctx.basedir }}/{{ ctx.pr_number }}/base-branch-failures.json` | Recent base branch failures. Array of `{workflow, runId, committedDate, commit, failedJobs[]}` — use `failedJobs` to check if the same job fails on the base branch |

## Failed Runs

{% if ctx.failed_runs -%}
{% for run in ctx.failed_runs -%}
### Run {{ run.run_id }} — {{ run.name }}

{% if run.failed_jobs -%}
Failed jobs: {{ run.failed_jobs | join(', ') }}
{% endif -%}
{% if run.logs -%}
Logs:
{% for log in run.logs -%}
- `{{ log.path }}` ({{ log.size_kb }}KB)
{% endfor -%}
{% else -%}
No logs downloaded.
{% endif -%}
Job details: `{{ ctx.basedir }}/{{ ctx.pr_number }}/{{ run.run_id }}/jobs.json`

{% endfor -%}
{% else -%}
No failed runs found for this PR head SHA.
{% endif %}
