from unittest.mock import patch, MagicMock
from gh_code_review.git import (
    get_merge_base,
    get_local_diff,
    get_current_branch,
    get_local_commits_metadata,
)

def test_get_merge_base():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="abcdef\n", returncode=0)
        base = get_merge_base("main", "/mock/repo")
        assert base == "abcdef"
        mock_run.assert_called_once_with(
            ["git", "merge-base", "main", "HEAD"],
            cwd="/mock/repo",
            capture_output=True,
            text=True,
            check=True
        )

def test_get_local_diff():
    with patch("gh_code_review.git.get_merge_base") as mock_base:
        mock_base.return_value = "abcdef"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="diff content", returncode=0)
            diff = get_local_diff("main", "/mock/repo")
            assert diff == "diff content"
            mock_run.assert_called_once_with(
                ["git", "diff", "-W", "abcdef"],
                cwd="/mock/repo",
                capture_output=True,
                text=True,
                check=True
            )

def test_get_current_branch():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="feat/test\n", returncode=0)
        branch = get_current_branch("/mock/repo")
        assert branch == "feat/test"

def test_get_local_commits_metadata():
    with patch("gh_code_review.git.get_merge_base") as mock_base:
        mock_base.return_value = "abcdef"
        with patch("gh_code_review.git.get_current_branch") as mock_branch:
            mock_branch.return_value = "feat/test"
            with patch("subprocess.run") as mock_run:
                # First call for git log, second for git diff, third for git config
                mock_run.side_effect = [
                    MagicMock(stdout="Commit 1\n\nBody 1\n---", returncode=0),
                    MagicMock(stdout="", returncode=0),
                    MagicMock(stdout="Test User\n", returncode=0),
                ]
                metadata = get_local_commits_metadata("main", "/mock/repo")
                assert metadata["number"] == "local"
                assert "feat/test" in metadata["title"]
                assert "Commit 1" in metadata["body"]
                assert metadata["author"]["login"] == "Test User"
