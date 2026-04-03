from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunLog:
    path: str
    size_kb: int


@dataclass
class FailedRun:
    run_id: str
    name: str
    logs: list[RunLog] = field(default_factory=list)
    failed_jobs: list[str] = field(default_factory=list)


@dataclass
class TemplateContext:
    repo: str
    pr_number: str
    pr_title: str
    pr_branch: str
    head_owner: str
    base_branch: str
    head_sha: str
    basedir: str
    failed_runs: list[FailedRun] = field(default_factory=list)
