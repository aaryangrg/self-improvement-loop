from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UploadedFile:
    id: str
    name: str
    mount_path: str


@dataclass(frozen=True)
class TaskHandle:
    task_id: str
    run_id: str | None


class IntrospectionClient:
    def __init__(self, repo_root: Path = Path(".")) -> None:
        self.repo_root = repo_root

    def _json(self, args: list[str], timeout: int = 300) -> Any:
        proc = subprocess.run(
            ["introspection", *args, "-o", "json"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            excerpt = ((proc.stderr or "") + (proc.stdout or "")).strip()[:1000]
            raise RuntimeError(f"`introspection {' '.join(args)}` failed: {excerpt}")
        return json.loads(proc.stdout)

    def upload_file(self, path: Path, mount_path: str) -> UploadedFile:
        payload = self._json(["files", "upload", "--file", str(path), "--file-type", "upload"])
        file_id = str(payload.get("id") or payload.get("file", {}).get("id"))
        name = str(payload.get("name") or path.name)
        return UploadedFile(id=file_id, name=name, mount_path=mount_path)

    def get_file(self, file_id: str) -> Any:
        return self._json(["files", "get", file_id], timeout=120)

    def create_task(
        self,
        runtime_name: str,
        runtime_id: str | None,
        environment: str,
        agent: str,
        prompt: str,
        files: list[UploadedFile],
    ) -> TaskHandle:
        args = [
            "tasks",
            "create",
        ]
        if runtime_id is None:
            args.extend(["--runtime", runtime_name])
        else:
            args.extend(["--runtime-id", runtime_id])
        args.extend(
            [
                "--environment",
                environment,
                "--agent",
                agent,
                "--prompt",
                prompt,
            ]
        )
        for uploaded in files:
            args.extend(["--file", f"{uploaded.id}={uploaded.mount_path}"])
        payload = self._json(args)
        task = payload.get("task") or payload
        run = payload.get("run") or {}
        return TaskHandle(task_id=str(task["id"]), run_id=str(run.get("id") or ""))

    def stream_until_finished(self, task_id: str, run_id: str | None) -> None:
        run_ref = run_id or "current"
        proc = subprocess.run(
            ["introspection", "tasks", "stream", task_id, "--run", run_ref, "--since", "0"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        if proc.returncode != 0:
            excerpt = ((proc.stderr or "") + (proc.stdout or "")).strip()[:1000]
            raise RuntimeError(f"`introspection tasks stream` failed: {excerpt}")

    def get_conversation(self, task_id: str) -> Any:
        return self._json(["conversations", "get", task_id], timeout=180)

    def get_task(self, task_id: str) -> Any:
        return self._json(["tasks", "get", task_id], timeout=120)

    def download_output_files(self, task_payload: Any, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        files = task_payload.get("output_files") or task_payload.get("outputs") or []
        downloaded: list[Path] = []
        for item in files:
            file_id = str(item.get("id") or item.get("file_id"))
            name = str(item.get("name") or file_id)
            target = output_dir / name
            proc = subprocess.run(
                ["introspection", "files", "content", file_id, "--out", str(target)],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if proc.returncode != 0:
                excerpt = ((proc.stderr or "") + (proc.stdout or "")).strip()[:1000]
                raise RuntimeError(f"`introspection files content {file_id}` failed: {excerpt}")
            downloaded.append(target)
        return downloaded
