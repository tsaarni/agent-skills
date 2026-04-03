import subprocess
import logging


def get_merge_base(base_ref: str, dir_path: str) -> str:
    """Finds the best common ancestor between a base branch and HEAD."""
    cmd = ["git", "merge-base", base_ref, "HEAD"]
    logging.debug(f"Executing command: {' '.join(cmd)} in {dir_path}")
    result = subprocess.run(
        cmd, cwd=dir_path, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def get_local_diff(base_ref: str, dir_path: str) -> str:
    """Gets the diff of all changes (commits + staged + unstaged) since the merge base."""
    merge_base = get_merge_base(base_ref, dir_path)
    cmd = ["git", "diff", "-W", merge_base]
    logging.debug(f"Executing command: {' '.join(cmd)} in {dir_path}")
    result = subprocess.run(
        cmd, cwd=dir_path, capture_output=True, text=True, check=True
    )
    return result.stdout


def get_current_branch(dir_path: str) -> str:
    """Gets the name of the current git branch."""
    cmd = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
    logging.debug(f"Executing command: {' '.join(cmd)} in {dir_path}")
    result = subprocess.run(
        cmd, cwd=dir_path, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def get_local_commits_metadata(base_ref: str, dir_path: str) -> dict:
    """Synthesizes metadata from local commits since the merge base."""
    merge_base = get_merge_base(base_ref, dir_path)
    
    # Get all commit messages
    cmd = ["git", "log", f"{merge_base}..HEAD", "--format=%s%n%n%b%n---"]
    logging.debug(f"Executing command: {' '.join(cmd)} in {dir_path}")
    result = subprocess.run(
        cmd, cwd=dir_path, capture_output=True, text=True, check=True
    )
    log_output = result.stdout.strip()

    # Check if there are any uncommitted changes (staged or unstaged)
    diff_cmd = ["git", "diff", merge_base]
    diff_result = subprocess.run(
        diff_cmd, cwd=dir_path, capture_output=True, text=True, check=True
    )
    has_diff = bool(diff_result.stdout.strip())

    branch_name = get_current_branch(dir_path)
    
    title = f"Local changes on {branch_name}"
    body = "Review of local commits, staged, and unstaged changes.\n\n"
    
    if log_output:
        body += "### Commit Messages:\n" + log_output
    elif has_diff:
        body += "Reviewing uncommitted local changes and staged files (no new commits found since merge base)."
    else:
        body += "No local changes or commits found since merge base."

    # Try to get author
    author_login = "local-user"
    try:
        author_cmd = ["git", "config", "user.name"]
        author_result = subprocess.run(
            author_cmd, cwd=dir_path, capture_output=True, text=True, check=True
        )
        if author_result.stdout.strip():
            author_login = author_result.stdout.strip()
    except subprocess.CalledProcessError:
        pass

    return {
        "number": "local",
        "title": title,
        "url": f"local-branch://{branch_name}",
        "author": {"login": author_login},
        "body": body,
    }
