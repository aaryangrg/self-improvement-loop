from __future__ import annotations

import json
from pathlib import Path

from .config import RunnerConfig
from .evaluate import evaluate_outputs
from .harvey import load_task
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

    client = IntrospectionClient(config.repo_root)
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
    client.stream_until_finished(handle.task_id, handle.run_id)

    conversation = client.get_conversation(handle.task_id)
    _write_json(trial_dir / "conversation.json", conversation)

    task_payload = client.get_task(handle.task_id)
    client.download_output_files(task_payload, outputs_dir)

    evaluation = evaluate_outputs(task, outputs_dir)
    _write_json(trial_dir / "evaluation.json", evaluation)
    return trial_dir
