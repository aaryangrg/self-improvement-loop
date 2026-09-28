from __future__ import annotations

import json
import shutil
import traceback
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .config import RunnerConfig
from .evaluate import evaluate_outputs
from .execute import run_one
from .harvey import load_task
from .paths import task_result_dir
from .progress import SplitProgress


@dataclass(frozen=True)
class SplitDefaults:
    trials_per_task: int = 1
    task_concurrency: int = 3
    eval_concurrency: int = 5


@dataclass(frozen=True)
class SplitDefinition:
    task_ids: tuple[str, ...]


@dataclass(frozen=True)
class SplitConfig:
    id: str
    description: str
    defaults: SplitDefaults
    splits: dict[str, SplitDefinition]


@dataclass(frozen=True)
class TrialResult:
    task_id: str
    trial: int
    trial_dir: str
    status: str
    scored: bool
    tasks_passed: int
    tasks_evaluated: int
    rubrics_passed: int
    rubrics_evaluated: int
    error: str | None = None


@dataclass(frozen=True)
class TaskSummary:
    task_id: str
    task_dir: str
    number_of_trials: int
    number_of_scored_trials: int
    number_of_failed_trials: int
    tasks_passed: int
    tasks_evaluated: int
    rubrics_passed: int
    rubrics_evaluated: int
    task_pass_rate: float | None
    rubric_pass_rate: float | None
    trial_summary_files: tuple[str, ...]
    score_files: tuple[str, ...]


class IncompleteExperimentError(RuntimeError):
    pass


def load_split_config(path: Path) -> SplitConfig:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a mapping")

    defaults_payload = payload.get("defaults", {})
    if defaults_payload is None:
        defaults_payload = {}
    if not isinstance(defaults_payload, dict):
        raise ValueError(f"{path}: defaults must be a mapping")

    defaults = SplitDefaults(
        trials_per_task=_int_default(defaults_payload, "trials_per_task", 1),
        task_concurrency=_int_default(
            defaults_payload,
            "task_concurrency",
            _int_default(defaults_payload, "max_concurrency", 3),
        ),
        eval_concurrency=_int_default(defaults_payload, "eval_concurrency", 5),
    )
    if defaults.trials_per_task < 1:
        raise ValueError(f"{path}: defaults.trials_per_task must be >= 1")
    if defaults.task_concurrency < 1:
        raise ValueError(f"{path}: defaults.task_concurrency must be >= 1")
    if defaults.eval_concurrency < 1:
        raise ValueError(f"{path}: defaults.eval_concurrency must be >= 1")

    splits_payload = payload.get("splits", {})
    if not isinstance(splits_payload, dict) or not splits_payload:
        raise ValueError(f"{path}: splits must be a non-empty mapping")

    splits: dict[str, SplitDefinition] = {}
    for split_name, split_payload in splits_payload.items():
        if not isinstance(split_name, str):
            raise ValueError(f"{path}: split names must be strings")
        if not isinstance(split_payload, dict):
            raise ValueError(f"{path}: split {split_name!r} must be a mapping")
        task_ids_payload = split_payload.get("task_ids", [])
        if not isinstance(task_ids_payload, list) or not all(
            isinstance(task_id, str) for task_id in task_ids_payload
        ):
            raise ValueError(f"{path}: split {split_name!r} task_ids must be a list of strings")
        splits[split_name] = SplitDefinition(task_ids=tuple(task_ids_payload))

    return SplitConfig(
        id=str(payload["id"]),
        description=str(payload.get("description", "")),
        defaults=defaults,
        splits=splits,
    )


def make_experiment_run_id(config_id: str, split: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}_{config_id}_{split}"


