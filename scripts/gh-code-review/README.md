# GitHub Code Review Assistant

A tool to generate context-rich prompts for agent code reviews by combining PR metadata, symbol extraction, and repository-wide impact analysis.

## Prerequisites

- [gh](https://cli.github.com/) CLI, authenticated
- [uv](https://docs.astral.sh/uv/)

## Installation

Install the tool using `uv`:

```bash
uv tool install --editable .
```

## Usage

1. **Check out the PR branch** you wish to review:

   ```bash
   gh pr checkout <pr_number>
   ```

2. **Run the tool** from within the repository:

   ```bash
   gh-code-review
   ```

   The script automatically detects the current repository and PR number. Run `gh-code-review --help` for more options.

3. **Start the review** session with an AI assistant:
   ```bash
   kiro-cli chat "$(cat gh-code-review/<pr_number>/agent-orchestrator.prompt.md)"
   # or
   copilot --interactive "$(cat gh-code-review/<pr_number>/agent-orchestrator.prompt.md)"
   # or
   gemini --prompt-interactive "$(cat gh-code-review/<pr_number>/agent-orchestrator.prompt.md)"
   ```

## What it Generates

The tool creates a directory `gh-code-review/<pr_number>/` containing:

- **`agent-orchestrator.prompt.md`**: The orchestrator prompt for a 3-agent review process.
- **`diff-with-function-context.json`**: Structured code context and cross-file identifier usages.
- **`metadata.json`**: PR metadata including comments.
- **`pr.diff`**: The raw unified diff for the PR.

After the agent review session completes, the following files are written under `gh-code-review/<pr_number>/results/`:

- **`impact-analysis.json`**: Blast-radius analysis produced by the "Analyze Impact" subagent. Maps modified public symbols to their external callers across the repository.
- **`review-findings.json`**: Qualitative review comments produced by the "Review Code" subagent. Each finding includes file/line reference, severity, type, description, and evidence.
- **`review-results.md`**: The final report produced by the "Criticize Results" subagent. Structured into four sections: Summary, Findings (grouped by severity), Backwards Compatibility, and Conclusion.

## Development

Run `ruff` linter for python code

```bash
uv run ruff check .
```
