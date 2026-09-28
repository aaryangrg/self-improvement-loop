from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .self_improvement import load_runtime_metadata, validate_run_id

RESEARCHER_ASSETS = Path(__file__).resolve().parents[1] / "researcher"
ALLOWED_EDIT_ROOTS = {"recipe", "workspace"}


def prepare_research_workspace(repo_root: Path, run_id: str, epoch: int) -> Path:
    """Stage the candidate recipe and point Codex at original train evidence."""
    repo_root = repo_root.resolve()
    validate_run_id(run_id)
    if epoch < 1:
        raise ValueError("researcher epoch must be >= 1; epoch 0 establishes the baseline")

    results_dir = repo_root / "results" / "self-improvement" / run_id
    metadata = load_runtime_metadata(repo_root, run_id)
    working_recipe = repo_root / metadata.working_recipe
    baseline_recipe = results_dir / "references" / "baseline_recipe"
    source_workspace = results_dir / "workspace"
    epoch_results = results_dir / "epochs"
    artifact_dir = _researcher_artifact_dir(results_dir, epoch)
    workspace = artifact_dir / "sandbox"

    for label, path in (
        ("working recipe", working_recipe),
        ("baseline recipe", baseline_recipe),
        ("research notes", source_workspace),
    ):
        if not path.is_dir():
            raise FileNotFoundError(f"{label} not found: {path}")
    _assert_within(working_recipe, repo_root, "working recipe")
    _walk_files(baseline_recipe)
    _walk_files(source_workspace)
    if not RESEARCHER_ASSETS.is_dir():
        raise FileNotFoundError(f"researcher instruction assets not found: {RESEARCHER_ASSETS}")
    _assert_fresh_session_mode(results_dir)
    if workspace.exists():
        required = (
            workspace / "recipe",
            workspace / "instructions",
            workspace / ".agents" / "skills",
        )
        if workspace.is_dir() and all(path.is_dir() for path in required):
            preparation_path = artifact_dir / "sandbox.json"
            if not preparation_path.is_file():
                raise FileExistsError(
                    f"researcher sandbox has no preparation record: {artifact_dir}"
                )
            preparation = _read_json_object(preparation_path)
            if (
                preparation.get("format_version") != 3
                or preparation.get("run_id") != run_id
                or preparation.get("epoch") != epoch
            ):
                raise ValueError(
                    f"researcher sandbox metadata does not match {run_id} epoch {epoch}"
                )
            if preparation.get("recipe_snapshot") != snapshot_paths(working_recipe):
                raise ValueError(
                    "working recipe changed after the researcher workspace was prepared"
                )
            if preparation.get("notes_snapshot") != snapshot_paths(source_workspace):
                raise ValueError(
                    "research notes changed after the researcher workspace was prepared"
                )
            return workspace
        raise FileExistsError(f"researcher sandbox is incomplete: {workspace}")

    for previous_epoch in range(epoch):
        previous_dir = epoch_results / f"epoch-{previous_epoch:03d}"
        aggregate = previous_dir / "aggregate.json"
        if not aggregate.is_file():
            raise FileNotFoundError(
                f"train epoch {previous_epoch} has no aggregate.json: {aggregate}"
            )
        _read_json_object(aggregate)
        _walk_files(previous_dir)

    artifact_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir()
    (workspace / "instructions").mkdir()
    _copy_tree(working_recipe, workspace / "recipe")
    _copy_tree(RESEARCHER_ASSETS / "skills", workspace / ".agents" / "skills")
    shutil.copy2(RESEARCHER_ASSETS / "AGENTS.md", workspace / "AGENTS.md")
    shutil.copy2(
        RESEARCHER_ASSETS / "researcher-result.schema.json",
        workspace / "instructions" / "researcher-result.schema.json",
    )
    _write_json(
        artifact_dir / "sandbox.json",
        {
            "format_version": 3,
            "run_id": run_id,
            "epoch": epoch,
            "recipe_snapshot": snapshot_paths(working_recipe),
            "notes_snapshot": snapshot_paths(source_workspace),
            "prepared_at": datetime.now(UTC).isoformat(),
        },
    )
    return workspace


