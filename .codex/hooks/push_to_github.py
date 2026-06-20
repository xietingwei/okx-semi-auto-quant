#!/usr/bin/env python3
"""Push clean, committed Codex work to the current GitHub branch."""

from __future__ import annotations

import fcntl
from pathlib import Path
import subprocess
import sys


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
    )


def main() -> int:
    root_result = git(Path.cwd(), "rev-parse", "--show-toplevel", check=False)
    if root_result.returncode != 0:
        return 0
    root = Path(root_result.stdout.strip())
    git_dir = Path(git(root, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = root / git_dir

    lock_path = git_dir / "codex-github-push.lock"
    log_path = git_dir / "codex-github-push.log"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

        remote = git(root, "remote", "get-url", "origin", check=False)
        remote_url = remote.stdout.strip()
        if remote.returncode != 0 or "github.com" not in remote_url:
            return record(log_path, "skip: origin is not a GitHub remote")

        branch = git(root, "branch", "--show-current").stdout.strip()
        if not branch:
            return record(log_path, "skip: detached HEAD")

        status = git(root, "status", "--porcelain").stdout.strip()
        if status:
            return record(log_path, "skip: working tree has uncommitted changes")

        upstream = git(
            root,
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
            check=False,
        )
        if upstream.returncode == 0:
            ahead = git(
                root,
                "rev-list",
                "--count",
                f"{upstream.stdout.strip()}..HEAD",
            ).stdout.strip()
            if ahead == "0":
                return record(log_path, f"skip: {branch} is already synchronized")

        push = git(root, "push", "--set-upstream", "origin", branch, check=False)
        if push.returncode != 0:
            detail = (push.stderr or push.stdout).strip().splitlines()
            message = detail[-1] if detail else "unknown git push error"
            record(log_path, f"error: {message}")
            print(f"GitHub 自动同步失败：{message}", file=sys.stderr)
            return 1

        return record(log_path, f"pushed: origin/{branch}")


def record(path: Path, message: str) -> int:
    from datetime import datetime, timezone

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as log:
        log.write(f"{timestamp} {message}\n")
    print(f"GitHub hook: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