def run_experiment(
    config_path: Path,
    split: str,
    runner_config: RunnerConfig,
    experiment_run_id: str | None = None,
    results_root: Path = Path("results/experiment_runs"),
    include_split_subdir: bool = True,
) -> Path:
    split_config = load_split_config(config_path)
    if split not in split_config.splits:
        available = ", ".join(sorted(split_config.splits))
        raise ValueError(f"Split {split!r} not found in {config_path}; available: {available}")

    run_id = experiment_run_id or make_experiment_run_id(split_config.id, split)
    run_dir = results_root / run_id
    split_dir = run_dir / split if include_split_subdir else run_dir
    run_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(config_path, run_dir / "split_config.yaml")

    run_metadata = {
        "experiment_run_id": run_id,
        "split_config_id": split_config.id,
        "split_config_path": str(config_path),
        "split": split,
        "description": split_config.description,
        "started_at": datetime.now(UTC).isoformat(),
        "runtime": {
            "runtime_name": runner_config.runtime_name,
            "runtime_id": runner_config.runtime_id,
            "environment": runner_config.environment,
            "agent": runner_config.agent,
        },
        "evaluation": {
            "judges": list(runner_config.eval_judges),
            "parallel": runner_config.eval_parallel,
            "reasoning_effort": runner_config.eval_reasoning_effort,
            "enabled": runner_config.enable_eval,
        },
        "defaults": asdict(split_config.defaults),
    }
    _write_json(run_dir / "run.json", run_metadata)

    jobs = [
        (task_id, trial)
        for task_id in split_config.splits[split].task_ids
        for trial in range(1, split_config.defaults.trials_per_task + 1)
    ]
    progress = SplitProgress(split_dir / "progress.json", split, jobs)

    trial_results: list[TrialResult] = []
    task_config = replace(runner_config, results_root=split_dir, enable_eval=False)
    eval_config = replace(runner_config, results_root=split_dir)
    with (
        ThreadPoolExecutor(max_workers=split_config.defaults.task_concurrency) as task_pool,
        ThreadPoolExecutor(max_workers=split_config.defaults.eval_concurrency) as eval_pool,
    ):
        pending: dict[Future[Any], tuple[str, str, int]] = {
            task_pool.submit(_run_with_progress, progress, task_id, trial, task_config): (
                "task",
                task_id,
                trial,
            )
            for task_id, trial in jobs
        }
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                phase, requested_task_id, requested_trial = pending.pop(future)
                result = future.result()
                if phase == "task" and not isinstance(result, TrialResult):
                    task_id, trial, trial_dir = result
                    if runner_config.enable_eval:
                        progress.set_trial(
                            requested_task_id, requested_trial, "awaiting_evaluation"
                        )
                        pending[
                            eval_pool.submit(
                                _evaluate_with_progress,
                                progress,
                                requested_task_id,
                                requested_trial,
                                task_id,
                                trial,
                                trial_dir,
                                eval_config,
                            )
                        ] = ("eval", requested_task_id, requested_trial)
                    else:
                        completed = _trial_result_from_dir(task_id, trial, trial_dir)
                        trial_results.append(completed)
                        progress.set_trial(requested_task_id, requested_trial, completed.status)
                else:
                    trial_results.append(result)
                    progress.set_trial(requested_task_id, requested_trial, result.status)
                _write_json(
                    split_dir / "aggregate.json", build_aggregate(run_id, split, trial_results)
                )

    aggregate = build_aggregate(run_id, split, trial_results)
    aggregate["finished_at"] = datetime.now(UTC).isoformat()
    _write_json(split_dir / "aggregate.json", aggregate)
    if runner_config.enable_eval and (
        not jobs
        or aggregate["number_of_scored_trials"] != len(jobs)
        or aggregate["number_of_evaluated_tasks"] != len(split_config.splits[split].task_ids)
    ):
        progress.finish("incomplete")
        raise IncompleteExperimentError(
            f"{split} incomplete: {aggregate['number_of_scored_trials']}/{len(jobs)} trials "
            f"scored, {aggregate['number_of_evaluated_tasks']}/"
            f"{len(split_config.splits[split].task_ids)} tasks evaluated. "
            f"Results: {split_dir / 'aggregate.json'}"
        )
    progress.finish("completed")
    return run_dir


def _run_with_progress(
    progress: SplitProgress, task_id: str, trial: int, config: RunnerConfig
) -> tuple[str, int, Path] | TrialResult:
    progress.set_trial(task_id, trial, "executing")
    return _run_trial_without_eval(task_id, trial, config)


def _evaluate_with_progress(
    progress: SplitProgress,
    requested_task_id: str,
    requested_trial: int,
    task_id: str,
    trial: int,
    trial_dir: Path,
    config: RunnerConfig,
) -> TrialResult:
    progress.set_trial(requested_task_id, requested_trial, "evaluating")
    return _evaluate_trial(task_id, trial, trial_dir, config)


