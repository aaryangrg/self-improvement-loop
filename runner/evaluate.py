from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

from .config import RunnerConfig
from .harvey import HarveyTask


def evaluate_outputs(
    task: HarveyTask,
    outputs_dir: Path,
    trial_dir: Path,
    config: RunnerConfig,
) -> dict[str, Any]:
    evaluation_dir = trial_dir / "evaluation"
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(
        str(path.relative_to(outputs_dir)) for path in outputs_dir.rglob("*") if path.is_file()
    )
    if not config.enable_eval:
        return {
            "status": "skipped",
            "reason": "Evaluation disabled by runner config.",
            "task_id": task.task_id,
            "output_files": files,
        }
    if not files:
        return {
            "status": "skipped",
            "reason": "No output files were downloaded to evaluate.",
            "task_id": task.task_id,
            "output_files": files,
        }

    eval_run_id = _harvey_eval_run_id(trial_dir, config.repo_root)
    harvey_run_dir = config.harvey_repo / "results" / eval_run_id
    harvey_output_dir = harvey_run_dir / "output"
    if harvey_output_dir.exists():
        shutil.rmtree(harvey_output_dir)
    harvey_output_dir.mkdir(parents=True, exist_ok=True)
    _copy_tree_contents(outputs_dir, harvey_output_dir)

    cmd = [
        "uv",
        "run",
        "--project",
        str(config.harvey_repo),
        "python",
        "-m",
        "lab_core.evaluation.run_eval",
        "--run-id",
        eval_run_id,
        "--task",
        task.task_id,
        "--judges",
        *config.eval_judges,
        "--parallel",
        str(config.eval_parallel),
    ]
    proc = subprocess.run(
        cmd,
        cwd=config.repo_root,
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    (evaluation_dir / "eval_stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (evaluation_dir / "eval_stderr.txt").write_text(proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        return {
            "status": "error",
            "task_id": task.task_id,
            "harvey_eval_run_id": eval_run_id,
            "output_files": files,
            "returncode": proc.returncode,
            "stderr_excerpt": proc.stderr.strip()[:2000],
        }

    copied: list[str] = []
    for name in ("scores.json", "scores_dual.json", "report.html"):
        source = harvey_run_dir / name
        if source.exists():
            target = evaluation_dir / name
            shutil.copyfile(source, target)
            copied.append(name)

    scores = _load_scores(evaluation_dir)
    return {
        "status": "completed",
        "task_id": task.task_id,
        "harvey_eval_run_id": eval_run_id,
        "output_files": files,
        "evaluation_files": copied,
        "scores": scores,
    }


def _harvey_eval_run_id(trial_dir: Path, repo_root: Path) -> str:
    resolved_repo = repo_root.resolve()
    resolved_trial = trial_dir.resolve()
    try:
        relative = resolved_trial.relative_to(resolved_repo)
    except ValueError:
        relative = Path(trial_dir.name)
    return (Path("introspection") / relative).as_posix()


def _copy_tree_contents(source_dir: Path, target_dir: Path) -> None:
    for source in source_dir.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(source_dir)
        target = target_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _load_scores(evaluation_dir: Path) -> dict[str, Any] | None:
    for name in ("scores.json", "scores_dual.json"):
        path = evaluation_dir / name
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return cast(dict[str, Any], payload)
    return None
