Your task is to analyze recent failed GitHub Actions workflow runs for a repository, identify patterns, and produce a triage report. Follow the structured steps below.

---

## Step 1. Gather Context

### 1a. Read Learnings

If `{{ ctx.basedir }}/../learnings.md` exists, read it.

### 1b. Review Collected Data

Review the files listed in **Collected Data** below.

## Step 2. Read Log Files

Read each log file listed under **Failed Runs** below.

**Log format notes:** One file per failed job. Each line is `<job_name>\tUNKNOWN STEP\t<timestamp> <log_line>`. To get actual step names and which step failed, use the `jobs.json` listed under each run:
```bash
jq '.[] | select(.conclusion=="failure") | {job: .name, steps: [.steps[] | {name, conclusion}]}' <run_dir>/jobs.json
```
Lines may contain ANSI color codes.

## Step 3. Analyze Each Failure

For every failed job/step, extract:
- The **error message** (compiler error, test failure, timeout, infrastructure error, etc.)
- The **failing file and line number** if present
- The **test name** if it's a test failure

For test failures, search GitHub issues for the test name:
```bash
gh search issues --repo {{ ctx.repo }} "{test_name}"
```
A match (open or recently closed) strongly indicates a known-flaky test. Record the full issue URL.

## Step 4. Detect Patterns

Group failures and look for:
- **Recurring failures** — same job/test failing across multiple runs or branches
- **Flaky tests** — tests that fail intermittently (different branches, different commits)
- **Regressions** — new failure appearing after a specific point in time or commit
- **Infrastructure issues** — runner failures, network errors, docker pull failures, OOM
- **Workflow-specific problems** — one workflow consistently failing

## Step 5. Classify Each Failure

Assign one of these categories:

| Category | Criteria |
|---|---|
| **Flaky test** | Same test fails intermittently across different branches/PRs, or is non-deterministic |
| **Infrastructure** | Runner failed to start, network error, docker pull failure, OOM, disk full, external service outage |
| **Regression** | New failure that started at a specific commit/time, consistently reproduces |
| **External dependency** | Caused by upstream dependency update, flaky external service |
| **Uncertain** | Evidence is insufficient to determine the cause |

## Step 6. Produce Report and Update Learnings

### Output Format

```
## Recent CI Failures — {{ ctx.repo }}

### Summary

- Total failed runs analyzed: N
- Failures by category: ...
- Most affected workflow(s): ...
- Most affected branch(es): ...

### Patterns Detected

<Describe recurring patterns, grouped failures, and trends>

---

### Run: <workflow_name> (ID: <run_id>)
**Branch:** <branch>
**Commit:** <sha>
**Date:** <created_at>
**Event:** <event>

**Job:** <job_name>
**Category:** <Flaky test | Infrastructure | Regression | External dependency | Uncertain>

**Error:**
<concise error message, max ~10 lines>

**Evidence:**
<Brief explanation of classification. Reference other runs if same failure repeats.>

---
(repeat for each failure)
```

End with recommended actions:
- Which failures need investigation (regressions)
- Which are safe to ignore (known flaky)
- Which need infrastructure attention

### Update Learnings

Update `{{ ctx.basedir }}/../learnings.md` with any new findings. Only add generic, reusable knowledge (no dates or run IDs). If nothing new was learned, make no change.

---

## Rules

- All data is pre-downloaded under `{{ ctx.basedir }}/`. Do NOT re-download. You MAY use `gh` for lookups not covered by the collected data (e.g., `gh search issues`).
- For large logs (>50KB), search for error patterns first:
  ```bash
  grep -n -E 'FAIL|FAILED|panic|Error:|TIMEOUT|exit code|error:' <logfile> | head -30
  ```
- Focus on the FIRST error in each log — cascading failures after the first are usually noise.
- Be precise. Quote exact error lines from logs.
- Do NOT speculate without evidence. If uncertain, classify as **Uncertain**.
- Prioritize pattern detection: the main value of this mode is identifying systemic issues.

---

# Collected Data

## Overview

- Repository: {{ ctx.repo }}
- Failed runs analyzed: {{ ctx.failed_runs | length }}
{% if ctx.branch_filter %}- Branch filter: {{ ctx.branch_filter }}{% endif %}
{% if ctx.workflow_filter %}- Workflow filter: {{ ctx.workflow_filter }}{% endif %}
- Output dir: `{{ ctx.basedir }}/`

## Collected Files

| File | Description |
|---|---|
| `{{ ctx.basedir }}/runs-summary.json` | List of all failed runs with metadata (workflow, branch, sha, date, event, url) |

## Failed Runs

{% if ctx.failed_runs -%}
{% for run in ctx.failed_runs -%}
### Run {{ run.run_id }} — {{ run.workflow_name }}

- **Title:** {{ run.display_title }}
- **Branch:** {{ run.branch }}
- **SHA:** {{ run.head_sha[:12] }}
- **Date:** {{ run.created_at }}
- **Event:** {{ run.event }}
- **URL:** {{ run.url }}

{% if run.failed_jobs -%}
Failed jobs: {{ run.failed_jobs | join(', ') }}
{% endif -%}
{% if run.logs -%}
Logs:
{% for log in run.logs -%}
- `{{ log.path }}` ({{ log.size_display }})
{% endfor -%}
{% else -%}
No logs downloaded.
{% endif -%}
Job details: `{{ ctx.basedir }}/{{ run.run_id }}/jobs.json`

{% endfor -%}
{% else -%}
No failed runs found.
{% endif %}
