from gh_code_review.main import main


def test_main_with_local_diff(tmp_path, monkeypatch):
    # Setup: Create a fake repo structure
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

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

    # Create a unified diff that modifies app.go
    # Note: GoAnalyzer uses tree-sitter, so it needs valid Go code.
    # The diff should point to lines that exist.
    diff_content = """--- app.go
+++ app.go
@@ -2,3 +2,4 @@
 
 func Hello() {
+    // added a change in line 4
 }
"""
    diff_file = tmp_path / "test.diff"
    diff_file.write_text(diff_content)

    target_dir = tmp_path / "output"
    target_dir.mkdir()

    # Mock dependency functions in main.py
    # get_repo_name and get_current_pr_number are imported into gh_code_review.main
    monkeypatch.setattr("gh_code_review.main.get_repo_name", lambda dir: "owner/repo")
    monkeypatch.setattr("gh_code_review.main.get_current_pr_number", lambda dir: 123)

    # Set arguments for main
    test_args = [
        "gh-code-review",
        "--diff",
        str(diff_file),
        "--dir",
        str(repo_dir),
        "--target",
        str(target_dir),
    ]
    monkeypatch.setattr("sys.argv", test_args)

    # Run the main function
    main()

    # The output directory is created based on diff filename: test
    pr_id = "test"
    output_path = target_dir / pr_id

    assert output_path.exists(), f"Output directory {output_path} was not created"
    assert (output_path / "pr.diff").exists()
    assert (output_path / "context.xml").exists()
    assert (output_path / "review.prompt.md").exists()

    # Verify content of context.xml contains our identifier
    context_content = (output_path / "context.xml").read_text()
    assert "Hello" in context_content
    # Impact scope should include caller.go
    assert "caller.go" in context_content
    assert "<files>" in context_content
    assert "</files>" in context_content