def build_permission_overrides(workspace: Path, results_dir: Path) -> list[str]:
    """Return Codex CLI config overrides for staged edits and original evidence."""
    workspace = workspace.resolve()
    results_dir = results_dir.resolve()
    rules = {
        ":root": "deny",
        ":minimal": "read",
        ":tmpdir": "deny",
        ":slash_tmp": "deny",
        str(workspace): "read",
        str(workspace / "recipe"): "write",
        str(workspace / "instructions"): "read",
        str(workspace / ".agents"): "read",
        str(results_dir / "epochs"): "read",
        str(results_dir / "references" / "baseline_recipe"): "read",
        str(results_dir / "workspace"): "write",
    }
    filesystem = ", ".join(
        f"{json.dumps(path)}={json.dumps(access)}" for path, access in rules.items()
    )
    return [
        'default_permissions="researcher"',
        f"permissions.researcher.filesystem={{{filesystem}}}",
        "permissions.researcher.network.enabled=false",
        'approval_policy="never"',
    ]


def snapshot_paths(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in _walk_files(root):
        relative = path.relative_to(root).as_posix()
        snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def changed_paths(root: Path, before: dict[str, str]) -> set[str]:
    after = snapshot_paths(root)
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}


def validate_research_result(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("researcher result must be a JSON object")
    expected = {"status", "summary", "hypothesis", "changes", "next_experiment"}
    if payload.keys() != expected:
        raise ValueError(f"researcher result keys must be exactly: {', '.join(sorted(expected))}")
    if payload["status"] not in {"ready_for_review", "no_change"}:
        raise ValueError("researcher result status must be ready_for_review or no_change")
    for key in ("summary", "hypothesis", "next_experiment"):
        if not isinstance(payload[key], str):
            raise ValueError(f"researcher result {key} must be a string")
    changes = payload["changes"]
    if not isinstance(changes, list):
        raise ValueError("researcher result changes must be a list")
    for change in changes:
        if not isinstance(change, dict) or change.keys() != {"path", "reason"}:
            raise ValueError("each change must contain exactly path and reason")
        path = change["path"]
        reason = change["reason"]
        if not isinstance(path, str) or not _is_allowed_relative_path(path):
            raise ValueError(f"researcher result contains disallowed path: {path!r}")
        if not isinstance(reason, str):
            raise ValueError("each change reason must be a string")
    return payload


def run_researcher(repo_root: Path, run_id: str, epoch: int) -> Path:
    repo_root = repo_root.resolve()
    validate_run_id(run_id)
    artifact_dir = _researcher_artifact_dir(
        repo_root / "results" / "self-improvement" / run_id, epoch
    )
    attempted_files = ("run.json", "researcher_result.json", "events.jsonl")
    if any((artifact_dir / name).exists() for name in attempted_files):
        raise FileExistsError(
            f"researcher epoch already attempted; inspect {artifact_dir} before retrying"
        )
    workspace = prepare_research_workspace(repo_root, run_id, epoch)
    artifact_dir = workspace.parent
    results_dir = repo_root / "results" / "self-improvement" / run_id
    model = _researcher_model(results_dir)
    notes_dir = results_dir / "workspace"
    result_path = artifact_dir / "researcher_result.json"
    events_path = artifact_dir / "events.jsonl"
    stderr_path = artifact_dir / "stderr.log"
    metadata_path = artifact_dir / "run.json"
    before = snapshot_paths(workspace)
    notes_before = snapshot_paths(notes_dir)
    previous_result = _read_json_object(
        results_dir / "epochs" / f"epoch-{epoch - 1:03d}" / "aggregate.json"
    )
    prompt = _research_prompt(run_id, epoch, previous_result, results_dir)
    command = _researcher_command(repo_root, workspace, results_dir, result_path, model, prompt)

    run_metadata: dict[str, Any] = {
        "run_id": run_id,
        "epoch": epoch,
        "model": model,
        "started_at": datetime.now(UTC).isoformat(),
        "command": command[:-1] + ["<prompt>"],
        "workspace": str(workspace),
        "status": "running",
    }
    with metadata_path.open("x", encoding="utf-8") as metadata_file:
        json.dump(run_metadata, metadata_file, indent=2, sort_keys=True)
        metadata_file.write("\n")
    isolated_home = artifact_dir / "codex-home"
    try:
        environment = _codex_environment(isolated_home)
        with (
            events_path.open("w", encoding="utf-8") as events,
            stderr_path.open("w", encoding="utf-8") as errors,
        ):
            completed = subprocess.run(
                command,
                cwd=workspace,
                env=environment,
                stdout=events,
                stderr=errors,
                check=False,
                text=True,
            )
        run_metadata["return_code"] = completed.returncode
        run_metadata["thread_id"] = _thread_id(events_path)
        if completed.returncode != 0:
            run_metadata["status"] = "codex_failed"
            run_metadata["finished_at"] = datetime.now(UTC).isoformat()
            _write_json(metadata_path, run_metadata)
            raise RuntimeError(
                f"Codex exited with status {completed.returncode}; "
                f"see {stderr_path} and {events_path}"
            )
        if run_metadata["thread_id"] is None:
            raise RuntimeError(f"Codex did not report a resumable thread ID in {events_path}")

        result = validate_research_result(_read_json_object(result_path))
        candidate_modified = changed_paths(workspace, before)
        disallowed = sorted(path for path in candidate_modified if not path.startswith("recipe/"))
        if disallowed:
            run_metadata["status"] = "disallowed_changes"
            run_metadata["disallowed_paths"] = disallowed
            raise RuntimeError(f"Codex changed read-only paths: {', '.join(disallowed)}")
        notes_modified = {f"workspace/{path}" for path in changed_paths(notes_dir, notes_before)}
        modified = candidate_modified | notes_modified
        reported = [change["path"] for change in result["changes"]]
        if len(reported) != len(set(reported)) or set(reported) != modified:
            raise ValueError(
                "researcher result changes do not match observed files: "
                f"reported={sorted(reported)}, observed={sorted(modified)}"
            )

        run_metadata.update(
            {
                "status": "pending_verification" if candidate_modified else "complete",
                "result": result,
                "changed_paths": sorted(modified),
                "candidate_snapshot": snapshot_paths(workspace / "recipe"),
                "notes_snapshot": snapshot_paths(notes_dir),
                "candidate_promoted": False,
            }
        )
    except Exception as error:
        if run_metadata.get("status") == "running":
            run_metadata["status"] = "failed"
        run_metadata["error"] = str(error)
        raise
    finally:
        run_metadata["finished_at"] = datetime.now(UTC).isoformat()
        _write_json(metadata_path, run_metadata)
    return artifact_dir


def revise_researcher(repo_root: Path, run_id: str, epoch: int, feedback_path: Path) -> Path:
    repo_root = repo_root.resolve()
    validate_run_id(run_id)
    results_dir = repo_root / "results" / "self-improvement" / run_id
    artifact_dir = _researcher_artifact_dir(results_dir, epoch)
    metadata_path = artifact_dir / "run.json"
    metadata = _read_json_object(metadata_path)
    feedback = _read_json_object(feedback_path)
    if metadata.get("status") != "pending_verification" or feedback.get("verdict") != "reject":
        raise ValueError("a pending candidate and rejected verifier verdict are required")
    thread_id = metadata.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("researcher session cannot be resumed without a thread ID")
    workspace = artifact_dir / "sandbox"
    notes_dir = results_dir / "workspace"
    recipe_before = snapshot_paths(workspace / "recipe")
    notes_before = snapshot_paths(notes_dir)
    if recipe_before != metadata.get("candidate_snapshot"):
        raise ValueError("candidate recipe changed after researcher completed")
    if notes_before != metadata.get("notes_snapshot"):
        raise ValueError("research notes changed after researcher completed")

    revisions_dir = artifact_dir / "revisions"
    revision_number = (
        len(list(revisions_dir.glob("revision-*"))) + 1 if revisions_dir.exists() else 1
    )
    revision_dir = revisions_dir / f"revision-{revision_number:03d}"
    revision_dir.mkdir(parents=True, exist_ok=False)
    result_path = revision_dir / "researcher_result.json"
    events_path = revision_dir / "events.jsonl"
    prompt = (
        "The independent verifier rejected your candidate. Revise the staged recipe and "
        "research notes as needed, using your prior research context. "
        f"Verifier feedback: {json.dumps(feedback, sort_keys=True)}. "
        "Return the required structured result with every file changed in this revision."
    )
    command = _researcher_command(
        repo_root,
        workspace,
        results_dir,
        result_path,
        _researcher_model(results_dir),
        prompt,
        thread_id=thread_id,
    )
    metadata["status"] = "revising"
    _write_json(metadata_path, metadata)
    environment = _codex_environment(artifact_dir / "codex-home")
    with (
        events_path.open("w", encoding="utf-8") as events,
        (revision_dir / "stderr.log").open("w", encoding="utf-8") as errors,
    ):
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=environment,
            stdout=events,
            stderr=errors,
            check=False,
            text=True,
        )
    if completed.returncode != 0:
        metadata["status"] = "revision_failed"
        _write_json(metadata_path, metadata)
        raise RuntimeError(f"Codex revision failed; see {revision_dir / 'stderr.log'}")
    result = validate_research_result(_read_json_object(result_path))
    recipe_after = snapshot_paths(workspace / "recipe")
    notes_after = snapshot_paths(notes_dir)
    changed = {
        f"recipe/{path}"
        for path in recipe_before.keys() | recipe_after.keys()
        if recipe_before.get(path) != recipe_after.get(path)
    } | {
        f"workspace/{path}"
        for path in notes_before.keys() | notes_after.keys()
        if notes_before.get(path) != notes_after.get(path)
    }
    reported = [change["path"] for change in result["changes"]]
    if len(reported) != len(set(reported)) or set(reported) != changed:
        metadata["status"] = "revision_failed"
        _write_json(metadata_path, metadata)
        raise ValueError("researcher revision changes do not match observed files")
    metadata.update(
        {
            "status": (
                "pending_verification"
                if recipe_after
                != snapshot_paths(
                    repo_root / load_runtime_metadata(repo_root, run_id).working_recipe
                )
                else "complete"
            ),
            "result": result,
            "candidate_snapshot": recipe_after,
            "notes_snapshot": notes_after,
            "revision_count": revision_number,
            "last_revision": str(revision_dir),
        }
    )
    _write_json(metadata_path, metadata)
    return revision_dir


