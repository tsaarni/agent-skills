from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def run_gh(
    args: list[str],
    *,
    allow_fail: bool = False,
    retries: int = 0,
    retry_delay_sec: float = 1.0,
) -> subprocess.CompletedProcess[str]:
    cmd = ["gh", *args]
    attempts = max(0, retries) + 1

    for attempt in range(1, attempts + 1):
        logger.debug("Running command (attempt %s/%s): %s", attempt, attempts, shlex.join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            return proc

        logger.warning(
            "Command exited with code %s on attempt %s/%s: %s",
            proc.returncode, attempt, attempts, shlex.join(cmd),
        )

        if attempt < attempts:
            time.sleep(retry_delay_sec)
            continue

        if not allow_fail:
            raise RuntimeError(f"Command failed: {shlex.join(cmd)}\n{proc.stderr.strip()}")

        return proc

    raise RuntimeError("unreachable")


def gh_json(args: list[str], *, default: Any, allow_fail: bool = False, retries: int = 0) -> Any:
    proc = run_gh(args, allow_fail=allow_fail, retries=retries)
    if proc.returncode:
        return default
    text = proc.stdout.strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if allow_fail:
            return default
        raise


def write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2)
        f.write("\n")


def sanitize(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return cleaned or "job"


def make_cwd_relative(path: Path) -> str:
    """Return path relative to CWD as a string, or absolute if outside CWD."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)
