from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from runner.config import RunnerConfig
from runner.experiment import TrialResult, run_experiment


class ExperimentSchedulingTests(unittest.TestCase):
    def test_evaluation_starts_before_all_agent_tasks_finish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "split.yaml"
            config_path.write_text(
                "id: pipelined\n"
                "defaults:\n"
                "  trials_per_task: 1\n"
                "  task_concurrency: 2\n"
                "  eval_concurrency: 1\n"
                "splits:\n"
                "  train:\n"
                "    task_ids: [fast, slow]\n",
                encoding="utf-8",
            )
            evaluation_started = Event()

            def run_trial(task_id: str, trial: int, config: RunnerConfig) -> tuple[str, int, Path]:
                if task_id == "slow" and not evaluation_started.wait(timeout=2):
                    raise AssertionError("evaluation waited for the slower agent task")
                trial_dir = config.results_root / task_id / f"trial-{trial:03d}"
                trial_dir.mkdir(parents=True)
                return task_id, trial, trial_dir

            def evaluate_trial(
                task_id: str, trial: int, trial_dir: Path, config: RunnerConfig
            ) -> TrialResult:
                evaluation_started.set()
                return TrialResult(
                    task_id=task_id,
                    trial=trial,
                    trial_dir=str(trial_dir),
                    status="scored",
                    scored=True,
                    tasks_passed=1,
                    tasks_evaluated=1,
                    rubrics_passed=1,
                    rubrics_evaluated=1,
                )

            with (
                patch("runner.experiment._run_trial_without_eval", side_effect=run_trial),
                patch("runner.experiment._evaluate_trial", side_effect=evaluate_trial),
            ):
                result_dir = run_experiment(
                    config_path=config_path,
                    split="train",
                    runner_config=RunnerConfig(repo_root=root),
                    experiment_run_id="epoch-000",
                    results_root=root / "results",
                    include_split_subdir=False,
                )

            aggregate = json.loads((result_dir / "aggregate.json").read_text(encoding="utf-8"))
            self.assertEqual(aggregate["number_of_scored_trials"], 2)
            self.assertEqual(aggregate["number_of_evaluated_tasks"], 2)
            progress = json.loads((result_dir / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual(progress["status"], "completed")
            self.assertEqual({item["status"] for item in progress["trials"]}, {"scored"})

    def test_task_without_output_does_not_start_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "split.yaml"
            config_path.write_text(
                "id: empty-output\nsplits:\n  train:\n    task_ids: [empty]\n",
                encoding="utf-8",
            )
            trial_root = root / "results" / "epoch-000" / "empty"

            def no_output(task_id: str, trial: int, config: RunnerConfig) -> Path:
                trial_dir = trial_root / f"trial-{trial:03d}"
                trial_dir.mkdir(parents=True)
                return trial_dir

            with (
                patch("runner.experiment.load_task", return_value=SimpleNamespace(task_id="empty")),
                patch("runner.experiment.run_one", side_effect=no_output) as run_one,
                patch("runner.experiment._evaluate_trial") as evaluate_trial,
            ):
                result_dir = run_experiment(
                    config_path=config_path,
                    split="train",
                    runner_config=RunnerConfig(repo_root=root),
                    experiment_run_id="epoch-000",
                    results_root=root / "results",
                    include_split_subdir=False,
                )

            evaluate_trial.assert_not_called()
            self.assertEqual([call.args[1] for call in run_one.call_args_list], [1, 2])
            aggregate = json.loads((result_dir / "aggregate.json").read_text(encoding="utf-8"))
            self.assertEqual(aggregate["number_of_failed_trials"], 2)
            self.assertEqual(aggregate["number_of_evaluated_tasks"], 0)
            progress = json.loads((result_dir / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual(progress["status"], "incomplete")
            self.assertEqual([item["status"] for item in progress["trials"]], ["failed", "failed"])

    def test_retry_only_tasks_with_no_scored_trial(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "split.yaml"
            config_path.write_text(
                "id: coverage\n"
                "defaults:\n  trials_per_task: 2\n  task_concurrency: 2\n"
                "splits:\n  train:\n    task_ids: [covered, uncovered, missing]\n",
                encoding="utf-8",
            )
            attempts: list[tuple[str, int]] = []

            def run_trial(task_id: str, trial: int, config: RunnerConfig) -> TrialResult:
                attempts.append((task_id, trial))
                scored = (task_id == "covered" and trial == 1) or (
                    task_id == "uncovered" and trial == 3
                )
                return TrialResult(
                    task_id=task_id,
                    trial=trial,
                    trial_dir=str(config.results_root / task_id / f"trial-{trial:03d}"),
                    status="scored" if scored else "failed",
                    scored=scored,
                    tasks_passed=int(scored),
                    tasks_evaluated=int(scored),
                    rubrics_passed=int(scored),
                    rubrics_evaluated=int(scored),
                )

            with patch("runner.experiment._run_trial_without_eval", side_effect=run_trial):
                result_dir = run_experiment(
                    config_path=config_path,
                    split="train",
                    runner_config=RunnerConfig(repo_root=root),
                    experiment_run_id="epoch-000",
                    results_root=root / "results",
                    include_split_subdir=False,
                )

            self.assertCountEqual(
                attempts,
                [
                    ("covered", 1),
                    ("covered", 2),
                    ("uncovered", 1),
                    ("uncovered", 2),
                    ("uncovered", 3),
                    ("missing", 1),
                    ("missing", 2),
                    ("missing", 3),
                ],
            )
            aggregate = json.loads((result_dir / "aggregate.json").read_text(encoding="utf-8"))
            self.assertEqual(aggregate["number_of_evaluated_tasks"], 2)
            self.assertEqual(aggregate["number_of_scored_trials"], 2)
            self.assertEqual(aggregate["number_of_failed_trials"], 6)
            progress = json.loads((result_dir / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual(progress["status"], "incomplete")

    def test_does_not_retry_at_half_task_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "split.yaml"
            config_path.write_text(
                "id: half\nsplits:\n  train:\n    task_ids: [covered, missing]\n",
                encoding="utf-8",
            )
            attempts: list[tuple[str, int]] = []

            def run_trial(task_id: str, trial: int, config: RunnerConfig) -> TrialResult:
                attempts.append((task_id, trial))
                scored = task_id == "covered"
                return TrialResult(
                    task_id=task_id,
                    trial=trial,
                    trial_dir=str(config.results_root / task_id / f"trial-{trial:03d}"),
                    status="scored" if scored else "failed",
                    scored=scored,
                    tasks_passed=int(scored),
                    tasks_evaluated=int(scored),
                    rubrics_passed=int(scored),
                    rubrics_evaluated=int(scored),
                )

            with patch("runner.experiment._run_trial_without_eval", side_effect=run_trial):
                result_dir = run_experiment(
                    config_path=config_path,
                    split="train",
                    runner_config=RunnerConfig(repo_root=root),
                    experiment_run_id="epoch-000",
                    results_root=root / "results",
                    include_split_subdir=False,
                )

            self.assertCountEqual(attempts, [("covered", 1), ("missing", 1)])
            aggregate = json.loads((result_dir / "aggregate.json").read_text(encoding="utf-8"))
            self.assertEqual(aggregate["number_of_evaluated_tasks"], 1)
            progress = json.loads((result_dir / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual(progress["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
