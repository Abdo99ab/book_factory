"""Which build is this? Short git SHA + branch, shown in the footer of every page.

Read once at startup via ``git rev-parse``; ``BUILD_SHA`` / ``BUILD_BRANCH`` env vars are the fallback
for deployments without a ``.git`` directory (so the operator can always tell which code made a book).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _git(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def get_build_info(cwd: Path | None = None) -> dict:
    """``{"sha", "branch", "label"}``; label is ``Build <short SHA> · <branch>``."""
    cwd = Path(cwd or Path(__file__).resolve().parent.parent)
    sha = _git(["rev-parse", "--short", "HEAD"], cwd) or os.getenv("BUILD_SHA", "").strip()
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd) or os.getenv("BUILD_BRANCH", "").strip()
    if branch == "HEAD":  # detached
        branch = os.getenv("BUILD_BRANCH", "").strip() or "detached"
    sha = sha or "unknown"
    branch = branch or "unknown"
    return {"sha": sha, "branch": branch, "label": f"Build {sha} \u00b7 {branch}"}
