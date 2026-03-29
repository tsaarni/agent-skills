# GitHub CI Failure Analyzer

Gathers failed GitHub Actions workflow logs for a Pull Request and produces a prompt document for AI-assisted failure analysis.
Leverages the `gh` CLI to retrieve PR metadata, diffs, logs, and base branch failure history through the GitHub API.


## Prerequisites

- [gh](https://cli.github.com/) CLI, authenticated
- [uv](https://docs.astral.sh/uv/) (for running the script)

## Usage

1. Check out the PR branch:
   ```bash
   gh pr checkout 1234
   ```
2. Run the script to download data
   ```bash
   <path>/analyze
   ```
   The script automatically detects the current GitHub repository and PR number, then fetches relevant data for analysis.
   You can also provide the repository and PR explicitly. Run `analyze --help` to view available options.
   It creates a `gh-ci-analyzer/<pr>` directory with an `analyze.prompt.md` file and supporting data files.

3. Feed the generated `analyze.prompt.md` to an AI assistant:

   ```bash
   kiro-cli chat "$(cat gh-ci-analyzer/<pr>/analyze.prompt.md)"

   # or
   copilot --interactive "$(cat gh-ci-analyzer/<pr>/analyze.prompt.md)"

   # or
   gemini --prompt-interactive "$(cat gh-ci-analyzer/<pr>/analyze.prompt.md)"
   ```
