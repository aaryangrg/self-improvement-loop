from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .incidents import RunIncidents, is_probably_transient


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
    def __init__(
        self,
        repo_root: Path = Path("."),
        incidents: RunIncidents | None = None,
        cli_retries: int = 2,
        download_retries: int = 2,
        stream_reattaches: int = 2,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self.repo_root = repo_root
        self.incidents = incidents
        self.cli_retries = cli_retries
        self.download_retries = download_retries
        self.stream_reattaches = stream_reattaches
        self.retry_backoff_seconds = retry_backoff_seconds

    def _json(self, args: list[str], timeout: int = 300, retries: int | None = None) -> Any:
        attempts = (self.cli_retries if retries is None else retries) + 1
        last_error: RuntimeError | None = None
        for attempt in range(attempts):
            try:
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
            except (json.JSONDecodeError, subprocess.TimeoutExpired, RuntimeError) as error:
                message = _format_error(error)
                last_error = RuntimeError(message)
                if attempt >= attempts - 1 or not is_probably_transient(message):
                    if self.incidents is not None:
                        self.incidents.record_platform_failure(message)
                    raise last_error from error
                if self.incidents is not None:
                    self.incidents.cli_retries += 1
                time.sleep(self.retry_backoff_seconds * (2**attempt))
        raise last_error or RuntimeError("introspection command failed")

    def upload_file(self, path: Path, mount_path: str) -> UploadedFile:
        payload = self._json(["files", "upload", "--file", str(path), "--file-type", "upload"])
        file_id = str(payload.get("id") or payload.get("file", {}).get("id"))
        name = str(payload.get("name") or path.name)
        return UploadedFile(id=file_id, name=name, mount_path=mount_path)

    def get_file(self, file_id: str) -> Any:
        return self._json(["files", "get", file_id], timeout=120)

    def create_runtime(self, manifest: Path) -> Any:
        return self._json(
            ["runtimes", "create", "--manifest", str(manifest), "--yes"],
            timeout=1800,
            retries=0,
        )

    def pin_runtime_branch(self, runtime_id: str, branch: str) -> Any:
        return self._json(
            ["runtimes", "pin", runtime_id, "--branch", branch, "--yes"],
            timeout=300,
            retries=0,
        )

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
        payload = self._json(args, retries=0)
        task = payload.get("task") or payload
        run = payload.get("run") or {}
        return TaskHandle(task_id=str(task["id"]), run_id=str(run.get("id") or ""))

    def stream_until_finished(self, task_id: str, run_id: str | None) -> None:
        run_ref = run_id or "current"
        attempts = self.stream_reattaches + 1
        for attempt in range(attempts):
            try:
                proc = subprocess.run(
                    ["introspection", "tasks", "stream", task_id, "--run", run_ref, "--since", "0"],
                    cwd=self.repo_root,
                    capture_output=True,
                    text=True,
                    timeout=1800,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                message = _format_error(error)
                if attempt >= attempts - 1:
                    if self.incidents is not None:
                        self.incidents.record_platform_failure(message)
                    raise RuntimeError(message) from error
                if self.incidents is not None:
                    self.incidents.stream_reattaches += 1
                time.sleep(self.retry_backoff_seconds * (2**attempt))
                continue

            if proc.returncode == 0:
                return

            excerpt = ((proc.stderr or "") + (proc.stdout or "")).strip()[:1000]
            message = f"`introspection tasks stream` failed: {excerpt}"
            if self._task_is_terminal(task_id):
                return
            if attempt >= attempts - 1 or not is_probably_transient(message):
                if self.incidents is not None:
                    self.incidents.record_platform_failure(message)
                raise RuntimeError(message)
            if self.incidents is not None:
                self.incidents.stream_reattaches += 1
            time.sleep(self.retry_backoff_seconds * (2**attempt))

    def get_conversation(self, task_id: str) -> Any:
        return self._json(["conversations", "get", task_id], timeout=180)

    def get_task(self, task_id: str) -> Any:
        return self._json(["tasks", "get", task_id], timeout=120)

    def list_generated_files(self, task_id: str) -> list[Any]:
        payload = self._json(
            [
                "files",
                "list",
                "--filter",
                f"generated_output_task_id={task_id}",
                "--limit",
                "100",
            ],
            timeout=120,
        )
        files = payload if isinstance(payload, list) else payload.get("files", [])
        return [item for item in files if item.get("generated_output_task_id") == task_id]

    def download_output_files(self, task_payload: Any, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        files = task_payload.get("output_files") or task_payload.get("outputs") or []
        if not files:
            files = self.list_generated_files(str(task_payload["id"]))

        downloaded: list[Path] = []
        for item in files:
            file_id = str(item.get("id") or item.get("file_id"))
            metadata = item.get("metadata") or {}
            name = str(
                metadata.get("relative_path")
                or metadata.get("original_filename")
                or item.get("name")
                or file_id
            )
            target = output_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            self._download_file(file_id, target)
            downloaded.append(target)
        return downloaded

    def _download_file(self, file_id: str, target: Path) -> None:
        attempts = self.download_retries + 1
        last_error: RuntimeError | None = None
        for attempt in range(attempts):
            try:
                proc = subprocess.run(
                    ["introspection", "files", "content", file_id, "--out", str(target)],
                    cwd=self.repo_root,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                message = _format_error(error)
                last_error = RuntimeError(message)
            else:
                if proc.returncode == 0:
                    return
                excerpt = ((proc.stderr or "") + (proc.stdout or "")).strip()[:1000]
                message = f"`introspection files content {file_id}` failed: {excerpt}"
                last_error = RuntimeError(message)

            if attempt >= attempts - 1 or not is_probably_transient(str(last_error)):
                if self.incidents is not None:
                    self.incidents.record_platform_failure(str(last_error))
                raise last_error
            if self.incidents is not None:
                self.incidents.download_retries += 1
            time.sleep(self.retry_backoff_seconds * (2**attempt))

    def _task_is_terminal(self, task_id: str) -> bool:
        try:
            payload = self.get_task(task_id)
        except RuntimeError:
            return False
        status = str(payload.get("status", "")).lower()
        return status in {"completed", "complete", "failed", "canceled", "cancelled", "terminated"}


def _format_error(error: BaseException) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return f"`{' '.join(error.cmd)}` timed out after {error.timeout}s"
    return str(error)
