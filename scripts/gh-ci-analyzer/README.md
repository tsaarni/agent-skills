# GitHub CI Failure Analyzer

Gathers failed GitHub Actions workflow logs for a Pull Request and produces a prompt document for agent-assisted failure analysis.
Leverages the `gh` CLI to retrieve PR metadata, diffs, logs, and base branch failure history through the GitHub API.

## Prerequisites

- [gh](https://cli.github.com/) CLI, authenticated
- [uv](https://docs.astral.sh/uv/)

## Installation

Install the tool using `uv`:

```bash
uv tool install --editable . 
```

## Usage

1. **Check out the PR branch** you wish to analyze:
   ```bash
   gh pr checkout <pr_number>
   ```

2. **Run the tool** from within the repository:
   ```bash
   gh-ci-analyze
   ```
   The script automatically detects the current repository and PR number. Run `gh-ci-analyze --help` for more options.

3. **Start the analysis** session with an AI assistant:
   ```bash
   kiro-cli chat "$(cat gh-ci-analyzer/<pr_number>/analyze.prompt.md)"
   # or
   copilot --interactive "$(cat gh-ci-analyzer/<pr_number>/analyze.prompt.md)"
   # or
   gemini --prompt-interactive "$(cat gh-ci-analyzer/<pr_number>/analyze.prompt.md)"
   ```

## What it Generates

The tool creates a directory `gh-ci-analyzer/<pr_number>/` containing:

- **`analyze.prompt.md`**: A ready-to-use AI prompt incorporating all gathered context (logs, diffs, etc.).
- **`metadata.json`**: PR metadata.
- **`pr.diff`**: The raw unified diff for the PR.
- **`base-branch-failures.json`**: Historical failure data for the base branch.
- **`<run_id>/`**: Directory for each failed workflow run containing its job logs.
