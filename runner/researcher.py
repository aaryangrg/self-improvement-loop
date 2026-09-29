from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .recipe_candidate import stage_agent_configs
from .self_improvement import load_runtime_metadata, validate_run_id

RESEARCHER_ASSETS = Path(__file__).resolve().parents[1] / "researcher"


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
                preparation.get("format_version") != 4
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
    stage_agent_configs(workspace / "recipe")
    _copy_tree(RESEARCHER_ASSETS / "skills", workspace / ".agents" / "skills")
    shutil.copy2(RESEARCHER_ASSETS / "AGENTS.md", workspace / "AGENTS.md")
    shutil.copy2(
        RESEARCHER_ASSETS / "researcher-result.schema.json",
        workspace / "instructions" / "researcher-result.schema.json",
    )
    _write_json(
        artifact_dir / "sandbox.json",
        {
            "format_version": 4,
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
    expected = {"hypothesis", "changes_made", "expected_outcome"}
    if payload.keys() != expected:
        raise ValueError(f"researcher result keys must be exactly: {', '.join(sorted(expected))}")
    for key in ("hypothesis", "expected_outcome"):
        if not isinstance(payload[key], str):
            raise ValueError(f"researcher result {key} must be a string")
    changes = payload["changes_made"]
    if not isinstance(changes, list) or not all(isinstance(change, str) for change in changes):
        raise ValueError("researcher result changes_made must be a list of strings")
    return payload


def recover_researcher(repo_root: Path, run_id: str, epoch: int) -> Path:
    """Reuse a completed Codex turn whose saved result failed post-run validation."""
    repo_root = repo_root.resolve()
    validate_run_id(run_id)
    results_dir = repo_root / "results" / "self-improvement" / run_id
    artifact_dir = _researcher_artifact_dir(results_dir, epoch)
    metadata_path = artifact_dir / "run.json"
    metadata = _read_json_object(metadata_path)
    if metadata.get("status") in {"pending_verification", "needs_recipe_edit"}:
        return artifact_dir
    if metadata.get("status") != "failed" or metadata.get("return_code") != 0:
        raise RuntimeError(f"researcher attempt cannot be safely recovered: {metadata_path}")
    if not metadata.get("thread_id") or (artifact_dir / "gates").exists():
        raise RuntimeError(f"researcher recovery has ambiguous side effects: {artifact_dir}")

    preparation = _read_json_object(artifact_dir / "sandbox.json")
    workspace = artifact_dir / "sandbox"
    recipe = workspace / "recipe"
    notes_dir = results_dir / "workspace"
    working_recipe = repo_root / load_runtime_metadata(repo_root, run_id).working_recipe
    if snapshot_paths(working_recipe) != preparation.get("recipe_snapshot"):
        raise RuntimeError("working recipe changed since the researcher sandbox was prepared")
    with tempfile.TemporaryDirectory(prefix="researcher-recovery-") as temp:
        staged_recipe = Path(temp) / "recipe"
        _copy_tree(working_recipe, staged_recipe)
        stage_agent_configs(staged_recipe)
        recipe_changes = {
            f"recipe/{path}" for path in changed_paths(recipe, snapshot_paths(staged_recipe))
        }
    notes_before = preparation.get("notes_snapshot")
    if not isinstance(notes_before, dict):
        raise ValueError("researcher sandbox has no original notes snapshot")
    note_changes = {f"workspace/{path}" for path in changed_paths(notes_dir, notes_before)}
    saved = _read_json_object(artifact_dir / "researcher_result.json")
    result = _recover_result_shape(saved)
    metadata.update(
        {
            "status": "pending_verification" if recipe_changes else "needs_recipe_edit",
            "result": result,
            "changed_paths": sorted(recipe_changes | note_changes),
            "candidate_snapshot": snapshot_paths(recipe),
            "notes_snapshot": snapshot_paths(notes_dir),
            "candidate_promoted": False,
            "recovered_at": datetime.now(UTC).isoformat(),
        }
    )
    _write_json(metadata_path, metadata)
    return artifact_dir


def _recover_result_shape(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) == {"hypothesis", "changes_made", "expected_outcome"}:
        return validate_research_result(payload)
    if set(payload) != {"status", "summary", "hypothesis", "changes", "next_experiment"}:
        raise ValueError("saved researcher output has an unknown schema")
    changes = payload["changes"]
    if payload["status"] != "ready_for_review" or not isinstance(changes, list):
        raise ValueError("saved researcher output is not a completed candidate")
    reasons = [
        change["reason"]
        for change in changes
        if isinstance(change, dict)
        and isinstance(change.get("path"), str)
        and change["path"].startswith("recipe/")
        and isinstance(change.get("reason"), str)
    ]
    result = {
        "hypothesis": payload["hypothesis"],
        "changes_made": reasons or [payload["summary"]],
        "expected_outcome": payload["next_experiment"],
    }
    return validate_research_result(result)


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
    model, reasoning_effort = _researcher_settings(results_dir)
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
    command = _researcher_command(
        repo_root, workspace, results_dir, result_path, model, reasoning_effort, prompt
    )

    run_metadata: dict[str, Any] = {
        "run_id": run_id,
        "epoch": epoch,
        "model": model,
        "reasoning_effort": reasoning_effort,
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

        run_metadata.update(
            {
                "status": "pending_verification" if candidate_modified else "needs_recipe_edit",
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
    if metadata.get("status") not in {
        "pending_verification",
        "promoted",
        "needs_recipe_edit",
    }:
        raise ValueError("a pending, promoted, or no-edit candidate is required for revision")
    if feedback.get("verdict") != "reject" and feedback.get("phase") not in {
        "check",
        "build",
        "smoke",
        "no_recipe_edit",
    }:
        raise ValueError("a verifier rejection or candidate diagnostic is required")
    thread_id = metadata.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("researcher session cannot be resumed without a thread ID")
    workspace = artifact_dir / "sandbox"
    notes_dir = results_dir / "workspace"
    recipe_before = snapshot_paths(workspace / "recipe")
    workspace_before = snapshot_paths(workspace)
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
        "Your candidate did not pass a pre-train gate. Revise the staged recipe and "
        "research notes as needed, using your prior research context. "
        f"Diagnostic feedback: {json.dumps(feedback, sort_keys=True)}. "
        "Return the required structured result with every file changed in this revision."
    )
    command = _researcher_command(
        repo_root,
        workspace,
        results_dir,
        result_path,
        *_researcher_settings(results_dir),
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
    disallowed = sorted(
        path
        for path in changed_paths(workspace, workspace_before)
        if not path.startswith("recipe/")
    )
    if disallowed:
        metadata["status"] = "revision_failed"
        metadata["disallowed_paths"] = disallowed
        _write_json(metadata_path, metadata)
        raise RuntimeError(f"Codex changed read-only paths: {', '.join(disallowed)}")
    recipe_after = snapshot_paths(workspace / "recipe")
    notes_after = snapshot_paths(notes_dir)
    working_recipe = repo_root / load_runtime_metadata(repo_root, run_id).working_recipe
    with tempfile.TemporaryDirectory(prefix="researcher-effective-") as temp:
        staged_working = Path(temp) / "recipe"
        shutil.copytree(working_recipe, staged_working)
        stage_agent_configs(staged_working)
        effective_changed = recipe_after != snapshot_paths(staged_working)
    metadata.update(
        {
            "status": (
                "pending_verification"
                if recipe_before != recipe_after and effective_changed
                else "needs_recipe_edit"
            ),
            "result": result,
            "candidate_snapshot": recipe_after,
            "candidate_promoted": False,
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
    reasoning_effort: str,
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
            "--config",
            f'model_reasoning_effort="{reasoning_effort}"',
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


def _researcher_settings(results_dir: Path) -> tuple[str, str]:
    payload = _read_json_object(results_dir / "metadata" / "run.json")
    config = payload.get("config")
    researcher = config.get("researcher") if isinstance(config, dict) else None
    model = researcher.get("model") if isinstance(researcher, dict) else None
    effort = researcher.get("reasoning_effort") if isinstance(researcher, dict) else None
    if not isinstance(model, str) or not model.strip():
        raise ValueError("run metadata is missing required researcher.model")
    if effort not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("run metadata is missing valid researcher.reasoning_effort")
    return model.strip(), str(effort)


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


def _dedicated_codex_home() -> Path:
    codex_home = Path.home() / ".self-improvement-loop" / "codex"
    codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    return codex_home.resolve()


def ensure_codex_login(repo_root: Path) -> None:
    codex_home = _dedicated_codex_home()
    cli = repo_root / "node_modules" / ".bin" / "codex"
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("CODEX_API_KEY", None)
    status_command = [str(cli), "login", "status"]
    status = subprocess.run(
        status_command,
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode == 0:
        return
    details = f"{status.stdout}\n{status.stderr}".strip()
    if "not logged in" not in details.lower():
        raise RuntimeError(f"Cannot check Codex login in {codex_home}: {details}")
    login_command = f'CODEX_HOME="{codex_home}" {cli} login'
    if not sys.stdin.isatty():
        raise RuntimeError(
            f"Codex login is required before self-improve start. Run: {login_command}"
        )
    print(f"Codex login is required for {codex_home}. Starting sign-in...", flush=True)
    login = subprocess.run([str(cli), "login"], cwd=repo_root, env=environment, check=False)
    if login.returncode != 0:
        raise RuntimeError(f"Codex login did not complete. Run: {login_command}")
    status = subprocess.run(
        status_command,
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0:
        raise RuntimeError(f"Codex login could not be confirmed. Run: {login_command}")


def _codex_environment(isolated_home: Path) -> dict[str, str]:
    allowed = {"PATH", "TMPDIR", "LANG", "LC_ALL", "TERM"}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    isolated_home.mkdir(parents=True, exist_ok=True)
    environment["HOME"] = str(isolated_home)
    environment["CODEX_HOME"] = str(_dedicated_codex_home())
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
        f"Completed training epochs: `{results_dir / 'epochs'}`. "
        f"Research notes: `{results_dir / 'workspace'}`. "
        f"Read-only starting recipe: `{results_dir / 'references' / 'baseline_recipe'}`.\n\n"
        "Follow `AGENTS.md` and its two research skills. Review this epoch against prior "
        "hypotheses, edit `recipe/`, record the experiment in the research notes, and return "
        "the structured result required by the output schema."
    )
