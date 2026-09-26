from __future__ import annotations

import argparse
from pathlib import Path

from .config import RunnerConfig
from .execute import run_one


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
        )
        trial_dir = run_one(args.task_id, args.trial, config)
        print(trial_dir)
        return 0
    raise AssertionError(f"unhandled command {args.command}")
