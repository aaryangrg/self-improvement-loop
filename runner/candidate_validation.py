from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .execute import _agent_execution_error
from .introspection import IntrospectionClient
from .recipe_candidate import materialize_agent_configs


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_candidate(candidate: Path, trusted: Path, manifest: Path, record: Path) -> None:
    """Validate an uncommitted recipe through a disposable runtime manifest."""
    manifest_payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest_payload, dict):
        raise ValueError(f"runtime manifest is not a mapping: {manifest}")
    manifest_payload["path"] = "recipe"
    with tempfile.TemporaryDirectory(prefix="recipe-check-") as temp:
        work_dir = Path(temp)
        shutil.copytree(
            candidate,
            work_dir / "recipe",
            symlinks=False,
            ignore=shutil.ignore_patterns(".venv", "node_modules", "__pycache__", "*.pyc"),
        )
        try:
            materialize_agent_configs(work_dir / "recipe", trusted)
        except ValueError as error:
            _write_json(record, {"status": "failed", "message": str(error)})
            raise RuntimeError(f"candidate composition failed: {error}") from error
        manifest_dir = work_dir / ".introspection"
        manifest_dir.mkdir()
        (manifest_dir / "candidate.yaml").write_text(
            yaml.safe_dump(manifest_payload, sort_keys=False),
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                ["introspection", "check", "--work-dir", str(work_dir), "-o", "json"],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            _write_json(record, {"status": "failed", "message": str(error)})
            raise RuntimeError(f"recipe check could not run: {error}") from error
    message = (result.stdout + "\n" + result.stderr).strip()
    _write_json(
        record, {"status": "passed" if result.returncode == 0 else "failed", "message": message}
    )
    if result.returncode != 0:
        raise RuntimeError(f"introspection check rejected candidate: {message[:4000]}")


def smoke_candidate(
    client: IntrospectionClient,
    version_id: str,
    agent: str,
    output_dir: Path,
) -> None:
    """Exercise startup and one agent turn without using a scored sample."""
    output_dir.mkdir(parents=True, exist_ok=False)
    handle: Any = None
    try:
        handle = client.create_task(
            runtime_name="",
            runtime_id=version_id,
            environment="staging",
            agent=agent,
            prompt="Reply with READY. Do not inspect workspace files or create deliverables.",
            files=[],
        )
        _write_json(
            output_dir / "handle.json", {"task_id": handle.task_id, "run_id": handle.run_id}
        )
        client.stream_until_finished(handle.task_id, handle.run_id)
        task = client.get_task(handle.task_id)
        conversation = client.get_conversation(handle.task_id)
        _write_json(output_dir / "task.json", task)
        _write_json(output_dir / "conversation.json", conversation)
        error = _agent_execution_error(conversation, agent)
        if error is not None:
            raise RuntimeError(error)
        items = conversation.get("items") if isinstance(conversation, dict) else None
        if not isinstance(items, list) or not any(
            isinstance(item, dict) and item.get("name") == f"invoke_agent {agent}" for item in items
        ):
            raise RuntimeError("smoke conversation has no root agent invocation")
        metadata = task.get("metadata") if isinstance(task, dict) else None
        if isinstance(metadata, dict) and metadata.get("error"):
            raise RuntimeError(str(metadata["error"]))
    except Exception as error:
        _write_json(output_dir / "smoke.json", {"status": "failed", "message": str(error)})
        raise
    _write_json(output_dir / "smoke.json", {"status": "passed"})
