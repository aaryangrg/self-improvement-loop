from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from runner.pr_summary import render_pr_body
from runner.self_improvement import (
    EvaluationConfig,
    LoopConfig,
    ResearcherConfig,
    SelfImprovementConfig,
    VerifierConfig,
)


class PrSummaryTest(unittest.TestCase):
    def test_body_tracks_scores_and_candidate_history(self) -> None:
        config = SelfImprovementConfig(
            id="smoke",
            description="",
            seed_recipe=Path("recipes/legal-agent"),
            split_config=Path("experiment_configs/smoke_split.yaml"),
            loop=LoopConfig(max_epochs=2, test_every=1),
            evaluation=EvaluationConfig(judges=("gpt-6-sol",)),
            researcher=ResearcherConfig(model="gpt-6-sol"),
            verifier=VerifierConfig(model="gpt-6-sol"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metrics = root / "metadata" / "metrics.jsonl"
            metrics.parent.mkdir()
            (root / "metadata" / "run.json").write_text(
                json.dumps({"config": {"split_config": "experiment_configs/smoke_split.yaml"}}),
                encoding="utf-8",
            )
            rows = [
                {
                    "epoch": 0,
                    "train_task_pass_rate": 0.0,
                    "train_rubric_pass_rate": 0.72,
                    "train_number_of_evaluated_tasks": 1,
                    "train_number_of_tasks": 1,
                    "test_task_pass_rate": 0.0,
                    "test_rubric_pass_rate": 0.435,
                    "test_number_of_evaluated_tasks": 1,
                    "test_number_of_tasks": 1,
                },
                {
                    "epoch": 1,
                    "train_task_pass_rate": 0.0,
                    "train_rubric_pass_rate": 0.84,
                    "train_number_of_evaluated_tasks": 1,
                    "train_number_of_tasks": 1,
                },
            ]
            metrics.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            epoch = root / "metadata" / "researcher" / "epoch-001"
            review = epoch / "verifier" / "attempt-001"
            review.mkdir(parents=True)
            (epoch / "researcher_result.json").write_text(
                json.dumps({"hypothesis": "A coverage check helps.", "changes_made": ["Added check."]}),
                encoding="utf-8",
            )
            (epoch / "candidate.json").write_text(
                json.dumps({"commit": "a" * 40}), encoding="utf-8"
            )
            (review / "verifier_result.json").write_text(
                json.dumps({"verdict": "approve"}), encoding="utf-8"
            )

            resumed_config = replace(config, split_config=Path("metadata/split_config.yaml"))
            body = render_pr_body(resumed_config, "run-001", root)
            (epoch / "candidate.json").unlink()
            old_run_body = render_pr_body(config, "run-001", root)

        self.assertIn("## Experiment\n\nRun `run-001`", body)
        self.assertIn("`experiment_configs/smoke_split.yaml`", body)
        self.assertNotIn("metadata/split_config.yaml", body)
        self.assertIn("1/2 research epochs scored", body)
        self.assertIn("| 0 (baseline) | 0.0% | 72.0% | 1/1 | 0.0% | 43.5% | 1/1 |", body)
        self.assertIn("| 1 | 0.0% | 84.0% | 1/1 | — | — | — |", body)
        self.assertIn("### Epoch 1 · commit `aaaaaaa`", body)
        self.assertIn("**Verifier:** approve", body)
        self.assertIn("### Epoch 1 · scored", old_run_body)


if __name__ == "__main__":
    unittest.main()
