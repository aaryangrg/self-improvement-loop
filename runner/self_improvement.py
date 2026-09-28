from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from .config import RunnerConfig
from .experiment import run_experiment

RECIPE_NAME = "legal-agent"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class LoopConfig:
    max_epochs: int = 10
    test_every: int = 3


@dataclass(frozen=True)
class ResearcherConfig:
    model: str
    epoch_session_mode: Literal["fresh", "resume"] = "fresh"


@dataclass(frozen=True)
class VerifierConfig:
    model: str
    enabled: bool = True
    max_revision_attempts: int = 2


@dataclass(frozen=True)
class EvaluationConfig:
    judges: tuple[str, ...]
    parallel: int = 1


@dataclass(frozen=True)
class SelfImprovementConfig:
    id: str
    description: str
    seed_recipe: Path
    split_config: Path
    loop: LoopConfig
    evaluation: EvaluationConfig
    researcher: ResearcherConfig
    verifier: VerifierConfig


@dataclass(frozen=True)
class SelfImprovementRun:
    run_id: str
    config_id: str
    working_recipe: Path
    manifest: Path
    results_dir: Path


@dataclass(frozen=True)
class RuntimeMetadata:
    run_id: str
    runtime_name: str
    environment: str
    manifest: str
    working_recipe: str
    runtime_id: str | None = None
    runtime_group_id: str | None = None
    pr_number: int | None = None
    pr_ref: str | None = None
    pr_branch: str | None = None
    pr_url: str | None = None
    bootstrap_commit: str | None = None
    candidate_commit: str | None = None
    candidate_version_id: str | None = None
    updated_at: str | None = None


SplitKind = Literal["train", "test"]


def generate_run_id(now: datetime | None = None) -> str:
    timestamp = now or datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    timestamp = timestamp.astimezone(UTC)
    return f"run-{timestamp.strftime('%Y%m%d-%H%M%S-%f')}"


def load_self_improvement_config(path: Path) -> SelfImprovementConfig:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a mapping")

    loop_payload = _mapping(payload.get("loop", {}), path, "loop")
    evaluation_payload = _mapping(payload.get("evaluation"), path, "evaluation")
    researcher_payload = _mapping(payload.get("researcher", {}), path, "researcher")
    verifier_payload = _mapping(payload.get("verifier", {}), path, "verifier")

    judges_payload = evaluation_payload.get("judges")
    if (
        not isinstance(judges_payload, list)
        or not 1 <= len(judges_payload) <= 2
        or not all(isinstance(judge, str) and judge.strip() for judge in judges_payload)
        or len(set(judges_payload)) != len(judges_payload)
    ):
        raise ValueError(f"{path}: evaluation.judges must list one or two distinct models")

    epoch_session_mode = str(researcher_payload.get("epoch_session_mode", "fresh"))
    if epoch_session_mode not in {"fresh", "resume"}:
        raise ValueError(f"{path}: researcher.epoch_session_mode must be 'fresh' or 'resume'")

    config_id = str(payload.get("id", "")).strip()
    if not config_id:
        raise ValueError(f"{path}: id is required")

    return SelfImprovementConfig(
        id=config_id,
        description=str(payload.get("description", "")),
        seed_recipe=Path(str(payload["seed_recipe"])),
        split_config=Path(str(payload["split_config"])),
        loop=LoopConfig(
            max_epochs=_positive_int(loop_payload, "max_epochs", 10, path),
            test_every=_positive_int(loop_payload, "test_every", 3, path),
        ),
        evaluation=EvaluationConfig(
            judges=tuple(judges_payload),
            parallel=_positive_int(evaluation_payload, "parallel", 1, path),
        ),
        researcher=ResearcherConfig(
            model=_required_model(researcher_payload, "researcher", path),
            epoch_session_mode=epoch_session_mode,  # type: ignore[arg-type]
        ),
        verifier=VerifierConfig(
            model=_required_model(verifier_payload, "verifier", path),
            enabled=bool(verifier_payload.get("enabled", True)),
            max_revision_attempts=_nonnegative_int(
                verifier_payload,
                "max_revision_attempts",
                2,
                path,
            ),
        ),
    )


