from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
