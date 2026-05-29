from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunLog:
    path: str
    size_bytes: int

    @property
    def size_display(self) -> str:
        if self.size_bytes < 1024:
            return f"{self.size_bytes}B"
        return f"{self.size_bytes // 1024}KB"


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


@dataclass
class RecentFailedRun:
    run_id: str
    workflow_name: str
    display_title: str
    branch: str
    head_sha: str
    created_at: str
    event: str
    url: str
    logs: list[RunLog] = field(default_factory=list)
    failed_jobs: list[str] = field(default_factory=list)


@dataclass
class RecentTemplateContext:
    repo: str
    limit: int
    branch_filter: str | None
    workflow_filter: str | None
    basedir: str
    failed_runs: list[RecentFailedRun] = field(default_factory=list)
