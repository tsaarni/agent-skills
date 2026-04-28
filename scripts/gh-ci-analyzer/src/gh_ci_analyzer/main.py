from __future__ import annotations

import argparse
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, PackageLoader, StrictUndefined

from .collector import AnalysisContext, build_template_context, collect
from .gh import run_gh

DEFAULT_TEMPLATE_NAME = "analyze.prompt.md"
DEFAULT_TEMPLATE_PACKAGE_PATH = "templates"


def _detect_repo() -> str:
    proc = run_gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    return proc.stdout.strip()


def _detect_pr() -> int:
    proc = run_gh(["pr", "view", "--json", "number", "-q", ".number"])
    return int(proc.stdout.strip())


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Analyzes failed CI workflows for a GitHub PR.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="If --repo or --pr are omitted, they are auto-detected from the current\n"
               "git repository and branch using the gh CLI.",
    )
    p.add_argument("--repo", help="owner/repo, e.g. projectcontour/contour (default: auto-detect from git remote)")
    p.add_argument("--pr", type=int, help="PR number (default: auto-detect from current branch)")
    p.add_argument("--dest-dir", default="gh-ci-analyzer", help="output directory (default: ./gh-ci-analyzer)")
    p.add_argument("--debug", action="store_true", help="enable debug logging")
    return p.parse_args()


def render_template(template_context, template_path: Path | None = None) -> str:
    if template_path:
        template_dir = template_path.parent
        template_name = template_path.name
        loader = FileSystemLoader(template_dir)
    else:
        template_name = DEFAULT_TEMPLATE_NAME
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
    pr = args.pr or _detect_pr()
    logging.info("Analyzing %s #%d", repo, pr)

    ctx = AnalysisContext(
        repo=repo,
        pr=str(pr),
        prdir=Path(args.dest_dir) / str(pr),
    )

    collect(ctx)

    template_context = build_template_context(ctx)
    rendered = render_template(template_context)
    (ctx.prdir / "analyze.prompt.md").write_text(rendered, encoding="utf-8")

    context_path = ctx.prdir / "analyze.prompt.md"
    print(f"""
==> Context gathering complete. Next-step instructions:

Kiro CLI:
  kiro-cli chat "$(cat {context_path})"

GitHub Copilot CLI:

  copilot --interactive "$(cat {context_path})"

Gemini CLI:

  gemini --prompt-interactive "$(cat {context_path})"

Collected data directory: {ctx.prdir}/""")
