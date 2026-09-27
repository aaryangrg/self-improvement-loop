from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PullRequestInfo:
    number: int
    url: str
    branch: str


class GitOps:
    def __init__(self, repo_root: Path = Path(".")) -> None:
        self.repo_root = repo_root

    def create_branch(self, branch: str, base: str = "main") -> None:
        self.ensure_clean_worktree()
        self.switch_branch(base)
        self._run(["git", "switch", "-c", branch, base])

    def switch_branch(self, branch: str) -> None:
        if self.current_branch() != branch:
            self._run(["git", "switch", branch])

    def current_branch(self) -> str:
        return self._run(["git", "branch", "--show-current"]).strip()

    def ensure_clean_worktree(self) -> None:
        status = self._run(["git", "status", "--porcelain"])
        if status.strip():
            raise RuntimeError(
                "worktree must be clean before creating a PR branch; commit or stash changes first"
            )

    def push_branch(self, branch: str, set_upstream: bool = True) -> None:
        self.switch_branch(branch)
        args = ["git", "push"]
        if set_upstream:
            args.extend(["-u", "origin", branch])
        else:
            args.extend(["origin", branch])
        self._run(args)

    def open_draft_pr(
        self,
        branch: str,
        base: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> PullRequestInfo:
        self.switch_branch(branch)
        args = [
            "gh",
            "pr",
            "create",
            "--base",
            base,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ]
        if draft:
            args.append("--draft")
        output = self._run(args).strip()
        url = _extract_pr_url(output)
        view_output = self._run(["gh", "pr", "view", url or branch, "--json", "number,url"])
        payload = json.loads(view_output)
        return PullRequestInfo(
            number=int(payload["number"]),
            url=str(payload["url"]),
            branch=branch,
        )

    def commit_paths(
        self,
        paths: tuple[str, ...],
        message: str,
    ) -> str:
        if not paths:
            raise ValueError("at least one path is required")
        self._run(["git", "add", "--", *paths])
        status = self._run(["git", "status", "--porcelain", "--", *paths])
        if not status.strip():
            raise RuntimeError("no candidate changes to commit")
        self._run(["git", "commit", "-m", message])
        return self.current_commit()

    def current_commit(self) -> str:
        return self._run(["git", "rev-parse", "HEAD"]).strip()

    def _run(self, args: list[str]) -> str:
        proc = subprocess.run(
            args,
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            excerpt = ((proc.stderr or "") + (proc.stdout or "")).strip()
            raise RuntimeError(f"`{' '.join(args)}` failed: {excerpt}")
        return proc.stdout


def _extract_pr_url(output: str) -> str | None:
    match = re.search(r"https://\S+/pull/\d+", output)
    if match is None:
        return None
    return match.group(0)
