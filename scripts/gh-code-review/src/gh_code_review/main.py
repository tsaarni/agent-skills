import sys
import os
import json
import argparse
import logging
import subprocess

from gh_code_review.github import (
    fetch_pr_metadata,
    get_repo_name,
    GitHubError,
)
from gh_code_review.git import (
    get_local_diff,
    get_local_commits_metadata,
    get_current_branch,
)
from gh_code_review.context import ReviewContext
from gh_code_review.extractor import extract_context_from_diff


def parse_args():
    parser = argparse.ArgumentParser(description="Deterministic Code Review Assistant")
    parser.add_argument(
        "--repo", help="GitHub repository (e.g., owner/repo). Auto-detected if omitted."
    )
    parser.add_argument(
        "--pr",
        type=int,
        help="Pull Request number. Auto-detected from current branch if omitted.",
    )
    parser.add_argument(
        "--base",
        help="Base branch to compare against for local code review (e.g., 'main'). Enables local mode.",
    )
    parser.add_argument(
        "--dir", default=".", help="Path to the local repository clone"
    )
    parser.add_argument(
        "--dest-dir",
        dest="dest_dir",
        default="gh-code-review",
        help="Directory to output the review context files (default: gh-code-review)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def get_repo_root(provided_dir):
    if provided_dir == ".":
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=True,
            )
            return res.stdout.strip()
        except subprocess.CalledProcessError:
            pass
    return provided_dir


def get_review_data(args, target_repo, current_branch):
    """Determine review mode and fetch initial data."""
    if args.base:
        logging.info(f"Running in local review mode against base '{args.base}'...")
        base_ref = args.base
        pr_id = f"local-{current_branch}"

        # Try to enrich with PR metadata if available
        if target_repo:
            logging.info(f"Checking for PR matching branch '{current_branch}' or PR #{args.pr}...")
            try:
                metadata = fetch_pr_metadata(target_repo, pr_number=args.pr, branch_name=current_branch)
            except Exception as e:
                logging.warning(
                    f"Failed to fetch PR metadata, falling back to local commits: {e}"
                )
                metadata = get_local_commits_metadata(args.base, args.dir)
        else:
            metadata = get_local_commits_metadata(args.base, args.dir)

        diff_content = get_local_diff(base_ref, args.dir)
        return pr_id, metadata, diff_content

    if target_repo:
        logging.info("Searching for PR...")
        try:
            metadata = fetch_pr_metadata(target_repo, pr_number=args.pr, branch_name=current_branch)
        except GitHubError as e:
            logging.error(f"Error: {e}")
            if not args.pr and args.dir == ".":
                 logging.info("Please checkout a PR branch, provide --pr <number>, or use --base <ref> for local review.")
            sys.exit(1)

        base_ref = metadata.get("baseRefName", "main")
        logging.info(f"Found PR #{metadata['number']}. Using local diff against base '{base_ref}'...")
        diff_content = get_local_diff(base_ref, args.dir)
        pr_id = str(metadata["number"])
        return pr_id, metadata, diff_content

    logging.error(
        "Error: Could not detect repository. Provide --repo, run from within a GitHub repository, or use --base <ref> for local review."
    )
    sys.exit(1)


def prepare_output_dir(dest_dir, pr_id):
    if os.path.exists(dest_dir) and not os.path.isdir(dest_dir):
        logging.error(
            f"Error: Output destination '{dest_dir}' exists but is not a directory."
        )
        if dest_dir == "gh-code-review" and os.path.isfile(dest_dir):
            logging.error(
                "This often happens when running from the source directory where the 'gh-code-review' script exists."
            )
            logging.error(
                "Please use --dest-dir to specify a different output directory."
            )
        sys.exit(1)

    output_dir = os.path.abspath(os.path.join(dest_dir, pr_id))
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, "results"), exist_ok=True)
    return output_dir


def write_results(output_dir, diff_content, metadata, contexts, dest_dir, template_dir):
    pr_diff_path = os.path.join(output_dir, "pr.diff")
    with open(pr_diff_path, "w", encoding="utf-8") as f:
        f.write(diff_content.strip())
    written_files = [pr_diff_path]

    metadata_file = None
    if metadata:
        metadata_file = "metadata.json"
        metadata_path = os.path.join(output_dir, metadata_file)
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
        written_files.append(metadata_path)

    changed_files_context = []
    for file_path, extracted in contexts.items():
        changed_files_context.append(
            {
                "path": file_path,
                "ranges": extracted.ranges,
            }
        )

    context = ReviewContext(
        basedir=os.path.relpath(dest_dir, os.getcwd()),
        metadata=metadata,
        metadata_file=metadata_file,
        changed_files=changed_files_context,
    )

    # Render diff-with-function-context.json
    diff_with_function_context_json = context.render(template_dir, "diff-with-function-context.json")
    diff_with_function_context_json_path = os.path.join(output_dir, "diff-with-function-context.json")
    with open(diff_with_function_context_json_path, "w", encoding="utf-8") as f:
        f.write(diff_with_function_context_json)
    written_files.append(diff_with_function_context_json_path)

    # Render agentic orchestrator prompt
    agent_orchestrator = context.render(template_dir, "agent-orchestrator.prompt.md")
    agent_orchestrator_path = os.path.join(output_dir, "agent-orchestrator.prompt.md")
    with open(agent_orchestrator_path, "w", encoding="utf-8") as f:
        f.write(agent_orchestrator)
    written_files.append(agent_orchestrator_path)

    return written_files, agent_orchestrator_path


def main():
    args = parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(message)s")

    # Automatic detection
    detected_repo = get_repo_name(args.dir)
    target_repo = args.repo or detected_repo
    current_branch = get_current_branch(args.dir)

    pr_id, metadata, diff_content = get_review_data(
        args, target_repo, current_branch
    )

    output_dir = prepare_output_dir(args.dest_dir, pr_id)

    # Detect repository root if not provided
    args.dir = get_repo_root(args.dir)

    contexts = extract_context_from_diff(diff_content)

    if not contexts:
        logging.info("No supported modified files found.")
        sys.exit(0)

    package_root = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(package_root))
    template_dir = os.path.join(project_root, "templates")

    written_files, agent_orchestrator_path = write_results(
        output_dir, diff_content, metadata, contexts, args.dest_dir, template_dir
    )

    rel_output_dir = os.path.relpath(output_dir, os.getcwd())
    print(f"\nSuccess! Review context saved to: {rel_output_dir}")
    for fpath in written_files:
        print(f"  - {os.path.relpath(fpath, os.getcwd())}")

    rel_orchestrator_path = os.path.relpath(agent_orchestrator_path, os.getcwd())
    print("\nTo start the review, run:")
    print(f'  gemini --prompt-interactive "$(cat {rel_orchestrator_path})"')
    print(f'  kiro-cli chat "$(cat {rel_orchestrator_path})"')
    print(f'  copilot --interactive "$(cat {rel_orchestrator_path})"')


if __name__ == "__main__":
    main()