def _researcher_command(
    repo_root: Path,
    workspace: Path,
    results_dir: Path,
    result_path: Path,
    model: str,
    prompt: str,
    thread_id: str | None = None,
) -> list[str]:
    command = [str(repo_root / "node_modules" / ".bin" / "codex"), "exec"]
    if thread_id is not None:
        command.extend(["resume", thread_id])
    command.extend(
        [
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
            "--skip-git-repo-check",
            "--json",
            "--output-schema",
            str(workspace / "instructions" / "researcher-result.schema.json"),
            "--output-last-message",
            str(result_path),
            "--model",
            model,
        ]
    )
    if thread_id is None:
        command.extend(["-C", str(workspace)])
    for override in build_permission_overrides(workspace, results_dir):
        command.extend(["--config", override])
    command.extend(
        [
            "--config",
            'shell_environment_policy.include_only=["PATH","HOME","TMPDIR","LANG","LC_ALL","TERM"]',
        ]
    )
    command.append(prompt)
    return command


def _researcher_artifact_dir(results_dir: Path, epoch: int) -> Path:
    return results_dir / "metadata" / "researcher" / f"epoch-{epoch:03d}"


def _assert_within(path: Path, root: Path, label: str) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"{label} escapes the repository root: {path}")


def _assert_fresh_session_mode(results_dir: Path) -> None:
    metadata_path = results_dir / "metadata" / "run.json"
    if not metadata_path.is_file():
        return
    payload = _read_json_object(metadata_path)
    config = payload.get("config")
    researcher = config.get("researcher") if isinstance(config, dict) else None
    mode = researcher.get("epoch_session_mode") if isinstance(researcher, dict) else None
    if mode == "resume":
        raise NotImplementedError(
            "researcher.epoch_session_mode=resume is not implemented yet; use fresh for V1"
        )


