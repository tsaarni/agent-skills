import subprocess
import json
from gh_code_review.main import main


def test_main_with_local_diff(tmp_path, monkeypatch):
    # Setup: Create a fake repo structure
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    # Initialize git repo for git grep to work
    subprocess.run(["git", "init"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "you@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Your Name"], cwd=repo_dir, check=True)

    # Create some files in the mock repo
    go_file = repo_dir / "app.go"
    go_file.write_text("""package main

func Hello() {
    // some original code
}
""")

    caller_file = repo_dir / "caller.go"
    caller_file.write_text("""package main

func main() {
    Hello()
}
""")

    # Commit the files
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_dir, check=True)

    # Create a unified diff that modifies app.go
    diff_content = """--- app.go
+++ app.go
@@ -2,3 +2,4 @@ func Hello() {

 func Hello() {
+    // added a change in line 4
 }
"""
    diff_file = tmp_path / "test.diff"
    diff_file.write_text(diff_content)

    target_dir = tmp_path / "output"
    target_dir.mkdir()

    # Mock dependency functions in main.py
    monkeypatch.setattr("gh_code_review.main.get_repo_name", lambda dir: "owner/repo")
    monkeypatch.setattr("gh_code_review.main.get_current_branch", lambda dir: "test-branch")
    monkeypatch.setattr("gh_code_review.main.get_local_diff", lambda base, dir: diff_content)
    monkeypatch.setattr("gh_code_review.main.get_local_commits_metadata", lambda base, dir: {
        "number": "local",
        "title": "Local changes on test-branch",
        "url": "local-branch://test-branch",
        "author": {"login": "local-user"},
        "body": "Mock body",
    })

    # Set arguments for main
    test_args = [
        "gh-code-review",
        "--base",
        "main",
        "--dir",
        str(repo_dir),
        "--dest-dir",
        str(target_dir),
    ]
    monkeypatch.setattr("sys.argv", test_args)

    # Run the main function
    main()

    # Verify that the output directory was created and contains the expected files
    output_path = target_dir / "local-test-branch"
    assert output_path.exists()
    assert (output_path / "pr.diff").exists()
    assert (output_path / "metadata.json").exists()
    assert (output_path / "diff-with-function-context.json").exists()
    assert (output_path / "agent-orchestrator.prompt.md").exists()

    with open(output_path / "metadata.json", "r") as f:
        meta = json.load(f)
        assert meta["number"] == "local"
        assert "test-branch" in meta["title"]


def test_main_with_pr_mode(tmp_path, monkeypatch):
    # Setup: Create a fake repo structure
    repo_dir = tmp_path / "repo-pr"
    repo_dir.mkdir()

    # Create some files in the mock repo
    go_file = repo_dir / "app.go"
    go_file.write_text("""package main

func Hello() {
    // some original code
}
""")

    # Create a unified diff that modifies app.go
    diff_content = """--- app.go
+++ app.go
@@ -2,3 +2,4 @@ func Hello() {

 func Hello() {
+    // added a change in line 4
 }
"""

    target_dir = tmp_path / "output-pr"
    target_dir.mkdir()

    # Mock dependency functions in main.py
    monkeypatch.setattr("gh_code_review.main.get_repo_name", lambda dir: "owner/repo")
    monkeypatch.setattr("gh_code_review.main.get_current_branch", lambda dir: "test-branch")
    monkeypatch.setattr("gh_code_review.main.get_local_diff", lambda base, dir: diff_content)

    # Mock PR metadata containing baseRefName
    monkeypatch.setattr("gh_code_review.main.fetch_pr_metadata", lambda repo, pr_number=None, branch_name=None: {
        "number": 123,
        "title": "A PR title",
        "url": "https://github.com/owner/repo/pull/123",
        "author": {"login": "pr-author"},
        "body": "PR description",
        "baseRefName": "develop",
        "headRefName": "test-branch"
    })

    # Set arguments for main (not using --base)
    test_args = [
        "gh-code-review",
        "--repo",
        "owner/repo",
        "--pr",
        "123",
        "--dir",
        str(repo_dir),
        "--dest-dir",
        str(target_dir),
    ]
    monkeypatch.setattr("sys.argv", test_args)

    # Run the main function
    main()

    # Verify that the output directory was created and contains the expected files
    output_path = target_dir / "123"
    assert output_path.exists()
    assert (output_path / "pr.diff").exists()
    assert (output_path / "metadata.json").exists()

    with open(output_path / "metadata.json", "r") as f:
        meta = json.load(f)
        assert meta["number"] == 123
        assert meta["title"] == "A PR title"
        assert meta["baseRefName"] == "develop"
