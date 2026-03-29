import json
import logging
import re
import subprocess
import sys
from typing import Optional


def fetch_pr_diff(repo: str, pr_number: int) -> str:
    """Fetches the unified diff of a GitHub Pull Request using the gh CLI."""
    cmd = ["gh", "pr", "diff", str(pr_number), "--repo", repo]
    logging.debug(f"Executing command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout


def fetch_pr_metadata(repo: str, pr_number: int) -> dict:
    """Fetches PR metadata (title, body, comments, review thread comments) using GitHub GraphQL via gh CLI."""
    owner, name = repo.split("/")
    query = """
    query($owner: String!, $repo: String!, $pr: Int!) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $pr) {
          number
          url
          title
          body
          author { login }
          comments(first: 50) {
            nodes {
              author { login }
              body
            }
          }
          reviewThreads(first: 50) {
            nodes {
              path
              isResolved
              comments(first: 20) {
                nodes {
                  author { login }
                  body
                  diffHunk
                  position
                  originalPosition
                  line
                  startLine
                  originalLine
                  originalStartLine
                }
              }
            }
          }
        }
      }
    }
    """
    cmd = [
        "gh",
        "api",
        "graphql",
        "-F",
        f"owner={owner}",
        "-F",
        f"repo={name}",
        "-F",
        f"pr={pr_number}",
        "-f",
        f"query={query}",
    ]
    logging.debug(f"Executing command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    if "errors" in data:
        logging.error(f"Error fetching GraphQL data: {data['errors']}")
        sys.exit(1)

    pr_data = data.get("data", {}).get("repository", {}).get("pullRequest")
    if pr_data is None:
        logging.error(f"Error: PR #{pr_number} not found in {repo}.")
        sys.exit(1)

    return pr_data


def get_repo_name(dir_path: str) -> Optional[str]:
    """Detects the GitHub repository name (owner/repo) for the given directory."""
    try:
        cmd = ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]
        logging.debug(f"Executing command: {' '.join(cmd)} in {dir_path}")
        result = subprocess.run(
            cmd, cwd=dir_path, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logging.debug(f"gh repo view failed: {e.stderr}")
        return None


def get_current_pr_number(dir_path: str) -> Optional[int]:
    """Detects the PR number associated with the currently checked out branch."""
    # Method 1: gh pr view (most direct)
    try:
        cmd = ["gh", "pr", "view", "--json", "number", "-q", ".number"]
        logging.debug(f"Executing command: {' '.join(cmd)} in {dir_path}")
        result = subprocess.run(
            cmd, cwd=dir_path, capture_output=True, text=True, check=True
        )
        num = result.stdout.strip()
        if num:
            return int(num)
    except Exception as e:
        logging.debug(f"gh pr view failed: {e}")

    # Method 2: gh pr status (sometimes more reliable for current branch)
    try:
        cmd = ["gh", "pr", "status", "--json", "number", "-q", ".currentBranch.number"]
        logging.debug(f"Executing command: {' '.join(cmd)} in {dir_path}")
        result = subprocess.run(
            cmd, cwd=dir_path, capture_output=True, text=True, check=True
        )
        num = result.stdout.strip()
        if num:
            return int(num)
    except Exception as e:
        logging.debug(f"gh pr status failed: {e}")

    # Method 3: gh pr list (search for PR matching current branch)
    try:
        cmd_branch = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
        branch = subprocess.run(
            cmd_branch, cwd=dir_path, capture_output=True, text=True, check=True
        ).stdout.strip()

        cmd_list = [
            "gh",
            "pr",
            "list",
            "--json",
            "number,headRefName",
            "-q",
            f'.[] | select(.headRefName == "{branch}") | .number',
        ]
        logging.debug(f"Executing command: {' '.join(cmd_list)} in {dir_path}")
        result = subprocess.run(
            cmd_list, cwd=dir_path, capture_output=True, text=True, check=True
        )
        num = result.stdout.strip()
        if num:
            return int(num)
    except Exception as e:
        logging.debug(f"gh pr list fallback failed: {e}")

    # Method 4: Parse branch name if it follows 'pr/123' pattern (common for gh pr checkout)
    try:
        cmd = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
        result = subprocess.run(
            cmd, cwd=dir_path, capture_output=True, text=True, check=True
        )
        branch = result.stdout.strip()
        match = re.search(r"pr/(\d+)", branch)
        if match:
            return int(match.group(1))
    except Exception as e:
        logging.debug(f"git branch detection failed: {e}")

    return None
