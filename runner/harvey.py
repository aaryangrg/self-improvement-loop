from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from .config import RunnerConfig


class HarveyTaskJson(TypedDict):
    instructions: NotRequired[str]
    deliverables: NotRequired[dict[str, str]]
    docs_dir: NotRequired[str]


@dataclass(frozen=True)
class HarveyDocument:
    local_path: Path
    cache_key: str
    mount_path: str


@dataclass(frozen=True)
class HarveyTask:
    task_id: str
    instructions: str
    deliverables: dict[str, str]
    document_root: Path
    documents: list[HarveyDocument]


def resolve_task_dir(harvey_repo: Path, task_id: str) -> Path:
    tasks_root = harvey_repo / "tasks"
    if not tasks_root.exists():
        raise FileNotFoundError(
            f"Harvey LAB tasks directory not found at {tasks_root}. "
            "Add or initialize the harvey-labs submodule first."
        )

    direct = tasks_root / task_id
    if (direct / "task.json").exists():
        return direct

    matches = [path.parent for path in tasks_root.rglob("task.json") if path.parent.name == task_id]
    if not matches:
        raise FileNotFoundError(f"Could not resolve Harvey task id {task_id!r} under {tasks_root}")
    if len(matches) > 1:
        rendered = ", ".join(str(path.relative_to(tasks_root)) for path in matches)
        raise ValueError(
            f"Ambiguous Harvey task id {task_id!r}; use a full path. Matches: {rendered}"
        )
    return matches[0]


def load_task(config: RunnerConfig, task_id: str) -> HarveyTask:
    task_dir = resolve_task_dir(config.harvey_repo, task_id)
    payload = cast(
        HarveyTaskJson,
        json.loads((task_dir / "task.json").read_text(encoding="utf-8")),
    )
    documents_dir = task_dir / "documents"
    if docs_dir := payload.get("docs_dir"):
        documents_dir = (task_dir / docs_dir).resolve()

    document_paths = (
        sorted(path for path in documents_dir.rglob("*") if path.is_file())
        if documents_dir.exists()
        else []
    )
    harvey_root = config.harvey_repo.resolve()
    document_root = documents_dir.resolve()
    documents = [
        HarveyDocument(
            local_path=path,
            cache_key=path.resolve().relative_to(harvey_root).as_posix(),
            mount_path=path.resolve().relative_to(document_root).as_posix(),
        )
        for path in document_paths
    ]
    return HarveyTask(
        task_id=str(task_dir.relative_to(config.harvey_repo / "tasks")),
        instructions=payload.get("instructions", ""),
        deliverables=payload.get("deliverables", {}),
        document_root=document_root,
        documents=documents,
    )
