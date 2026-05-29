# GitHub CI Failure Analyzer

Gathers failed GitHub Actions workflow logs and produces a prompt document for agent-assisted failure analysis.
Leverages the `gh` CLI to retrieve metadata, diffs, logs, and failure history through the GitHub API.

## Prerequisites

- [gh](https://cli.github.com/) CLI, authenticated
- [uv](https://docs.astral.sh/uv/)

## Usage

### Analyze a PR

```bash
# Check out the PR branch, then run from within the repository:
gh pr checkout <pr_number>
gh-ci-analyze pr

# Or specify explicitly:
gh-ci-analyze pr --repo owner/repo --pr 123
```

### Analyze recent failures across the repo

```bash
# Last 10 failed runs (default):
gh-ci-analyze recent

# Last 20 failures on main branch:
gh-ci-analyze recent --limit 20 --branch main

# Filter to a specific workflow:
gh-ci-analyze recent --repo owner/repo --workflow "CI"
```

### Start the analysis session

```bash
kiro-cli chat "$(cat gh-ci-analyzer/<pr_number>/analyze.prompt.md)"
# or for recent mode:
kiro-cli chat "$(cat gh-ci-analyzer/recent/recent.prompt.md)"
```

Also works with `copilot --interactive` or `gemini --prompt-interactive`.

## What it Generates

### PR mode (`gh-ci-analyze pr`)

Creates `gh-ci-analyzer/<pr_number>/` containing:

- **`analyze.prompt.md`** — AI prompt with all gathered context
- **`pull-request.json`** — PR metadata
- **`pr.diff`** — Full PR diff
- **`base-branch-failures.json`** — Historical base branch failures
- **`<run_id>/`** — Directory per failed run with job logs

### Recent mode (`gh-ci-analyze recent`)

Creates `gh-ci-analyzer/recent/` containing:

- **`recent.prompt.md`** — AI prompt focused on pattern detection
- **`runs-summary.json`** — List of failed runs with metadata
- **`<run_id>/`** — Directory per failed run with job logs
