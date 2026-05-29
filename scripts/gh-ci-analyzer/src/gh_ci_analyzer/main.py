from __future__ import annotations

import argparse
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, PackageLoader, StrictUndefined

from .collector import AnalysisContext, RecentContext, build_recent_template_context, build_template_context, collect, collect_recent
from .gh import run_gh

DEFAULT_TEMPLATE_PACKAGE_PATH = "templates"


def _detect_repo() -> str:
    proc = run_gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    return proc.stdout.strip()


def _detect_pr() -> int:
    proc = run_gh(["pr", "view", "--json", "number", "-q", ".number"])
    return int(proc.stdout.strip())


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Analyzes failed CI workflows for a GitHub repository.",
    )
    p.add_argument("--debug", action="store_true", help="enable debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    # pr subcommand
    pr_p = sub.add_parser("pr", help="Analyze failures for a specific PR")
    pr_p.add_argument("--repo", help="owner/repo (default: auto-detect)")
    pr_p.add_argument("--pr", type=int, help="PR number (default: auto-detect)")
    pr_p.add_argument("--dest-dir", default="gh-ci-analyzer", help="output directory")

    # recent subcommand
    recent_p = sub.add_parser("recent", help="Analyze recent failures across the repo")
    recent_p.add_argument("--repo", help="owner/repo (default: auto-detect)")
    recent_p.add_argument("--limit", type=int, default=10, help="number of failed runs to fetch (default: 10)")
    recent_p.add_argument("--branch", help="filter to a specific branch")
    recent_p.add_argument("--workflow", help="filter to a specific workflow name")
    recent_p.add_argument("--dest-dir", default="gh-ci-analyzer", help="output directory")

    return p.parse_args()


def render_template(template_context, template_name: str, template_path: Path | None = None) -> str:
    if template_path:
        loader = FileSystemLoader(template_path.parent)
        template_name = template_path.name
    else:
        loader = PackageLoader("gh_ci_analyzer", DEFAULT_TEMPLATE_PACKAGE_PATH)

    env = Environment(
        loader=loader,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )
    return env.get_template(template_name).render(ctx=template_context)


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    repo = args.repo or _detect_repo()

    if args.command == "pr":
        pr = args.pr or _detect_pr()
        logging.info("Analyzing %s #%d", repo, pr)

        ctx = AnalysisContext(
            repo=repo,
            pr=str(pr),
            prdir=Path(args.dest_dir) / str(pr),
        )
        collect(ctx)
        template_context = build_template_context(ctx)
        rendered = render_template(template_context, "analyze.prompt.md")
        output_path = ctx.prdir / "analyze.prompt.md"
        output_path.write_text(rendered, encoding="utf-8")

    elif args.command == "recent":
        logging.info("Analyzing recent failures for %s (limit=%d)", repo, args.limit)

        ctx = RecentContext(
            repo=repo,
            limit=args.limit,
            branch=args.branch,
            workflow=args.workflow,
            outdir=Path(args.dest_dir) / "recent",
        )
        collect_recent(ctx)
        template_context = build_recent_template_context(ctx)
        rendered = render_template(template_context, "recent.prompt.md")
        output_path = ctx.outdir / "recent.prompt.md"
        output_path.write_text(rendered, encoding="utf-8")

    print(f"""
==> Context gathering complete. Next-step instructions:

Kiro CLI:
  kiro-cli chat "$(cat {output_path})"

GitHub Copilot CLI:
  copilot --interactive "$(cat {output_path})"

Gemini CLI:
  gemini --prompt-interactive "$(cat {output_path})"

Collected data directory: {output_path.parent}/""")
