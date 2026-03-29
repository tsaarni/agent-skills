from __future__ import annotations

import json
import logging
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .gh import gh_json, make_cwd_relative, run_gh, sanitize, write_json
from .models import FailedRun, RunLog, TemplateContext

RUN_ID_RE = re.compile(r"actions/runs/(\d+)")
logger = logging.getLogger(__name__)

GRAPHQL_QUERY = """query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      title body headRefName baseRefName headRefOid
      headRepository { owner { login } }
      files(first: 100) { nodes { path additions deletions } }
      commits(last: 1) { nodes { commit {
        checkSuites(first: 100) { nodes {
          conclusion
          workflowRun { databaseId workflow { name } }
          checkRuns(first: 100, filterBy: {conclusions: [FAILURE]}) { nodes {
            name detailsUrl
          } }
        } }
      } } }
    }
  }
}"""

BASE_FAILURES_QUERY = """query($owner: String!, $repo: String!, $branch: String!, $count: Int!) {
  repository(owner: $owner, name: $repo) {
    ref(qualifiedName: $branch) {
      target { ... on Commit {
        history(first: $count) { nodes {
          oid committedDate
          checkSuites(first: 20) { nodes {
            conclusion
            workflowRun { databaseId workflow { name } }
            checkRuns(first: 10, filterBy: {conclusions: [FAILURE]}) { nodes {
              name
            } }
          } }
        } }
      } }
    }
  }
}"""


@dataclass
class AnalysisContext:
    repo: str
    pr: str
    prdir: Path
    max_runs: int = 20
    max_base_failures: int = 15
    log_workers: int = 4
    gh_retries: int = 2
    # Populated during collection
    pr_title: str = ""
    pr_description: str = ""
    pr_branch: str = ""
    head_owner: str = ""
    base_branch: str = ""
    head_sha: str = ""
    changed_files: list[dict] = field(default_factory=list)
    run_map: dict[str, str] = field(default_factory=dict)
    base_failures: list[dict] = field(default_factory=list)


def fetch_pr_data(ctx: AnalysisContext) -> None:
    """Single GraphQL query for PR metadata, check suites, and failed runs."""
    logger.info("==> Fetching PR data for %s#%s", ctx.repo, ctx.pr)
    owner, repo = ctx.repo.split("/", 1)

    data = gh_json(
        ["api", "graphql",
         "-F", f"owner={owner}", "-F", f"repo={repo}", "-F", f"pr={ctx.pr}",
         "-f", f"query={GRAPHQL_QUERY}"],
        default={}, retries=ctx.gh_retries,
    )

    pr = (data.get("data") or {}).get("repository", {}).get("pullRequest") or {}
    if not pr:
        raise RuntimeError("Could not read PR metadata")

    # Extract PR fields
    ctx.pr_title = pr.get("title", "")
    ctx.pr_description = pr.get("body") or ""
    ctx.pr_branch = pr.get("headRefName", "")
    ctx.base_branch = pr.get("baseRefName", "")
    ctx.head_sha = pr.get("headRefOid", "")
    ctx.head_owner = (pr.get("headRepository") or {}).get("owner", {}).get("login") or owner
    ctx.changed_files = (pr.get("files") or {}).get("nodes") or []

    logger.info("    PR branch: %s, head owner: %s, base: %s", ctx.pr_branch, ctx.head_owner, ctx.base_branch)
    logger.info("    HEAD SHA: %s", ctx.head_sha)

    # Write pull-request.json for AI reference
    write_json(ctx.prdir / "pull-request.json", pr)

    # Extract failed runs and delegated IDs from check suites
    commit = ((pr.get("commits") or {}).get("nodes") or [{}])[0].get("commit") or {}
    suites = (commit.get("checkSuites") or {}).get("nodes") or []

    for suite in suites:
        conclusion = (suite.get("conclusion") or "").upper()
        wf_run = suite.get("workflowRun") or {}
        run_id = str(wf_run.get("databaseId") or "")
        wf_name = (wf_run.get("workflow") or {}).get("name", "")

        if conclusion == "FAILURE" and run_id:
            ctx.run_map[run_id] = wf_name or "failed run"

        # Scan failed check runs for delegated workflow IDs
        for cr in ((suite.get("checkRuns") or {}).get("nodes") or []):
            cr_name = cr.get("name") or "delegated check"
            details_url = cr.get("detailsUrl") or ""
            for did in RUN_ID_RE.findall(details_url):
                if did not in ctx.run_map:
                    ctx.run_map[did] = f"{cr_name} (delegated)"
                    logger.info("    Found delegated run %s from check '%s'", did, cr_name)