def _researcher_model(results_dir: Path) -> str:
    payload = _read_json_object(results_dir / "metadata" / "run.json")
    config = payload.get("config")
    researcher = config.get("researcher") if isinstance(config, dict) else None
    model = researcher.get("model") if isinstance(researcher, dict) else None
    if not isinstance(model, str) or not model.strip():
        raise ValueError("run metadata is missing required researcher.model")
    return model.strip()


def _copy_tree(source: Path, destination: Path) -> None:
    _walk_files(source)
    shutil.copytree(source, destination, symlinks=False)


def _walk_files(root: Path) -> list[Path]:
    if root.is_symlink():
        raise ValueError(f"symlink is not allowed in researcher inputs: {root}")
    files: list[Path] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        for dirname in list(dirnames):
            path = current / dirname
            if path.is_symlink():
                raise ValueError(f"symlink is not allowed in researcher inputs: {path}")
        for filename in filenames:
            path = current / filename
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"non-regular file is not allowed in researcher inputs: {path}")
            files.append(path)
    return files


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _is_allowed_relative_path(path: str) -> bool:
    candidate = Path(path)
    return (
        not candidate.is_absolute()
        and ".." not in candidate.parts
        and len(candidate.parts) > 1
        and candidate.parts[0] in ALLOWED_EDIT_ROOTS
    )