def bootstrap_run(
    repo_root: Path,
    config_path: Path,
    config: SelfImprovementConfig,
    run_id: str,
) -> SelfImprovementRun:
    validate_run_id(run_id)
    seed_recipe = repo_root / config.seed_recipe
    split_config = repo_root / config.split_config
    if not seed_recipe.exists():
        raise FileNotFoundError(f"seed recipe not found: {seed_recipe}")
    if not split_config.exists():
        raise FileNotFoundError(f"split config not found: {split_config}")

    working_recipe = repo_root / "recipes" / "self-improvement" / run_id / RECIPE_NAME
    manifest = repo_root / ".introspection" / f"self-improvement-{run_id}.yaml"
    results_dir = repo_root / "results" / "self-improvement" / run_id
    baseline_snapshot = results_dir / "references" / "baseline_recipe"

    for path in (working_recipe, manifest, results_dir):
        if path.exists():
            raise FileExistsError(f"self-improvement bootstrap target already exists: {path}")

    working_recipe.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(seed_recipe, working_recipe, ignore=_copy_ignore)

    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest_payload = {
        "name": f"self-improvement-{run_id}",
        "path": _relative_posix(working_recipe, repo_root),
        "description": f"Self-improvement working recipe for {run_id}.",
        "runtime": {"llm_mode": "managed"},
    }
    manifest.write_text(yaml.safe_dump(manifest_payload, sort_keys=False), encoding="utf-8")

    (results_dir / "workspace").mkdir(parents=True)
    (results_dir / "epochs").mkdir()
    (results_dir / "test").mkdir()
    (results_dir / "metadata").mkdir()
    baseline_snapshot.parent.mkdir(parents=True)
    shutil.copytree(seed_recipe, baseline_snapshot, ignore=_copy_ignore)
    _write_workspace_files(results_dir, run_id, config)
    _write_run_metadata(
        results_dir,
        repo_root,
        config_path,
        config,
        run_id,
        working_recipe,
        manifest,
    )

    run = SelfImprovementRun(
        run_id=run_id,
        config_id=config.id,
        working_recipe=working_recipe,
        manifest=manifest,
        results_dir=results_dir,
    )
    initialize_runtime_metadata(repo_root, run)
    return run


def load_runtime_metadata(repo_root: Path, run_id: str) -> RuntimeMetadata:
    validate_run_id(run_id)
    path = _runtime_metadata_path(repo_root, run_id)
    if not path.exists():
        raise FileNotFoundError(f"runtime metadata not found: {path}")
    payload = _load_json(path)
    pr_number_payload = payload.get("pr_number")
    pr_number = int(pr_number_payload) if pr_number_payload is not None else None
    return RuntimeMetadata(
        run_id=str(payload["run_id"]),
        runtime_name=str(payload["runtime_name"]),
        environment=str(payload.get("environment", "staging")),
        manifest=str(payload["manifest"]),
        working_recipe=str(payload["working_recipe"]),
        runtime_id=_optional_str(payload.get("runtime_id")),
        runtime_group_id=_optional_str(payload.get("runtime_group_id")),
        pr_number=pr_number,
        pr_ref=_optional_str(payload.get("pr_ref")),
        pr_branch=_optional_str(payload.get("pr_branch")),
        pr_url=_optional_str(payload.get("pr_url")),
        bootstrap_commit=_optional_str(payload.get("bootstrap_commit")),
        candidate_commit=_optional_str(payload.get("candidate_commit")),
        candidate_version_id=_optional_str(payload.get("candidate_version_id")),
        updated_at=_optional_str(payload.get("updated_at")),
    )