def fetch_pr_diff(ctx: AnalysisContext) -> None:
    logger.info("==> Fetching PR diff")
    diff = run_gh(
        ["pr", "diff", ctx.pr, "--repo", ctx.repo],
        allow_fail=True, retries=ctx.gh_retries,
    )
    (ctx.prdir / "pr.diff").write_text(diff.stdout, encoding="utf-8")


def fetch_base_failures(ctx: AnalysisContext) -> None:
    if not ctx.base_branch:
        return
    logger.info("==> Checking base branch failure history")
    owner, repo = ctx.repo.split("/", 1)

    data = gh_json(
        ["api", "graphql",
         "-F", f"owner={owner}", "-F", f"repo={repo}",
         "-F", f"branch=refs/heads/{ctx.base_branch}",
         "-F", f"count={ctx.max_base_failures}",
         "-f", f"query={BASE_FAILURES_QUERY}"],
        default={}, allow_fail=True, retries=ctx.gh_retries,
    )

    commits = (
        ((((data.get("data") or {}).get("repository") or {}).get("ref") or {})
         .get("target") or {}).get("history") or {}
    ).get("nodes") or []

    # Extract failed runs with job names from commit check suites
    failures = []
    for commit in commits:
        for suite in ((commit.get("checkSuites") or {}).get("nodes") or []):
            if (suite.get("conclusion") or "").upper() != "FAILURE":
                continue
            wf_run = suite.get("workflowRun") or {}
            failed_checks = [cr.get("name") for cr in ((suite.get("checkRuns") or {}).get("nodes") or [])]
            failures.append({
                "workflow": ((wf_run.get("workflow") or {}).get("name") or "unknown"),
                "runId": wf_run.get("databaseId"),
                "committedDate": commit.get("committedDate"),
                "commit": commit.get("oid", "")[:12],
                "failedJobs": failed_checks,
            })

    ctx.base_failures = failures
    write_json(ctx.prdir / "base-branch-failures.json", failures)


def fallback_discover_by_branch(ctx: AnalysisContext) -> None:
    """Fallback: if GraphQL found no failed runs, try gh run list by branch."""
    if ctx.run_map or not ctx.pr_branch:
        return
    logger.info("==> Fallback: discovering failed runs by branch")
    runs = gh_json(
        ["run", "list", "--repo", ctx.repo, "--branch", ctx.pr_branch,
         "--status", "failure", "--json", "databaseId,name,headSha",
         "--limit", str(ctx.max_runs)],
        default=[], allow_fail=True, retries=ctx.gh_retries,
    )
    if not isinstance(runs, list):
        return
    expected = ctx.head_sha
    for run in runs:
        if not isinstance(run, dict):
            continue
        if expected and str(run.get("headSha") or "") != expected:
            continue
        run_id = str(run.get("databaseId") or "")
        if run_id:
            ctx.run_map[run_id] = str(run.get("name") or "failed run")