def _codex_environment(isolated_home: Path) -> dict[str, str]:
    allowed = {"PATH", "TMPDIR", "LANG", "LC_ALL", "TERM"}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    isolated_home.mkdir(parents=True, exist_ok=True)
    environment["HOME"] = str(isolated_home)
    environment["CODEX_HOME"] = str(
        Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    )
    return environment


def _thread_id(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            if isinstance(event.get("thread_id"), str):
                return str(event["thread_id"])
            payload = event.get("thread")
            if isinstance(payload, dict) and isinstance(payload.get("id"), str):
                return str(payload["id"])
    return None


def _research_prompt(run_id: str, epoch: int, aggregate: dict[str, Any], results_dir: Path) -> str:
    scores = {
        key: aggregate.get(key)
        for key in ("task_pass_rate", "rubric_pass_rate", "number_of_evaluated_tasks")
    }
    return (
        f"Self-improvement run `{run_id}`, epoch {epoch}.\n\n"
        f"Train epoch {epoch - 1} just completed. Its aggregate was: "
        f"`{json.dumps(scores, sort_keys=True)}`.\n\n"
        "Review completed train traces and evaluation scores under "
        f"`{results_dir / 'epochs'}` and research notes under "
        f"`{results_dir / 'workspace'}`. The read-only baseline is at "
        f"`{results_dir / 'references' / 'baseline_recipe'}`. "
        "Form a generalizable hypothesis, then edit the staged `recipe/` or the research notes. "
        "Do not optimize for an individual task or inspect held-out data. "
        "Do not change files outside staged recipe/ and the research notes directory. "
        "Return only the structured result required by the output schema, and document the reason "
        "for each changed file. Do not commit, push, or modify evaluation or runner code."
    )
