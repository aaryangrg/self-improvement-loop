from __future__ import annotations

import json
from pathlib import Path

from .config import RunnerConfig
from .evaluate import evaluate_outputs
from .harvey import load_task
from .incidents import RunIncidents
from .introspection import IntrospectionClient, UploadedFile
from .paths import task_result_dir
from .prompt import build_prompt
from .upload_cache import UploadCache


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_one(task_id: str, trial: int, config: RunnerConfig) -> Path:
    task = load_task(config, task_id)
    trial_dir = task_result_dir(config.results_root, task.task_id, trial)
    outputs_dir = trial_dir / "outputs"
    trial_dir.mkdir(parents=True, exist_ok=True)

    incidents = RunIncidents()
    client = IntrospectionClient(
        config.repo_root,
        incidents=incidents,
        cli_retries=config.cli_retries,
        download_retries=config.download_retries,
        stream_reattaches=config.stream_reattaches,
        retry_backoff_seconds=config.retry_backoff_seconds,
    )
    cache = UploadCache(config.repo_root / config.upload_cache_path)
    uploads: list[UploadedFile] = []
    for document in task.documents:
        size = document.local_path.stat().st_size
        cached = cache.get(document.cache_key, size)
        if cached is not None:
            try:
                client.get_file(cached.file_id)
                uploads.append(
                    UploadedFile(
                        id=cached.file_id,
                        name=cached.file_name,
                        mount_path=document.mount_path,
                    )
                )
                continue
            except RuntimeError:
                cache.remove(document.cache_key)

        uploaded = client.upload_file(document.local_path, document.mount_path)
        cache.put(document.cache_key, uploaded.id, uploaded.name, size)
        uploads.append(uploaded)

    handle = client.create_task(
        runtime_name=config.runtime_name,
        runtime_id=config.runtime_id,
        environment=config.environment,
        agent=config.agent,
        prompt=build_prompt(task),
        files=uploads,
    )
    _write_json(trial_dir / "introspection-task-handle.json", handle.__dict__)

    try:
        client.stream_until_finished(handle.task_id, handle.run_id)
    except RuntimeError as error:
        incidents.record_platform_failure(str(error))
        _write_json(
            trial_dir / "execution_error.json",
            {"status": "error", "phase": "execution", "message": str(error)},
        )
        _salvage_after_task_start(client, handle.task_id, outputs_dir, trial_dir)
        _write_json(trial_dir / "incidents.json", incidents.to_dict())
        raise

    _salvage_after_task_start(client, handle.task_id, outputs_dir, trial_dir)
    _write_json(trial_dir / "incidents.json", incidents.to_dict())

    evaluation = evaluate_outputs(task, outputs_dir, trial_dir, config)
    _write_json(trial_dir / "evaluation.json", evaluation)
    return trial_dir


def _salvage_after_task_start(
    client: IntrospectionClient,
    task_id: str,
    outputs_dir: Path,
    trial_dir: Path,
) -> None:
    try:
        conversation = client.get_conversation(task_id)
        _write_json(trial_dir / "conversation.json", conversation)
    except RuntimeError as error:
        _write_json(
            trial_dir / "conversation_error.json",
            {"status": "error", "phase": "conversation", "message": str(error)},
        )

    try:
        task_payload = client.get_task(task_id)
        _write_json(trial_dir / "introspection-task.json", task_payload)
    except RuntimeError as error:
        _write_json(
            trial_dir / "task_fetch_error.json",
            {"status": "error", "phase": "task_fetch", "message": str(error)},
        )
        return

    try:
        client.download_output_files(task_payload, outputs_dir)
    except RuntimeError as error:
        _write_json(
            trial_dir / "artifact_download_error.json",
            {"status": "error", "phase": "artifact_download", "message": str(error)},
        )