def write_runtime_metadata(repo_root: Path, metadata: RuntimeMetadata) -> RuntimeMetadata:
    validate_run_id(metadata.run_id)
    path = _runtime_metadata_path(repo_root, metadata.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(
        replace(
            metadata,
            pr_ref=_pr_ref(metadata.pr_number, metadata.pr_ref),
            updated_at=datetime.now(UTC).isoformat(),
        )
    )
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return load_runtime_metadata(repo_root, metadata.run_id)


def initialize_runtime_metadata(repo_root: Path, run: SelfImprovementRun) -> RuntimeMetadata:
    return write_runtime_metadata(
        repo_root,
        RuntimeMetadata(
            run_id=run.run_id,
            runtime_name=f"self-improvement-{run.run_id}",
            environment="staging",
            manifest=_relative_posix(run.manifest, repo_root),
            working_recipe=_relative_posix(run.working_recipe, repo_root),
        ),
    )


def record_runtime(
    repo_root: Path,
    run_id: str,
    runtime_id: str,
    runtime_name: str | None = None,
    runtime_group_id: str | None = None,
    environment: str | None = None,
) -> RuntimeMetadata:
    metadata = load_runtime_metadata(repo_root, run_id)
    return write_runtime_metadata(
        repo_root,
        replace(
            metadata,
            runtime_id=runtime_id,
            runtime_name=runtime_name or metadata.runtime_name,
            runtime_group_id=runtime_group_id or metadata.runtime_group_id,
            environment=environment or metadata.environment,
        ),
    )


def record_pr(
    repo_root: Path,
    run_id: str,
    pr_number: int,
    pr_branch: str,
    pr_url: str | None = None,
) -> RuntimeMetadata:
    if pr_number < 1:
        raise ValueError(f"pr_number must be >= 1, got {pr_number}")
    metadata = load_runtime_metadata(repo_root, run_id)
    return write_runtime_metadata(
        repo_root,
        replace(
            metadata,
            pr_number=pr_number,
            pr_ref=f"pr/{pr_number}",
            pr_branch=pr_branch,
            pr_url=pr_url,
        ),
    )


def record_pr_branch(repo_root: Path, run_id: str, pr_branch: str) -> RuntimeMetadata:
    metadata = load_runtime_metadata(repo_root, run_id)
    return write_runtime_metadata(
        repo_root,
        replace(
            metadata,
            pr_branch=pr_branch,
        ),
    )


def record_bootstrap_commit(
    repo_root: Path,
    run_id: str,
    bootstrap_commit: str,
) -> RuntimeMetadata:
    metadata = load_runtime_metadata(repo_root, run_id)
    return write_runtime_metadata(
        repo_root,
        replace(
            metadata,
            bootstrap_commit=bootstrap_commit,
        ),
    )


def record_candidate(
    repo_root: Path,
    run_id: str,
    candidate_commit: str,
    candidate_version_id: str | None = None,
) -> RuntimeMetadata:
    metadata = load_runtime_metadata(repo_root, run_id)
    return write_runtime_metadata(
        repo_root,
        replace(
            metadata,
            candidate_commit=candidate_commit,
            candidate_version_id=candidate_version_id,
        ),
    )


def candidate_commit_paths(repo_root: Path, run_id: str) -> tuple[str, ...]:
    metadata = load_runtime_metadata(repo_root, run_id)
    return (
        _relative_posix(repo_root / metadata.working_recipe, repo_root),
        _relative_posix(repo_root / metadata.manifest, repo_root),
    )


def runner_config_with_runtime_metadata(
    repo_root: Path,
    run_id: str,
    runner_config: RunnerConfig,
) -> RunnerConfig:
    metadata = load_runtime_metadata(repo_root, run_id)
    runtime_id = runner_config.runtime_id or metadata.runtime_id
    if runtime_id is None:
        raise ValueError(
            f"runtime id is required for self-improvement run {run_id}. "
            f"Record it in {_runtime_metadata_path(repo_root, run_id)} or pass --runtime-id."
        )
    return replace(
        runner_config,
        runtime_name=metadata.runtime_name,
        runtime_id=runtime_id,
        environment=runner_config.environment or metadata.environment,
    )


def run_epoch_split(
    repo_root: Path,
    config: SelfImprovementConfig,
    run_id: str,
    epoch: int,
    split_kind: SplitKind,
    runner_config: RunnerConfig,
) -> Path:
    validate_run_id(run_id)
    if epoch < 0:
        raise ValueError(f"epoch must be >= 0, got {epoch}")

    results_dir = repo_root / "results" / "self-improvement" / run_id
    if not results_dir.exists():
        raise FileNotFoundError(
            f"self-improvement run results directory not found: {results_dir}. "
            "Run `self-improve bootstrap` first."
        )

    _pin_evaluation_config(results_dir, config.evaluation)
    runner_config = replace(
        runner_config,
        eval_judges=config.evaluation.judges,
        eval_parallel=config.evaluation.parallel,
    )
    parent = results_dir / ("epochs" if split_kind == "train" else "test")
    return run_experiment(
        config_path=repo_root / config.split_config,
        split=split_kind,
        runner_config=runner_config,
        experiment_run_id=f"epoch-{epoch:03d}",
        results_root=parent,
        include_split_subdir=False,
    )


def _pin_evaluation_config(results_dir: Path, evaluation: EvaluationConfig) -> None:
    path = results_dir / "metadata" / "evaluation.json"
    expected = {"judges": list(evaluation.judges), "parallel": evaluation.parallel}
    if path.exists():
        if _load_json(path) != expected:
            raise ValueError(f"evaluation config differs from the pinned settings in {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def refresh_metrics(repo_root: Path, run_id: str) -> list[dict[str, Any]]:
    validate_run_id(run_id)
    results_dir = repo_root / "results" / "self-improvement" / run_id
    if not results_dir.exists():
        raise FileNotFoundError(f"self-improvement results directory not found: {results_dir}")

    rows: list[dict[str, Any]] = []
    for train_aggregate in sorted((results_dir / "epochs").glob("epoch-*/aggregate.json")):
        epoch = _parse_epoch(train_aggregate.parent.name)
        train = _load_json(train_aggregate)
        test_path = results_dir / "test" / f"epoch-{epoch:03d}" / "aggregate.json"
        test = _load_json(test_path) if test_path.exists() else None
        rows.append(_metric_row(epoch, train, test))

    metadata_dir = results_dir / "metadata"
    metadata_dir.mkdir(exist_ok=True)
    _write_metrics_jsonl(metadata_dir / "metrics.jsonl", rows)
    _write_metrics_csv(metadata_dir / "metrics.csv", rows)
    return rows


def update_best_candidate(repo_root: Path, run_id: str) -> dict[str, Any]:
    validate_run_id(run_id)
    metadata_dir = repo_root / "results" / "self-improvement" / run_id / "metadata"
    metrics_path = metadata_dir / "metrics.jsonl"
    if not metrics_path.exists():
        raise FileNotFoundError(f"metrics file not found: {metrics_path}")

    rows = [
        json.loads(line)
        for line in metrics_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError(f"metrics file has no rows: {metrics_path}")

    best_row = max(
        rows,
        key=lambda row: (
            _score_value(row.get("train_task_pass_rate")),
            _score_value(row.get("train_rubric_pass_rate")),
        ),
    )
    previous = (
        _load_json(metadata_dir / "best_candidate.json")
        if (metadata_dir / "best_candidate.json").exists()
        else {}
    )
    best_epoch = int(_required_epoch(best_row))
    same_best = previous.get("best_epoch") == best_epoch
    best_score = (
        _score_value(best_row.get("train_task_pass_rate")),
        _score_value(best_row.get("train_rubric_pass_rate")),
    )
    previous_score = (
        (
            _score_value(previous.get("train_task_pass_rate")),
            _score_value(previous.get("train_rubric_pass_rate")),
        )
        if previous
        else None
    )
    is_new_best = previous_score is None or best_score > previous_score
    preserve_checkpoint = same_best and not is_new_best
    payload = {
        "run_id": run_id,
        "selection_metric": "train_task_pass_rate",
        "tie_breaker_metric": "train_rubric_pass_rate",
        "best_epoch": best_epoch,
        "train_task_pass_rate": best_row.get("train_task_pass_rate"),
        "train_rubric_pass_rate": best_row.get("train_rubric_pass_rate"),
        "is_new_best": is_new_best,
        "previous_best_epoch": previous.get("best_epoch"),
        "previous_train_task_pass_rate": previous.get("train_task_pass_rate"),
        "previous_train_rubric_pass_rate": previous.get("train_rubric_pass_rate"),
        "checkpoint_created": (
            bool(previous.get("checkpoint_created", False)) if preserve_checkpoint else False
        ),
        "checkpoint_commit": previous.get("checkpoint_commit") if preserve_checkpoint else None,
        "pr_created": bool(previous.get("pr_created", False)) if preserve_checkpoint else False,
        "pr_url": previous.get("pr_url") if preserve_checkpoint else None,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    (metadata_dir / "best_candidate.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _required_epoch(row: dict[str, Any]) -> int:
    if "epoch" not in row:
        raise ValueError("metrics row missing epoch")
    value = row["epoch"]
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"epoch must be an integer, got {type(value).__name__}")


def _score_value(value: object) -> float:
    if value is None:
        return -1.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise TypeError(f"score value must be numeric, got {type(value).__name__}")


def _parse_epoch(name: str) -> int:
    match = re.fullmatch(r"epoch-(\d+)", name)
    if match is None:
        raise ValueError(f"invalid epoch directory name: {name}")
    return int(match.group(1))


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _runtime_metadata_path(repo_root: Path, run_id: str) -> Path:
    return repo_root / "results" / "self-improvement" / run_id / "metadata" / "runtime.json"


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _pr_ref(pr_number: int | None, existing: str | None) -> str | None:
    if pr_number is None:
        return existing
    return f"pr/{pr_number}"


def _metric_row(epoch: int, train: dict[str, Any], test: dict[str, Any] | None) -> dict[str, Any]:
    row: dict[str, Any] = {"epoch": epoch}
    for prefix, payload in (("train", train), ("test", test)):
        row.update(_prefixed_metrics(prefix, payload))
    return row


def _prefixed_metrics(prefix: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    keys = (
        "task_pass_rate",
        "rubric_pass_rate",
        "number_of_tasks",
        "number_of_evaluated_tasks",
        "number_of_unevaluated_tasks",
        "number_of_trials",
        "number_of_scored_trials",
        "number_of_failed_trials",
    )
    return {f"{prefix}_{key}": payload.get(key) if payload is not None else None for key in keys}


def _write_metrics_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_metrics_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "epoch",
        "train_task_pass_rate",
        "train_rubric_pass_rate",
        "train_number_of_tasks",
        "train_number_of_evaluated_tasks",
        "train_number_of_unevaluated_tasks",
        "train_number_of_trials",
        "train_number_of_scored_trials",
        "train_number_of_failed_trials",
        "test_task_pass_rate",
        "test_rubric_pass_rate",
        "test_number_of_tasks",
        "test_number_of_evaluated_tasks",
        "test_number_of_unevaluated_tasks",
        "test_number_of_trials",
        "test_number_of_scored_trials",
        "test_number_of_failed_trials",
    ]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(
            "run_id must start with an alphanumeric character and contain only "
            "letters, numbers, dots, underscores, or hyphens"
        )


def _write_workspace_files(
    results_dir: Path,
    run_id: str,
    config: SelfImprovementConfig,
) -> None:
    workspace = results_dir / "workspace"
    (workspace / "journal.md").write_text(
        f"# Research Journal: {run_id}\n\n"
        f"Config: `{config.id}`\n\n"
        "Use this file to record observations, hypotheses, decisions, and open questions.\n",
        encoding="utf-8",
    )
    (workspace / "hypotheses.md").write_text(
        "# Hypotheses\n\n" "| ID | Epoch | Hypothesis | Status |\n" "| --- | ---: | --- | --- |\n",
        encoding="utf-8",
    )
    (workspace / "experiments.md").write_text(
        "# Experiments\n\n"
        "| Epoch | Hypothesis | Change | Result | Decision |\n"
        "| ---: | --- | --- | --- | --- |\n",
        encoding="utf-8",
    )
    (workspace / "current_plan.md").write_text(
        "# Current Plan\n\n" "No active plan yet.\n",
        encoding="utf-8",
    )


def _write_run_metadata(
    results_dir: Path,
    repo_root: Path,
    config_path: Path,
    config: SelfImprovementConfig,
    run_id: str,
    working_recipe: Path,
    manifest: Path,
) -> None:
    payload = {
        "run_id": run_id,
        "config": asdict(config),
        "config_path": _relative_posix(config_path, repo_root),
        "working_recipe": _relative_posix(working_recipe, repo_root),
        "manifest": _relative_posix(manifest, repo_root),
        "results_dir": _relative_posix(results_dir, repo_root),
        "created_at": datetime.now(UTC).isoformat(),
    }
    (results_dir / "metadata" / "run.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _mapping(payload: object, path: Path, key: str) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: {key} must be a mapping")
    return payload


def _required_model(payload: dict[str, Any], role: str, path: Path) -> str:
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError(f"{path}: {role}.model must be a non-empty string")
    return model.strip()


def _positive_int(payload: dict[str, Any], key: str, default: int, path: Path) -> int:
    value = int(payload.get(key, default))
    if value < 1:
        raise ValueError(f"{path}: {key} must be >= 1")
    return value


def _nonnegative_int(payload: dict[str, Any], key: str, default: int, path: Path) -> int:
    value = int(payload.get(key, default))
    if value < 0:
        raise ValueError(f"{path}: {key} must be >= 0")
    return value


def _relative_posix(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".venv",
        "node_modules",
    }
    return {name for name in names if name in ignored}
