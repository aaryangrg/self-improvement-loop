from __future__ import annotations

from pathlib import Path
from typing import Any

from .harvey import HarveyTask


def evaluate_outputs(task: HarveyTask, outputs_dir: Path) -> dict[str, Any]:
    files = sorted(path.name for path in outputs_dir.rglob("*") if path.is_file())
    return {
        "status": "not_configured",
        "reason": "Harvey LAB evaluator integration has not been wired yet.",
        "task_id": task.task_id,
        "output_files": files,
    }