def _int_default(payload: dict[Any, Any], key: str, default: int) -> int:
    value: Any = payload.get(key, default)
    if value is None:
        return default
    return int(value)


def _run_trial_without_eval(
    task_id: str, trial: int, config: RunnerConfig
) -> tuple[str, int, Path] | TrialResult:
    try:
        resolved_task_id = load_task(config, task_id).task_id
        trial_dir = run_one(task_id, trial, config)
        outputs_dir = trial_dir / "outputs"
        if not any(path.is_file() for path in outputs_dir.rglob("*")):
            return _trial_result_from_dir(resolved_task_id, trial, trial_dir)
        return resolved_task_id, trial, trial_dir
    except Exception as error:
        trial_dir = _fallback_trial_dir(task_id, trial, config)
        trial_dir.mkdir(parents=True, exist_ok=True)
        error_payload = {
            "status": "failed",
            "type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
        }
        _write_json(trial_dir / "error.json", error_payload)
        result = TrialResult(
            task_id=task_id,
            trial=trial,
            trial_dir=str(trial_dir),
            status="failed",
            scored=False,
            tasks_passed=0,
            tasks_evaluated=0,
            rubrics_passed=0,
            rubrics_evaluated=0,
            error=f"{type(error).__name__}: {error}",
        )
        _write_json(trial_dir / "trial_summary.json", _trial_summary_payload(result))
        return result


def _evaluate_trial(
    task_id: str,
    trial: int,
    trial_dir: Path,
    config: RunnerConfig,
) -> TrialResult:
    try:
        task = load_task(config, task_id)

        evaluation = evaluate_outputs(task, trial_dir / "outputs", trial_dir, config)
        _write_json(trial_dir / "evaluation.json", evaluation)
        return _trial_result_from_dir(task_id, trial, trial_dir)
    except Exception as error:
        error_payload = {
            "status": "failed",
            "type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
        }
        _write_json(trial_dir / "evaluation_error.json", error_payload)
        result = TrialResult(
            task_id=task_id,
            trial=trial,
            trial_dir=str(trial_dir),
            status="failed",
            scored=False,
            tasks_passed=0,
            tasks_evaluated=0,
            rubrics_passed=0,
            rubrics_evaluated=0,
            error=f"{type(error).__name__}: {error}",
        )
        _write_json(trial_dir / "trial_summary.json", _trial_summary_payload(result))
        return result


def _fallback_trial_dir(task_id: str, trial: int, config: RunnerConfig) -> Path:
    try:
        resolved_task_id = load_task(config, task_id).task_id
    except Exception:
        resolved_task_id = task_id
    return task_result_dir(config.results_root, resolved_task_id, trial)


def _trial_result_from_dir(task_id: str, trial: int, trial_dir: Path) -> TrialResult:
    scores_path = trial_dir / "evaluation" / "scores.json"
    if scores_path.exists():
        scores = json.loads(scores_path.read_text(encoding="utf-8"))
        n_criteria = int(scores.get("n_criteria", 0))
        n_passed = int(scores.get("n_passed", 0))
        result = TrialResult(
            task_id=task_id,
            trial=trial,
            trial_dir=str(trial_dir),
            status="scored",
            scored=True,
            tasks_passed=1 if scores.get("all_pass", False) else 0,
            tasks_evaluated=1,
            rubrics_passed=n_passed,
            rubrics_evaluated=n_criteria,
        )
        _write_json(trial_dir / "trial_summary.json", _trial_summary_payload(result))
        return result

    evaluation_path = trial_dir / "evaluation.json"
    if evaluation_path.exists():
        evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
        reason = str(evaluation.get("reason") or evaluation.get("stderr_excerpt") or "")
        result = TrialResult(
            task_id=task_id,
            trial=trial,
            trial_dir=str(trial_dir),
            status="failed",
            scored=False,
            tasks_passed=0,
            tasks_evaluated=0,
            rubrics_passed=0,
            rubrics_evaluated=0,
            error=reason or None,
        )
        _write_json(trial_dir / "trial_summary.json", _trial_summary_payload(result))
        return result

    result = TrialResult(
        task_id=task_id,
        trial=trial,
        trial_dir=str(trial_dir),
        status="failed",
        scored=False,
        tasks_passed=0,
        tasks_evaluated=0,
        rubrics_passed=0,
        rubrics_evaluated=0,
    )
    _write_json(trial_dir / "trial_summary.json", _trial_summary_payload(result))
    return result


