from __future__ import annotations

import json

from .harvey import HarveyTask


def build_prompt(task: HarveyTask) -> str:
    deliverables = json.dumps(task.deliverables, indent=2, sort_keys=True)
    document_list = "\n".join(
        f"- /workspace/files/{document.mount_path}" for document in task.documents
    )
    if not document_list:
        document_list = "- No input documents were discovered for this task."
    return (
        "# Harvey LAB Task\n\n"
        f"Task id: {task.task_id}\n\n"
        f"## Instructions\n\n{task.instructions.strip()}\n\n"
        f"## Deliverables\n\n```json\n{deliverables}\n```\n\n"
        f"## Input Files\n\n{document_list}\n\n"
        "Use only the task instructions, deliverables, and input files listed above. "
        "Do not look for grading criteria or a task JSON file. Write final deliverables "
        "under `/workspace/outputs` with the requested filenames."
    )
