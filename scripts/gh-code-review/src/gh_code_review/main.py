import sys
import os
import json
import argparse
import logging
import subprocess

from gh_code_review.github import (
    fetch_pr_diff,
    fetch_pr_metadata,
    get_repo_name,
    get_current_pr_number,
)
from gh_code_review.diff import parse_diff
from gh_code_review.context import ReviewContext
from gh_code_review.analyzers.go import GoAnalyzer
from gh_code_review.analyzers.python import PythonAnalyzer
from gh_code_review.analyzers.java import JavaAnalyzer
from gh_code_review.analyzers.cpp import CppAnalyzer


def main():
    parser_arg = argparse.ArgumentParser(
        description="Deterministic Code Review Assistant"
    )
    parser_arg.add_argument(
        "--repo", help="GitHub repository (e.g., owner/repo). Auto-detected if omitted."
    )
    parser_arg.add_argument(
        "--pr",
        type=int,
        help="Pull Request number. Auto-detected from current branch if omitted.",
    )
    parser_arg.add_argument("--diff", help="Path to a local unified diff file")
    parser_arg.add_argument(
        "--dir", default=".", help="Path to the local repository clone"
    )
    parser_arg.add_argument(
        "--dest-dir",
        dest="dest_dir",
        default="gh-code-review",
        help="Directory to output the review context files (default: gh-code-review)",
    )
    parser_arg.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser_arg.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(message)s")

    analyzers = [
        GoAnalyzer(),
        PythonAnalyzer(),
        JavaAnalyzer(),
        CppAnalyzer(),
    ]

    # Automatic detection
    detected_repo = get_repo_name(args.dir)
    current_pr = get_current_pr_number(args.dir)

    target_repo = args.repo or detected_repo
    target_pr = args.pr or current_pr

    if args.diff:
        logging.info(f"Reading local diff file: {args.diff}...")
        with open(args.diff, "r", encoding="utf-8") as f:
            diff_content = f.read()
        pr_id = os.path.basename(args.diff).replace(".diff", "")
        metadata = {
            "number": pr_id,
            "title": f"Local Diff: {os.path.basename(args.diff)}",
            "url": "local-file://" + os.path.abspath(args.diff),
            "author": {"login": "local-user"},
            "body": "This review was generated from a local diff file.",
        }
    elif target_repo and target_pr:
        if current_pr != target_pr:
            logging.error(f"Error: Current branch is not PR #{target_pr}.")
            logging.info(f"Please run: gh pr checkout {target_pr}")
            sys.exit(1)

        logging.info(f"Fetching data for PR #{target_pr} from {target_repo}...")
        diff_content = fetch_pr_diff(target_repo, target_pr)
        metadata = fetch_pr_metadata(target_repo, target_pr)
        pr_id = str(target_pr)
    elif target_repo:
        logging.error(f"Error: Could not detect PR for repository '{target_repo}'.")
        logging.info("Please checkout a PR branch or provide --pr <number>.")
        sys.exit(1)
    else:
        logging.error(
            "Error: Could not detect repository. Provide --repo or run from within a GitHub repository."
        )
        sys.exit(1)

    output_dir = os.path.abspath(os.path.join(args.dest_dir, pr_id))
    os.makedirs(output_dir, exist_ok=True)

    changed_files = parse_diff(diff_content, analyzers)

    # Exclude auto-generated files
    filtered_files = {}
    for f_path, lines in changed_files.items():
        if (
            "zz_generated" in f_path
            or f_path.endswith(".pb.go")
            or f_path.endswith("_mock.go")
        ):
            continue
        filtered_files[f_path] = lines
    changed_files = filtered_files

    if not changed_files:
        logging.info("No supported modified files found.")
        sys.exit(0)

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

    context = ReviewContext(
        basedir=os.path.relpath(args.dest_dir, os.getcwd()),
        metadata=metadata,
        metadata_file=metadata_file,
    )

    # Detect repository root if not provided
    if args.dir == ".":
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=True,
            )
            args.dir = res.stdout.strip()
        except subprocess.CalledProcessError:
            pass

    identifiers_by_analyzer = {}
    ident_to_source_file = {}

    for file_path, lines in changed_files.items():
        analyzer = next(a for a in analyzers if a.supports_file(file_path))
        extracted = analyzer.extract_context(os.path.join(args.dir, file_path), lines)

        context.changed_files.append(
            {
                "path": file_path,
                "lines": list(lines),
                "ranges": extracted.ranges,
                "identifiers": list(extracted.identifiers),
            }
        )

        if extracted.identifiers:
            if analyzer not in identifiers_by_analyzer:
                identifiers_by_analyzer[analyzer] = set()
            identifiers_by_analyzer[analyzer].update(extracted.identifiers)

            for ident in extracted.identifiers:
                if ident not in ident_to_source_file:
                    ident_to_source_file[ident] = set()
                ident_to_source_file[ident].add(file_path)

    for analyzer, identifiers in identifiers_by_analyzer.items():
        logging.info(f"Scanning repository for {len(identifiers)} identifiers...")
        # Scan for all identifiers at once
        all_usages = analyzer.scan_for_usages(args.dir, identifiers, exclude_file=None)

        for ident, usages in all_usages.items():
            if usages:
                source_files = ident_to_source_file.get(ident, set())
                for source_file in source_files:
                    # Filter out self-usages
                    filtered_usages = [u for u in usages if u.file_path != source_file]
                    if filtered_usages:
                        context.impact_scope.append(
                            {
                                "modified_file": source_file,
                                "identifier": ident,
                                "usages": filtered_usages,
                            }
                        )

    package_root = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(package_root))
    template_dir = os.path.join(project_root, "templates")

    context_xml = context.render(template_dir, "context.xml")
    context_xml_path = os.path.join(output_dir, "context.xml")
    with open(context_xml_path, "w", encoding="utf-8") as f:
        f.write(context_xml)
    written_files.append(context_xml_path)

    context.context_file = "context.xml"

    rendered_markdown = context.render(template_dir, "review.prompt.md")

    review_file_path = os.path.join(output_dir, "review.prompt.md")
    with open(review_file_path, "w", encoding="utf-8") as f:
        f.write(rendered_markdown)
    written_files.append(review_file_path)

    rel_output_dir = os.path.relpath(output_dir, os.getcwd())
    print(f"\nSuccess! Review context saved to: {rel_output_dir}")
    for fpath in written_files:
        print(f"  - {os.path.relpath(fpath, os.getcwd())}")

    rel_review_path = os.path.relpath(review_file_path, os.getcwd())
    print("\nTo perform the code review, run one of the following commands:\n")
    print(f'  kiro-cli chat "$(cat {rel_review_path})"')
    print(f'  copilot --interactive "$(cat {rel_review_path})"')
    print(f'  gemini --prompt-interactive "$(cat {rel_review_path})"')


if __name__ == "__main__":
    main()