def build_aggregate(run_id: str, split: str, trial_results: list[TrialResult]) -> dict[str, Any]:
    task_summaries = _build_task_summaries(trial_results)
    task_pass_rates = [
        summary.task_pass_rate for summary in task_summaries if summary.task_pass_rate is not None
    ]
    rubric_pass_rates = [
        summary.rubric_pass_rate
        for summary in task_summaries
        if summary.rubric_pass_rate is not None
    ]
    return {
        "experiment_run_id": run_id,
        "split": split,
        "number_of_tasks": len(task_summaries),
        "number_of_evaluated_tasks": len(
            [summary for summary in task_summaries if summary.number_of_scored_trials > 0]
        ),
        "number_of_unevaluated_tasks": len(
            [summary for summary in task_summaries if summary.number_of_scored_trials == 0]
        ),
        "number_of_trials": len(trial_results),
        "number_of_scored_trials": sum(
            summary.number_of_scored_trials for summary in task_summaries
        ),
        "number_of_failed_trials": sum(
            summary.number_of_failed_trials for summary in task_summaries
        ),
        "task_pass_rate": _average(task_pass_rates),
        "rubric_pass_rate": _average(rubric_pass_rates),
        "task_summary_files": [
            str(Path(summary.task_dir) / "task_summary.json") for summary in task_summaries
        ],
    }


def _trial_summary_payload(result: TrialResult) -> dict[str, Any]:
    return {
        "task_id": result.task_id,
        "trial": result.trial,
        "status": result.status,
        "scored": result.scored,
        "tasks_passed": result.tasks_passed,
        "tasks_evaluated": result.tasks_evaluated,
        "rubrics_passed": result.rubrics_passed,
        "rubrics_evaluated": result.rubrics_evaluated,
        "task_pass_rate": _rate(result.tasks_passed, result.tasks_evaluated),
        "rubric_pass_rate": _rate(result.rubrics_passed, result.rubrics_evaluated),
        "error": result.error,
    }


def _build_task_summaries(trial_results: list[TrialResult]) -> list[TaskSummary]:
    grouped: dict[str, list[TrialResult]] = {}
    for result in trial_results:
        grouped.setdefault(result.task_id, []).append(result)

    summaries: list[TaskSummary] = []
    for task_id, results in sorted(grouped.items()):
        summary = _task_summary(task_id, results)
        summaries.append(summary)
        _write_json(Path(summary.task_dir) / "task_summary.json", asdict(summary))
    return summaries


def _task_summary(task_id: str, results: list[TrialResult]) -> TaskSummary:
    scored = [result for result in results if result.scored]
    tasks_passed = sum(result.tasks_passed for result in scored)
    tasks_evaluated = sum(result.tasks_evaluated for result in scored)
    rubrics_passed = sum(result.rubrics_passed for result in scored)
    rubrics_evaluated = sum(result.rubrics_evaluated for result in scored)
    task_dir = str(Path(results[0].trial_dir).parent) if results else ""
    task_dir_path = Path(task_dir)
    return TaskSummary(
        task_id=task_id,
        task_dir=task_dir,
        number_of_trials=len(results),
        number_of_scored_trials=len(scored),
        number_of_failed_trials=len(results) - len(scored),
        tasks_passed=tasks_passed,
        tasks_evaluated=tasks_evaluated,
        rubrics_passed=rubrics_passed,
        rubrics_evaluated=rubrics_evaluated,
        task_pass_rate=_rate(tasks_passed, tasks_evaluated),
        rubric_pass_rate=_rate(rubrics_passed, rubrics_evaluated),
        trial_summary_files=tuple(
            str(Path(result.trial_dir).relative_to(task_dir_path) / "trial_summary.json")
            for result in sorted(results, key=lambda item: item.trial)
        ),
        score_files=tuple(
            str(Path(result.trial_dir).relative_to(task_dir_path) / "evaluation" / "scores.json")
            for result in sorted(scored, key=lambda item: item.trial)
            if (Path(result.trial_dir) / "evaluation" / "scores.json").exists()
        ),
    )


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rate(passed: int, evaluated: int) -> float | None:
    return passed / evaluated if evaluated else None


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
