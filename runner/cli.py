from __future__ import annotations

import argparse
from pathlib import Path

from .config import RunnerConfig
from .execute import run_one
from .experiment import run_experiment
from .self_improvement import (
    bootstrap_run,
    generate_run_id,
    load_self_improvement_config,
    refresh_metrics,
    run_epoch_split,
    start_self_improvement_run,
    update_best_candidate,
)


def main() -> int:
    parser = argparse.ArgumentParser(prog="harvey-runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_one_parser = subparsers.add_parser("run-one")
    run_one_parser.add_argument("--task-id", required=True)
    run_one_parser.add_argument("--trial", type=int, default=1)
    run_one_parser.add_argument("--harvey-repo", type=Path, default=Path("harvey-labs"))
    run_one_parser.add_argument("--results-root", type=Path, default=Path("results/tasks"))
    run_one_parser.add_argument("--runtime-name", default="legal-agent")
    run_one_parser.add_argument("--runtime-id", default=None)
    run_one_parser.add_argument("--environment", default="development")
    run_one_parser.add_argument("--agent", default="agent")
    run_one_parser.add_argument("--skip-eval", action="store_true")
    run_one_parser.add_argument("--eval-judges", nargs="+", default=["gpt-4.1"])
    run_one_parser.add_argument("--eval-parallel", type=int, default=2)

    run_experiment_parser = subparsers.add_parser("run-experiment")
    run_experiment_parser.add_argument("--config", type=Path, required=True)
    run_experiment_parser.add_argument("--split", required=True)
    run_experiment_parser.add_argument("--harvey-repo", type=Path, default=Path("harvey-labs"))
    run_experiment_parser.add_argument(
        "--results-root", type=Path, default=Path("results/experiment_runs")
    )
    run_experiment_parser.add_argument("--experiment-run-id", default=None)
    run_experiment_parser.add_argument("--runtime-name", default="legal-agent")
    run_experiment_parser.add_argument("--runtime-id", default=None)
    run_experiment_parser.add_argument("--environment", default="development")
    run_experiment_parser.add_argument("--agent", default="agent")
    run_experiment_parser.add_argument("--skip-eval", action="store_true")
    run_experiment_parser.add_argument("--eval-judges", nargs="+", default=["gpt-4.1"])
    run_experiment_parser.add_argument("--eval-parallel", type=int, default=2)

    self_improve_parser = subparsers.add_parser("self-improve")
    self_improve_subparsers = self_improve_parser.add_subparsers(
        dest="self_improve_command",
        required=True,
    )

    bootstrap_parser = self_improve_subparsers.add_parser("bootstrap")
    bootstrap_parser.add_argument("--config", type=Path, required=True)
    bootstrap_parser.add_argument("--run-id", required=True)

    start_parser = self_improve_subparsers.add_parser("start")
    add_self_improve_start_args(start_parser)

    run_train_parser = self_improve_subparsers.add_parser("run-train")
    add_self_improve_run_args(run_train_parser)

    run_test_parser = self_improve_subparsers.add_parser("run-test")
    add_self_improve_run_args(run_test_parser)

    refresh_metrics_parser = self_improve_subparsers.add_parser("refresh-metrics")
    refresh_metrics_parser.add_argument("--run-id", required=True)

    update_best_parser = self_improve_subparsers.add_parser("update-best-candidate")
    update_best_parser.add_argument("--run-id", required=True)

    args = parser.parse_args()
    if args.command == "run-one":
        config = RunnerConfig(
            repo_root=Path("."),
            harvey_repo=args.harvey_repo,
            results_root=args.results_root,
            runtime_name=args.runtime_name,
            runtime_id=args.runtime_id,
            environment=args.environment,
            agent=args.agent,
            enable_eval=not args.skip_eval,
            eval_judges=tuple(args.eval_judges),
            eval_parallel=args.eval_parallel,
        )
        trial_dir = run_one(args.task_id, args.trial, config)
        print(trial_dir)
        return 0
    if args.command == "run-experiment":
        config = RunnerConfig(
            repo_root=Path("."),
            harvey_repo=args.harvey_repo,
            runtime_name=args.runtime_name,
            runtime_id=args.runtime_id,
            environment=args.environment,
            agent=args.agent,
            enable_eval=not args.skip_eval,
            eval_judges=tuple(args.eval_judges),
            eval_parallel=args.eval_parallel,
        )
        run_dir = run_experiment(
            config_path=args.config,
            split=args.split,
            runner_config=config,
            experiment_run_id=args.experiment_run_id,
            results_root=args.results_root,
        )
        print(run_dir)
        return 0
    if args.command == "self-improve":
        if args.self_improve_command == "bootstrap":
            self_improvement_config = load_self_improvement_config(args.config)
            run = bootstrap_run(
                Path("."),
                args.config,
                self_improvement_config,
                args.run_id,
            )
            print(run.results_dir)
            return 0
        if args.self_improve_command == "start":
            self_improvement_config = load_self_improvement_config(args.config)
            run_id = args.run_id or generate_run_id()
            config = RunnerConfig(
                repo_root=Path("."),
                harvey_repo=args.harvey_repo,
                runtime_name=f"self-improvement-{run_id}",
                runtime_id=args.runtime_id,
                environment=args.environment,
                agent=args.agent,
                enable_eval=not args.skip_eval,
                eval_judges=tuple(args.eval_judges),
                eval_parallel=args.eval_parallel,
            )
            result = start_self_improvement_run(
                repo_root=Path("."),
                config_path=args.config,
                config=self_improvement_config,
                run_id=run_id,
                runner_config=config,
            )
            print(result.run.results_dir)
            return 0
        if args.self_improve_command in {"run-train", "run-test"}:
            self_improvement_config = load_self_improvement_config(args.config)
            config = RunnerConfig(
                repo_root=Path("."),
                harvey_repo=args.harvey_repo,
                runtime_name=f"self-improvement-{args.run_id}",
                runtime_id=args.runtime_id,
                environment=args.environment,
                agent=args.agent,
                enable_eval=not args.skip_eval,
                eval_judges=tuple(args.eval_judges),
                eval_parallel=args.eval_parallel,
            )
            epoch_dir = run_epoch_split(
                repo_root=Path("."),
                config=self_improvement_config,
                run_id=args.run_id,
                epoch=args.epoch,
                split_kind="train" if args.self_improve_command == "run-train" else "test",
                runner_config=config,
            )
            print(epoch_dir)
            return 0
        if args.self_improve_command == "refresh-metrics":
            refresh_metrics(Path("."), args.run_id)
            print(Path("results") / "self-improvement" / args.run_id / "metadata" / "metrics.csv")
            return 0
        if args.self_improve_command == "update-best-candidate":
            update_best_candidate(Path("."), args.run_id)
            print(
                Path("results")
                / "self-improvement"
                / args.run_id
                / "metadata"
                / "best_candidate.json"
            )
            return 0
        raise AssertionError(f"unhandled self-improve command {args.self_improve_command}")
    raise AssertionError(f"unhandled command {args.command}")


def add_self_improve_start_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--harvey-repo", type=Path, default=Path("harvey-labs"))
    parser.add_argument("--runtime-id", default=None)
    parser.add_argument("--environment", default="development")
    parser.add_argument("--agent", default="agent")
    parser.add_argument("--skip-eval", action="store_true")
    parser.add_argument("--eval-judges", nargs="+", default=["gpt-4.1"])
    parser.add_argument("--eval-parallel", type=int, default=2)


def add_self_improve_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("--harvey-repo", type=Path, default=Path("harvey-labs"))
    parser.add_argument("--runtime-id", default=None)
    parser.add_argument("--environment", default="development")
    parser.add_argument("--agent", default="agent")
    parser.add_argument("--skip-eval", action="store_true")
    parser.add_argument("--eval-judges", nargs="+", default=["gpt-4.1"])
    parser.add_argument("--eval-parallel", type=int, default=2)
