import json
import pytest
from unittest.mock import patch, MagicMock
from gh_code_review.github import (
    fetch_pr_metadata,
    get_repo_name,
    GitHubError,
)

def test_fetch_pr_metadata_invalid_repo():
    with pytest.raises(GitHubError, match="Invalid repository format"):
        fetch_pr_metadata("invalid-repo", 123)

def test_fetch_pr_metadata_success_by_number():
    mock_data = {
        "data": {
            "search": {
                "nodes": [{
                    "number": 123,
                    "title": "PR Title",
                    "url": "http://github.com/owner/repo/pull/123",
                    "body": "PR Body",
                    "author": {"login": "author-user"},
                    "baseRefName": "main",
                    "headRefName": "test-branch",
                    "comments": {"nodes": []},
                    "reviewThreads": {"nodes": []}
                }]
            }
        }
    }
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=json.dumps(mock_data), returncode=0)
        metadata = fetch_pr_metadata("owner/repo", pr_number=123)
        assert metadata["number"] == 123
        assert metadata["title"] == "PR Title"
        assert "is:pr 123" in mock_run.call_args[0][0][-1]

def test_fetch_pr_metadata_success_by_branch():
    mock_data = {
        "data": {
            "search": {
                "nodes": [{
                    "number": 456,
                    "title": "Branch PR",
                    "url": "http://github.com/owner/repo/pull/456",
                    "body": "PR Body",
                    "author": {"login": "author-user"},
                    "baseRefName": "main",
                    "headRefName": "feat/test",
                    "comments": {"nodes": []},
                    "reviewThreads": {"nodes": []}
                }]
            }
        }
    }
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=json.dumps(mock_data), returncode=0)
        metadata = fetch_pr_metadata("owner/repo", branch_name="feat/test")
        assert metadata["number"] == 456
        assert "head:feat/test" in mock_run.call_args[0][0][-1]

def test_fetch_pr_metadata_not_found():
    mock_data = {
        "data": {
            "search": {
                "nodes": []
            }
        }
    }
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=json.dumps(mock_data), returncode=0)
        with pytest.raises(GitHubError, match="PR #123 not found in owner/repo"):
            fetch_pr_metadata("owner/repo", pr_number=123)

def test_get_repo_name_success():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="owner/repo\n", returncode=0)
        repo = get_repo_name("/mock/repo")
        assert repo == "owner/repo"
