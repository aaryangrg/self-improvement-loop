from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runner.dashboard_data import list_runs, load_run
from runner.progress import SplitProgress, write_run_progress


class DashboardDataTests(unittest.TestCase):
    def test_local_run_reader_and_costs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            root = repo / "results" / "self-improvement" / "sample"
            metadata = root / "metadata"
            metadata.mkdir(parents=True)
            write_run_progress(root, epoch=1, phase="train")
            split = root / "epochs" / "epoch-001"
            progress = SplitProgress(split / "progress.json", "train", [("task-a", 1)])
            progress.set_trial("task-a", 1, "evaluating")
            trial = split / "task-a" / "trial-001"
            trial.mkdir(parents=True)
            (trial / "conversation.json").write_text(
                json.dumps({"conversation": {"cost": {"usd": 0.75}}}), encoding="utf-8"
            )
            evaluation = trial / "evaluation"
            evaluation.mkdir()
            (evaluation / "judge_usage.json").write_text(
                json.dumps(
                    {
                        "responses": [
                            {
                                "model": "gpt-6-sol",
                                "input_tokens": 1000,
                                "output_tokens": 100,
                                "input_tokens_details": {"cached_tokens": 200},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(list_runs(repo), ["sample"])
            data = load_run(repo, "sample")
            self.assertEqual(data["progress"]["phase"], "train")
            self.assertEqual(data["splits"][0]["trials"][0]["status"], "evaluating")
            self.assertEqual(data["splits"][0]["generation_cost_usd"], 0.75)
            self.assertAlmostEqual(data["splits"][0]["judge_cost_usd"], 0.00264)

    def test_research_history_groups_decisions_and_links_approved_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            root = repo / "results" / "self-improvement" / "sample"
            epoch = root / "metadata" / "researcher" / "epoch-001"
            review = epoch / "verifier" / "attempt-001"
            review.mkdir(parents=True)
            write_run_progress(root, epoch=1, phase="complete", status="completed")
            (root / "metadata" / "runtime.json").write_text(
                json.dumps({"pr_url": "https://github.com/example/project/pull/7"}),
                encoding="utf-8",
            )
            (epoch / "researcher_result.json").write_text(
                json.dumps(
                    {
                        "hypothesis": "Missing source review causes omissions.",
                        "changes_made": [
                            "Added a source checklist.",
                            "Added a final coverage pass.",
                        ],
                        "expected_outcome": "Fewer missed issues.",
                    }
                ),
                encoding="utf-8",
            )
            (review / "verifier_result.json").write_text(
                json.dumps({"verdict": "approve", "summary": "General change.", "issues": []}),
                encoding="utf-8",
            )
            sha = "a" * 40
            (epoch / "candidate.json").write_text(
                json.dumps({"commit": sha, "version_id": "version-1"}), encoding="utf-8"
            )

            history = load_run(repo, "sample")["research_history"]

            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["epoch"], 1)
            self.assertEqual(
                history[0]["researcher"]["result"]["changes_made"][0], "Added a source checklist."
            )
            self.assertEqual(history[0]["verifier"]["result"]["verdict"], "approve")
            self.assertEqual(history[0]["commit"], sha)
            self.assertEqual(
                history[0]["commit_url"], f"https://github.com/example/project/commit/{sha}"
            )

    def test_research_history_uses_git_for_old_completed_epochs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            root = repo / "results" / "self-improvement" / "sample"
            review = root / "metadata" / "researcher" / "epoch-001" / "verifier" / "attempt-001"
            review.mkdir(parents=True)
            write_run_progress(root, epoch=1, phase="complete", status="completed")
            (root / "metadata" / "runtime.json").write_text(
                json.dumps({"pr_url": "https://github.com/example/project/pull/7"}),
                encoding="utf-8",
            )
            (review / "verifier_result.json").write_text(
                json.dumps({"verdict": "approve", "summary": "General change.", "issues": []}),
                encoding="utf-8",
            )
            split = root / "epochs" / "epoch-001"
            split.mkdir(parents=True)
            (split / "run.json").write_text("{}", encoding="utf-8")
            sha = "b" * 40
            git_output = f"{sha}\tSelf-improvement sample epoch 001 candidate 2\n"
            with patch("runner.dashboard_data.subprocess.run") as run:
                run.return_value.stdout = git_output
                run.return_value.returncode = 0
                history = load_run(repo, "sample")["research_history"]

            self.assertEqual(history[0]["commit"], sha)
            self.assertEqual(
                history[0]["commit_url"], f"https://github.com/example/project/commit/{sha}"
            )


if __name__ == "__main__":
    unittest.main()
