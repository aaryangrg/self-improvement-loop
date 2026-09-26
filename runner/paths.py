from pathlib import Path


def safe_task_id(task_id: str) -> str:
    return task_id.strip("/").replace("/", "__")


def task_result_dir(results_root: Path, task_id: str, trial: int) -> Path:
    if trial < 1:
        raise ValueError(f"trial must be >= 1, got {trial}")
    return results_root / safe_task_id(task_id) / f"trial-{trial:03d}"