def download_failed_job_logs(ctx: AnalysisContext) -> None:
    logger.info("==> Downloading per-job logs")

    def download_one(run_id: str) -> None:
        run_dir = ctx.prdir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        run_view = gh_json(
            ["run", "view", run_id, "--repo", ctx.repo, "--json", "jobs"],
            default={}, allow_fail=True, retries=ctx.gh_retries,
        )
        jobs = (run_view.get("jobs") or []) if isinstance(run_view, dict) else []
        write_json(run_dir / "jobs.json", jobs)
        failed_jobs = [j for j in jobs if isinstance(j, dict) and j.get("conclusion") == "failure"]

        if not failed_jobs:
            logger.info("    Run %s: no failed jobs, skipping", run_id)
            return

        for job in failed_jobs:
            job_id = str(job.get("databaseId") or "")
            job_name = str(job.get("name") or "job")
            if not job_id:
                continue
            logger.info("    Run %s, job '%s' (%s)", run_id, job_name, job_id)
            log = run_gh(
                ["run", "view", run_id, "--repo", ctx.repo, "--job", job_id, "--log-failed"],
                allow_fail=True, retries=ctx.gh_retries,
            )
            text = log.stdout
            if log.returncode != 0 and log.stderr:
                text = (text + "\n" + log.stderr).strip() + "\n"
            (run_dir / f"{sanitize(job_name)}.txt").write_text(text, encoding="utf-8")

    run_ids = sorted(ctx.run_map, key=int)
    if ctx.log_workers <= 1 or len(run_ids) <= 1:
        for rid in run_ids:
            download_one(rid)
        return

    with ThreadPoolExecutor(max_workers=ctx.log_workers) as pool:
        futures = {pool.submit(download_one, rid): rid for rid in run_ids}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                logger.warning("    Run %s: log download failed: %s", futures[future], exc)


def collect(ctx: AnalysisContext) -> None:
    """Run all data collection steps, populating ctx in place."""
    shutil.rmtree(ctx.prdir, ignore_errors=True)
    ctx.prdir.mkdir(parents=True, exist_ok=True)

    fetch_pr_data(ctx)
    fetch_pr_diff(ctx)
    fetch_base_failures(ctx)
    fallback_discover_by_branch(ctx)

    if ctx.run_map:
        logger.info("==> Total failed runs to process: %s", len(ctx.run_map))
        for run_id in sorted(ctx.run_map, key=int):
            logger.info("    %s: %s", run_id, ctx.run_map[run_id])
        download_failed_job_logs(ctx)
    else:
        logger.info("No failed runs found.")


def build_template_context(ctx: AnalysisContext) -> TemplateContext:
    failed_runs: list[FailedRun] = []
    for run_id in sorted(ctx.run_map, key=int):
        run_dir = ctx.prdir / run_id
        # Find logs: one .txt file per failed job
        log_files = sorted(p for p in run_dir.glob("*.txt") if p.stat().st_size > 0)
        logs = [
            RunLog(path=make_cwd_relative(p), size_kb=p.stat().st_size // 1024)
            for p in log_files
        ]
        # Read failed job names from saved jobs.json
        failed_jobs: list[str] = []
        jobs_file = run_dir / "jobs.json"
        if jobs_file.exists():
            try:
                jobs = json.loads(jobs_file.read_text(encoding="utf-8"))
                failed_jobs = [
                    str(j.get("name") or "unknown")
                    for j in jobs if isinstance(j, dict) and j.get("conclusion") == "failure"
                ]
            except (json.JSONDecodeError, OSError):
                pass
        failed_runs.append(FailedRun(run_id=run_id, name=ctx.run_map[run_id], logs=logs, failed_jobs=failed_jobs))

    return TemplateContext(
        repo=ctx.repo,
        pr_number=ctx.pr,
        pr_title=ctx.pr_title,
        pr_branch=ctx.pr_branch,
        head_owner=ctx.head_owner,
        base_branch=ctx.base_branch,
        head_sha=ctx.head_sha,
        basedir=make_cwd_relative(ctx.prdir.parent),
        failed_runs=failed_runs,
    )
