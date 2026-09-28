from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


def write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_run_progress(
    results_dir: Path,
    *,
    epoch: int,
    phase: str,
    status: str = "running",
    message: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "epoch": epoch,
        "phase": phase,
        "status": status,
        "updated_at": _now(),
    }
    if message:
        payload["message"] = message
    write_atomic_json(results_dir / "metadata" / "progress.json", payload)


class SplitProgress:
    def __init__(self, path: Path, split: str, jobs: list[tuple[str, int]]) -> None:
        self.path = path
        self.lock = Lock()
        self.payload: dict[str, Any] = {
            "split": split,
            "status": "running",
            "started_at": _now(),
            "updated_at": _now(),
            "trials": [
                {"task_id": task_id, "trial": trial, "status": "queued"} for task_id, trial in jobs
            ],
        }
        write_atomic_json(path, self.payload)

    def set_trial(self, task_id: str, trial: int, status: str) -> None:
        with self.lock:
            for item in self.payload["trials"]:
                if item["task_id"] == task_id and item["trial"] == trial:
                    item["status"] = status
                    item["updated_at"] = _now()
                    break
            self.payload["updated_at"] = _now()
            write_atomic_json(self.path, self.payload)

    def finish(self, status: str) -> None:
        with self.lock:
            self.payload["status"] = status
            self.payload["updated_at"] = _now()
            write_atomic_json(self.path, self.payload)
