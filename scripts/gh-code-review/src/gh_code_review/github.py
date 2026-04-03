import json
import logging
import subprocess
from typing import Optional



class GitHubError(Exception):
    """Custom exception for GitHub-related errors."""
    pass


def fetch_pr_metadata(repo: str, pr_number: Optional[int] = None, branch_name: Optional[str] = None) -> dict:
    """
    Fetches PR metadata using a single GraphQL search query.
    Can search by PR number or by branch name.
    """
    if "/" not in repo or len(repo.split("/")) != 2:
        raise GitHubError(f"Invalid repository format '{repo}'. Expected 'owner/repo'.")

    if pr_number:
        search_query = f"repo:{repo} is:pr {pr_number}"
    elif branch_name:
        search_query = f"repo:{repo} is:pr is:open head:{branch_name}"
    else:
        raise GitHubError("Either pr_number or branch_name must be provided.")

    query = """
    query($searchQuery: String!) {
      search(query: $searchQuery, type: ISSUE, first: 1) {
        nodes {
          ... on PullRequest {
            number
            url
            title
            body
            baseRefName
            headRefName
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
    }
    """
    cmd = [
        "gh",
        "api",
        "graphql",
        "-f",
        f"query={query}",
        "-F",
        f"searchQuery={search_query}",
    ]
    logging.debug(f"Executing command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    
    if "errors" in data:
        raise GitHubError(f"Error fetching GraphQL data: {data['errors']}")

    nodes = data.get("data", {}).get("search", {}).get("nodes", [])
    if not nodes:
        if pr_number:
            raise GitHubError(f"PR #{pr_number} not found in {repo}.")
        else:
            raise GitHubError(f"No open PR found for branch '{branch_name}' in {repo}.")

    return nodes[0]


def get_repo_name(dir_path: str) -> Optional[str]:
    """Detects the GitHub repository name (owner/repo) for the given directory."""
    try:
        cmd = ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]
        logging.debug(f"Executing command: {' '.join(cmd)} in {dir_path}")
        result = subprocess.run(
            cmd, cwd=dir_path, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except Exception as e:
        logging.debug(f"gh repo view failed: {e}")
        return None
