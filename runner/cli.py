from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

from .config import RunnerConfig
from .execute import run_one
from .experiment import IncompleteExperimentError, run_experiment
from .git_ops import GitOps
from .introspection import IntrospectionClient
from .loop import start_loop
from .researcher import prepare_research_workspace, run_researcher
from .self_improvement import (
    bootstrap_run,
    candidate_commit_paths,
    load_runtime_metadata,
    load_self_improvement_config,
    record_bootstrap_commit,
    record_candidate,
    record_pr,
    record_pr_branch,
    record_runtime,
    refresh_metrics,
    run_epoch_split,
    runner_config_with_runtime_metadata,
    update_best_candidate,
)
from .verifier import promote_candidate, run_verifier


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
    run_one_parser.add_argument("--environment", default="staging")
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
    run_experiment_parser.add_argument("--environment", default="staging")
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

    commit_bootstrap_parser = self_improve_subparsers.add_parser("commit-bootstrap")
    commit_bootstrap_parser.add_argument("--run-id", required=True)
    commit_bootstrap_parser.add_argument("--branch", default="main")
    commit_bootstrap_parser.add_argument("--message", default=None)
    commit_bootstrap_parser.add_argument("--no-push", action="store_true")

    create_runtime_parser = self_improve_subparsers.add_parser("create-runtime")
    create_runtime_parser.add_argument("--run-id", required=True)

    show_runtime_parser = self_improve_subparsers.add_parser("show-runtime")
    show_runtime_parser.add_argument("--run-id", required=True)

    record_runtime_parser = self_improve_subparsers.add_parser("record-runtime")
    record_runtime_parser.add_argument("--run-id", required=True)
    record_runtime_parser.add_argument("--runtime-id", required=True)
    record_runtime_parser.add_argument("--runtime-name", default=None)
    record_runtime_parser.add_argument("--runtime-group-id", default=None)
    record_runtime_parser.add_argument("--environment", default=None)

    record_pr_parser = self_improve_subparsers.add_parser("record-pr")
    record_pr_parser.add_argument("--run-id", required=True)
    record_pr_parser.add_argument("--pr-number", type=int, required=True)
    record_pr_parser.add_argument("--branch", required=True)
    record_pr_parser.add_argument("--url", default=None)

    create_pr_branch_parser = self_improve_subparsers.add_parser("create-pr-branch")
    create_pr_branch_parser.add_argument("--run-id", required=True)
    create_pr_branch_parser.add_argument("--branch", default=None)
    create_pr_branch_parser.add_argument("--base", default="main")

    open_pr_parser = self_improve_subparsers.add_parser("open-pr")
    open_pr_parser.add_argument("--run-id", required=True)
    open_pr_parser.add_argument("--base", default="main")
    open_pr_parser.add_argument("--title", default=None)
    open_pr_parser.add_argument("--body", default=None)
    open_pr_parser.add_argument("--ready", action="store_true")

    record_candidate_parser = self_improve_subparsers.add_parser("record-candidate")
    record_candidate_parser.add_argument("--run-id", required=True)
    record_candidate_parser.add_argument("--commit", required=True)
    record_candidate_parser.add_argument("--version-id", default=None)

    commit_candidate_parser = self_improve_subparsers.add_parser("commit-candidate")
    commit_candidate_parser.add_argument("--run-id", required=True)
    commit_candidate_parser.add_argument("--epoch", type=int, required=True)
    commit_candidate_parser.add_argument("--message", default=None)
    commit_candidate_parser.add_argument("--no-push", action="store_true")

    pin_staging_parser = self_improve_subparsers.add_parser("pin-staging-pr")
    pin_staging_parser.add_argument("--run-id", required=True)

    run_train_parser = self_improve_subparsers.add_parser("run-train")
    add_self_improve_run_args(run_train_parser)

    run_test_parser = self_improve_subparsers.add_parser("run-test")
    add_self_improve_run_args(run_test_parser)

    refresh_metrics_parser = self_improve_subparsers.add_parser("refresh-metrics")
    refresh_metrics_parser.add_argument("--run-id", required=True)

    update_best_parser = self_improve_subparsers.add_parser("update-best-candidate")
    update_best_parser.add_argument("--run-id", required=True)

    prepare_researcher_parser = self_improve_subparsers.add_parser("prepare-researcher")
    add_researcher_args(prepare_researcher_parser)

    research_parser = self_improve_subparsers.add_parser("research")
    add_researcher_args(research_parser)

    verify_parser = self_improve_subparsers.add_parser("verify")
    add_researcher_args(verify_parser)

    promote_parser = self_improve_subparsers.add_parser("promote")
    add_researcher_args(promote_parser)

    start_parser = self_improve_subparsers.add_parser("start")
    start_parser.add_argument("--config", type=Path, required=True)
    start_parser.add_argument("--run-id", default=None)

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
        try:
            run_dir = run_experiment(
                config_path=args.config,
                split=args.split,
                runner_config=config,
                experiment_run_id=args.experiment_run_id,
                results_root=args.results_root,
            )
        except IncompleteExperimentError as error:
            print(error, file=sys.stderr)
            return 1
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
        if args.self_improve_command == "commit-bootstrap":
            message = args.message or f"Bootstrap self-improvement run {args.run_id}"
            git_ops = GitOps(repo_root=Path("."))
            git_ops.switch_branch(args.branch)
            commit = git_ops.commit_paths(
                paths=candidate_commit_paths(Path("."), args.run_id),
                message=message,
            )
            if not args.no_push:
                git_ops.push_branch(args.branch)
            updated = record_bootstrap_commit(Path("."), args.run_id, commit)
            print(_json_line(updated))
            return 0
        if args.self_improve_command == "create-runtime":
            metadata = load_runtime_metadata(Path("."), args.run_id)
            payload = IntrospectionClient(repo_root=Path(".")).create_runtime(
                Path(metadata.manifest)
            )
            runtime_id = _first_deep_string(payload, ("runtime_id", "id"))
            runtime_group_id = _first_deep_string(payload, ("runtime_group_id", "runtimeGroupId"))
            if runtime_id is None:
                raise RuntimeError(
                    f"could not find runtime id in create-runtime response: {payload}"
                )
            updated = record_runtime(
                repo_root=Path("."),
                run_id=args.run_id,
                runtime_id=runtime_id,
                runtime_group_id=runtime_group_id,
            )
            print(_json_line(updated))
            return 0
        if args.self_improve_command == "show-runtime":
            print(_json_line(load_runtime_metadata(Path("."), args.run_id)))
            return 0
        if args.self_improve_command == "record-runtime":
            updated = record_runtime(
                repo_root=Path("."),
                run_id=args.run_id,
                runtime_id=args.runtime_id,
                runtime_name=args.runtime_name,
                runtime_group_id=args.runtime_group_id,
                environment=args.environment,
            )
            print(_json_line(updated))
            return 0
        if args.self_improve_command == "record-pr":
            updated = record_pr(
                repo_root=Path("."),
                run_id=args.run_id,
                pr_number=args.pr_number,
                pr_branch=args.branch,
                pr_url=args.url,
            )
            print(_json_line(updated))
            return 0
        if args.self_improve_command == "create-pr-branch":
            branch = args.branch or f"self-improvement/{args.run_id}"
            GitOps(repo_root=Path(".")).create_branch(branch=branch, base=args.base)
            updated = record_pr_branch(Path("."), args.run_id, branch)
            print(_json_line(updated))
            return 0
        if args.self_improve_command == "open-pr":
            metadata = load_runtime_metadata(Path("."), args.run_id)
            if metadata.pr_branch is None:
                raise RuntimeError("pr_branch must be recorded before opening a PR")
            title = args.title or f"Self-improvement run {args.run_id}"
            body = args.body or _default_pr_body(args.run_id)
            git_ops = GitOps(repo_root=Path("."))
            git_ops.push_branch(metadata.pr_branch)
            pr = git_ops.open_draft_pr(
                branch=metadata.pr_branch,
                base=args.base,
                title=title,
                body=body,
                draft=not args.ready,
            )
            updated = record_pr(
                repo_root=Path("."),
                run_id=args.run_id,
                pr_number=pr.number,
                pr_branch=pr.branch,
                pr_url=pr.url,
            )
            print(_json_line(updated))
            return 0
        if args.self_improve_command == "record-candidate":
            updated = record_candidate(
                repo_root=Path("."),
                run_id=args.run_id,
                candidate_commit=args.commit,
                candidate_version_id=args.version_id,
            )
            print(_json_line(updated))
            return 0
        if args.self_improve_command == "commit-candidate":
            metadata = load_runtime_metadata(Path("."), args.run_id)
            if metadata.pr_branch is None:
                raise RuntimeError("pr_branch must be recorded before committing a candidate")
            message = args.message or f"Self-improvement {args.run_id} epoch {args.epoch:03d}"
            git_ops = GitOps(repo_root=Path("."))
            git_ops.switch_branch(metadata.pr_branch)
            commit = git_ops.commit_paths(
                paths=candidate_commit_paths(Path("."), args.run_id),
                message=message,
            )
            if not args.no_push:
                git_ops.push_branch(metadata.pr_branch)
            updated = record_candidate(Path("."), args.run_id, candidate_commit=commit)
            print(_json_line(updated))
            return 0
        if args.self_improve_command == "pin-staging-pr":
            metadata = load_runtime_metadata(Path("."), args.run_id)
            if metadata.runtime_id is None:
                raise RuntimeError("runtime_id must be recorded before pinning staging")
            if metadata.pr_ref is None:
                raise RuntimeError("pr_ref must be recorded before pinning staging")
            payload = IntrospectionClient(repo_root=Path(".")).pin_runtime_branch(
                metadata.runtime_id,
                metadata.pr_ref,
            )
            print(_json_line(payload))
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
            )
            config = runner_config_with_runtime_metadata(Path("."), args.run_id, config)
            try:
                epoch_dir = run_epoch_split(
                    repo_root=Path("."),
                    config=self_improvement_config,
                    run_id=args.run_id,
                    epoch=args.epoch,
                    split_kind="train" if args.self_improve_command == "run-train" else "test",
                    runner_config=config,
                )
            except IncompleteExperimentError as error:
                print(error, file=sys.stderr)
                return 1
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
        if args.self_improve_command == "prepare-researcher":
            print(prepare_research_workspace(Path("."), args.run_id, args.epoch))
            return 0
        if args.self_improve_command == "research":
            print(run_researcher(Path("."), args.run_id, args.epoch))
            return 0
        if args.self_improve_command == "verify":
            print(run_verifier(Path("."), args.run_id, args.epoch))
            return 0
        if args.self_improve_command == "promote":
            print(promote_candidate(Path("."), args.run_id, args.epoch))
            return 0
        if args.self_improve_command == "start":
            print(start_loop(Path("."), args.config, args.run_id))
            return 0
        raise AssertionError(f"unhandled self-improve command {args.self_improve_command}")
    raise AssertionError(f"unhandled command {args.command}")


def add_self_improve_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("--harvey-repo", type=Path, default=Path("harvey-labs"))
    parser.add_argument("--runtime-id", default=None)
    parser.add_argument("--environment", default="staging")
    parser.add_argument("--agent", default="agent")


def add_researcher_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--epoch", type=int, required=True)


def _json_line(payload: object) -> str:
    if is_dataclass(payload) and not isinstance(payload, type):
        payload = asdict(payload)
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _first_deep_string(payload: object, keys: tuple[str, ...]) -> str | None:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        for value in payload.values():
            found = _first_deep_string(value, keys)
            if found is not None:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _first_deep_string(item, keys)
            if found is not None:
                return found
    return None


def _default_pr_body(run_id: str) -> str:
    return (
        f"Self-improvement candidate branch for `{run_id}`.\n\n"
        "This PR is used by the runner to produce Introspection staging candidate builds. "
        "Individual best-checkpoint summaries can be added after train/test evaluation."
    )
