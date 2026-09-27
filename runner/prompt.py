from __future__ import annotations

from .harvey import HarveyTask


def build_prompt(task: HarveyTask) -> str:
    return (
        f"{task.instructions.strip()}\n\n"
        "Input files are available under `/workspace/files`. Inspect that directory "
        "and use the files relevant to the task.\n\n"
        "Write final deliverables under `/workspace/outputs`."
    )
