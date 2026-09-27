from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from runner.config import RunnerConfig
from runner.self_improvement import (
    bootstrap_run,
    generate_run_id,
    load_self_improvement_config,
    refresh_metrics,
    run_epoch_split,
    start_self_improvement_run,
    update_best_candidate,
)


class SelfImprovementBootstrapTest(unittest.TestCase):
    def test_generate_run_id_uses_utc_timestamp_with_microseconds(self) -> None:
        run_id = generate_run_id(datetime(2026, 9, 27, 9, 5, 12, 345678, tzinfo=UTC))

        self.assertEqual(run_id, "run-20260927-090512-345678")

    def test_bootstrap_creates_run_recipe_manifest_and_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            seed_recipe = repo_root / "recipes" / "legal-agent"
            seed_recipe.mkdir(parents=True)
            (seed_recipe / "SYSTEM.md").write_text("baseline system\n", encoding="utf-8")
            (seed_recipe / "package.json").write_text("{}\n", encoding="utf-8")

            split_config = repo_root / "experiment_configs" / "smoke_split.yaml"
            split_config.parent.mkdir(parents=True)
            split_config.write_text("id: smoke\n", encoding="utf-8")

            config_path = repo_root / "self-improvement-configs" / "smoke.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                "\n".join(
                    [
                        "id: smoke",
                        "description: Smoke loop.",
                        "seed_recipe: recipes/legal-agent",
                        "split_config: experiment_configs/smoke_split.yaml",
                        "loop:",
                        "  max_epochs: 2",
                        "  test_every: 1",
                        "researcher:",
                        "  epoch_session_mode: fresh",
                        "verifier:",
                        "  enabled: true",
                        "  max_revision_attempts: 2",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            config = load_self_improvement_config(config_path)
            run = bootstrap_run(repo_root, config_path, config, "run-001")

            self.assertEqual(run.run_id, "run-001")
            self.assertEqual(
                run.working_recipe,
                repo_root / "recipes" / "self-improvement" / "run-001" / "legal-agent",
            )
            self.assertEqual(
                run.manifest,
                repo_root / ".introspection" / "self-improvement-run-001.yaml",
            )
            self.assertEqual(
                run.results_dir,
                repo_root / "results" / "self-improvement" / "run-001",
            )
            self.assertEqual(
                (run.working_recipe / "SYSTEM.md").read_text(encoding="utf-8"),
                "baseline system\n",
            )
            self.assertIn(
                "path: recipes/self-improvement/run-001/legal-agent",
                run.manifest.read_text(encoding="utf-8"),
            )
            self.assertTrue((run.results_dir / "workspace" / "journal.md").exists())
            self.assertTrue((run.results_dir / "workspace" / "hypotheses.md").exists())
            self.assertTrue((run.results_dir / "workspace" / "experiments.md").exists())
            self.assertTrue((run.results_dir / "workspace" / "current_plan.md").exists())
            self.assertTrue((run.results_dir / "epochs").is_dir())
            self.assertTrue((run.results_dir / "test").is_dir())
            self.assertEqual(
                (
                    run.results_dir / "references" / "baseline_recipe" / "SYSTEM.md"
                ).read_text(encoding="utf-8"),
                "baseline system\n",
            )
            self.assertTrue((run.results_dir / "metadata" / "run.json").exists())

    def test_run_epoch_split_writes_train_and_test_to_conventional_epoch_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            config_path = repo_root / "self-improvement-configs" / "smoke.yaml"
            split_config = repo_root / "experiment_configs" / "smoke_split.yaml"
            config_path.parent.mkdir(parents=True)
            split_config.parent.mkdir(parents=True)
            config_path.write_text(
                "\n".join(
                    [
                        "id: smoke",
                        "seed_recipe: recipes/legal-agent",
                        "split_config: experiment_configs/smoke_split.yaml",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            split_config.write_text("id: smoke\n", encoding="utf-8")
            (repo_root / "results" / "self-improvement" / "run-001").mkdir(parents=True)

            config = load_self_improvement_config(config_path)
            runner_config = RunnerConfig(repo_root=repo_root, runtime_id="runtime-123")

            with patch("runner.self_improvement.run_experiment") as run_experiment:
                run_experiment.side_effect = [
                    repo_root / "results" / "self-improvement" / "run-001" / "epochs" / "epoch-003",
                    repo_root / "results" / "self-improvement" / "run-001" / "test" / "epoch-003",
                ]

                train_dir = run_epoch_split(
                    repo_root=repo_root,
                    config=config,
                    run_id="run-001",
                    epoch=3,
                    split_kind="train",
                    runner_config=runner_config,
                )
                test_dir = run_epoch_split(
                    repo_root=repo_root,
                    config=config,
                    run_id="run-001",
                    epoch=3,
                    split_kind="test",
                    runner_config=runner_config,
                )

            self.assertEqual(
                train_dir,
                repo_root / "results" / "self-improvement" / "run-001" / "epochs" / "epoch-003",
            )
            self.assertEqual(
                test_dir,
                repo_root / "results" / "self-improvement" / "run-001" / "test" / "epoch-003",
            )
            train_call, test_call = run_experiment.call_args_list
            self.assertEqual(train_call.kwargs["split"], "train")
            self.assertEqual(test_call.kwargs["split"], "test")
            self.assertEqual(
                train_call.kwargs["results_root"],
                repo_root / "results" / "self-improvement" / "run-001" / "epochs",
            )
            self.assertEqual(
                test_call.kwargs["results_root"],
                repo_root / "results" / "self-improvement" / "run-001" / "test",
            )
            self.assertEqual(train_call.kwargs["experiment_run_id"], "epoch-003")
            self.assertEqual(test_call.kwargs["experiment_run_id"], "epoch-003")
            self.assertFalse(train_call.kwargs["include_split_subdir"])
            self.assertFalse(test_call.kwargs["include_split_subdir"])

    def test_start_runs_epoch_zero_train_and_test_then_updates_metrics_and_best(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            seed_recipe = repo_root / "recipes" / "legal-agent"
            seed_recipe.mkdir(parents=True)
            (seed_recipe / "SYSTEM.md").write_text("baseline system\n", encoding="utf-8")

            split_config = repo_root / "experiment_configs" / "smoke_split.yaml"
            split_config.parent.mkdir(parents=True)
            split_config.write_text("id: smoke\n", encoding="utf-8")

            config_path = repo_root / "self-improvement-configs" / "smoke.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                "\n".join(
                    [
                        "id: smoke",
                        "seed_recipe: recipes/legal-agent",
                        "split_config: experiment_configs/smoke_split.yaml",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_self_improvement_config(config_path)
            runner_config = RunnerConfig(repo_root=repo_root, runtime_id="runtime-123")

            train_dir = (
                repo_root
                / "results"
                / "self-improvement"
                / "run-001"
                / "epochs"
                / "epoch-000"
            )
            test_dir = repo_root / "results" / "self-improvement" / "run-001" / "test" / "epoch-000"
            with (
                patch("runner.self_improvement.run_epoch_split") as run_epoch,
                patch("runner.self_improvement.refresh_metrics") as refresh,
                patch("runner.self_improvement.update_best_candidate") as update_best,
            ):
                run_epoch.side_effect = [train_dir, test_dir]
                refresh.return_value = [{"epoch": 0, "train_task_pass_rate": 0.25}]
                update_best.return_value = {"best_epoch": 0, "is_new_best": True}

                result = start_self_improvement_run(
                    repo_root=repo_root,
                    config_path=config_path,
                    config=config,
                    run_id="run-001",
                    runner_config=runner_config,
                )

            self.assertEqual(
                result.run.results_dir,
                repo_root / "results" / "self-improvement" / "run-001",
            )
            self.assertEqual(result.train_dir, train_dir)
            self.assertEqual(result.test_dir, test_dir)
            self.assertEqual(result.metrics, [{"epoch": 0, "train_task_pass_rate": 0.25}])
            self.assertEqual(result.best_candidate, {"best_epoch": 0, "is_new_best": True})
            self.assertEqual(run_epoch.call_count, 2)
            train_call, test_call = run_epoch.call_args_list
            self.assertEqual(train_call.kwargs["epoch"], 0)
            self.assertEqual(train_call.kwargs["split_kind"], "train")
            self.assertEqual(test_call.kwargs["epoch"], 0)
            self.assertEqual(test_call.kwargs["split_kind"], "test")
            refresh.assert_called_once_with(repo_root, "run-001")
            update_best.assert_called_once_with(repo_root, "run-001")

    def test_refresh_metrics_combines_train_and_optional_test_epoch_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            run_dir = repo_root / "results" / "self-improvement" / "run-001"
            (run_dir / "epochs" / "epoch-000").mkdir(parents=True)
            (run_dir / "epochs" / "epoch-001").mkdir(parents=True)
            (run_dir / "test" / "epoch-000").mkdir(parents=True)

            _write_json(
                run_dir / "epochs" / "epoch-000" / "aggregate.json",
                {
                    "task_pass_rate": 0.25,
                    "rubric_pass_rate": 0.7,
                    "number_of_tasks": 2,
                    "number_of_evaluated_tasks": 2,
                    "number_of_trials": 2,
                    "number_of_scored_trials": 2,
                    "number_of_failed_trials": 0,
                },
            )
            _write_json(
                run_dir / "epochs" / "epoch-001" / "aggregate.json",
                {
                    "task_pass_rate": 0.5,
                    "rubric_pass_rate": 0.8,
                    "number_of_tasks": 2,
                    "number_of_evaluated_tasks": 1,
                    "number_of_trials": 2,
                    "number_of_scored_trials": 1,
                    "number_of_failed_trials": 1,
                },
            )
            _write_json(
                run_dir / "test" / "epoch-000" / "aggregate.json",
                {
                    "task_pass_rate": 0.0,
                    "rubric_pass_rate": 0.6,
                    "number_of_tasks": 1,
                    "number_of_evaluated_tasks": 1,
                    "number_of_trials": 1,
                    "number_of_scored_trials": 1,
                    "number_of_failed_trials": 0,
                },
            )

            rows = refresh_metrics(repo_root, "run-001")

            self.assertEqual([row["epoch"] for row in rows], [0, 1])
            self.assertEqual(rows[0]["train_task_pass_rate"], 0.25)
            self.assertEqual(rows[0]["test_task_pass_rate"], 0.0)
            self.assertEqual(rows[1]["train_task_pass_rate"], 0.5)
            self.assertIsNone(rows[1]["test_task_pass_rate"])
            self.assertTrue((run_dir / "metadata" / "metrics.jsonl").exists())
            self.assertFalse((run_dir / "metadata" / "best_candidate.json").exists())
            csv_text = (run_dir / "metadata" / "metrics.csv").read_text(encoding="utf-8")
            self.assertIn("epoch,train_task_pass_rate", csv_text)
            self.assertIn("0,0.25", csv_text)

    def test_update_best_candidate_uses_task_pass_rate_then_rubric_tie_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            metadata_dir = repo_root / "results" / "self-improvement" / "run-001" / "metadata"
            metadata_dir.mkdir(parents=True)
            metrics_path = metadata_dir / "metrics.jsonl"
            metrics_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "epoch": 0,
                                "train_task_pass_rate": 0.5,
                                "train_rubric_pass_rate": 0.7,
                            }
                        ),
                        json.dumps(
                            {
                                "epoch": 1,
                                "train_task_pass_rate": 0.5,
                                "train_rubric_pass_rate": 0.8,
                            }
                        ),
                        json.dumps(
                            {
                                "epoch": 2,
                                "train_task_pass_rate": 0.25,
                                "train_rubric_pass_rate": 1.0,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            best = update_best_candidate(repo_root, "run-001")

            self.assertEqual(best["best_epoch"], 1)
            self.assertEqual(best["train_task_pass_rate"], 0.5)
            self.assertEqual(best["train_rubric_pass_rate"], 0.8)
            self.assertTrue(best["is_new_best"])
            self.assertIsNone(best["previous_best_epoch"])
            self.assertFalse(best["checkpoint_created"])
            self.assertFalse(best["pr_created"])
            self.assertEqual(
                json.loads((metadata_dir / "best_candidate.json").read_text(encoding="utf-8"))[
                    "best_epoch"
                ],
                1,
            )

            second_best = update_best_candidate(repo_root, "run-001")

            self.assertFalse(second_best["is_new_best"])
            self.assertEqual(second_best["previous_best_epoch"], 1)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
