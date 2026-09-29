from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runner.git_ops import GitOps


class GitOpsTest(unittest.TestCase):
    def test_update_pr_passes_multiline_body_without_shell_interpretation(self) -> None:
        body = "## Experiment\n\nRun `run-001` starts from `recipes/legal-agent`."
        with patch.object(GitOps, "_run") as run:
            GitOps(Path(".")).update_pr_body(2, body)

        run.assert_called_once_with(["gh", "pr", "edit", "2", "--body", body])

    def test_create_branch_requires_clean_worktree_and_switches_to_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            calls: list[list[str]] = []

            def fake_run(
                args: list[str],
                cwd: Path,
                capture_output: bool,
                text: bool,
                check: bool,
            ) -> subprocess.CompletedProcess[str]:
                calls.append(args)
                self.assertEqual(cwd, repo_root)
                self.assertTrue(capture_output)
                self.assertTrue(text)
                self.assertFalse(check)
                if args == ["git", "status", "--porcelain"]:
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                if args == ["git", "branch", "--show-current"]:
                    return subprocess.CompletedProcess(args, 0, stdout="feature\n", stderr="")
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            with patch("runner.git_ops.subprocess.run", side_effect=fake_run):
                GitOps(repo_root).create_branch("self-improvement/run-001", base="main")

            self.assertEqual(
                calls,
                [
                    ["git", "status", "--porcelain"],
                    ["git", "branch", "--show-current"],
                    ["git", "switch", "main"],
                    ["git", "switch", "-c", "self-improvement/run-001", "main"],
                ],
            )

    def test_open_draft_pr_switches_branch_and_reads_pr_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            calls: list[list[str]] = []

            def fake_run(
                args: list[str],
                cwd: Path,
                capture_output: bool,
                text: bool,
                check: bool,
            ) -> subprocess.CompletedProcess[str]:
                calls.append(args)
                self.assertEqual(cwd, repo_root)
                if args == ["git", "branch", "--show-current"]:
                    return subprocess.CompletedProcess(args, 0, stdout="main\n", stderr="")
                if args[:3] == ["gh", "pr", "create"]:
                    return subprocess.CompletedProcess(
                        args,
                        0,
                        stdout="https://github.com/example/repo/pull/42\n",
                        stderr="",
                    )
                if args == [
                    "gh",
                    "pr",
                    "view",
                    "https://github.com/example/repo/pull/42",
                    "--json",
                    "number,url",
                ]:
                    return subprocess.CompletedProcess(
                        args,
                        0,
                        stdout=json.dumps(
                            {
                                "number": 42,
                                "url": "https://github.com/example/repo/pull/42",
                            }
                        ),
                        stderr="",
                    )
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            with patch("runner.git_ops.subprocess.run", side_effect=fake_run):
                pr = GitOps(repo_root).open_draft_pr(
                    branch="self-improvement/run-001",
                    base="main",
                    title="Test PR",
                    body="Body",
                )

            self.assertEqual(pr.number, 42)
            self.assertEqual(pr.url, "https://github.com/example/repo/pull/42")
            self.assertEqual(pr.branch, "self-improvement/run-001")
            self.assertIn(["git", "switch", "self-improvement/run-001"], calls)


if __name__ == "__main__":
    unittest.main()
