from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .recipe_candidate import materialize_agent_configs
from .researcher import _codex_environment, snapshot_paths
from .self_improvement import load_runtime_metadata, validate_run_id

VERIFIER_ASSETS = Path(__file__).resolve().parents[1] / "verifier"
VERIFIER_SCHEMA = VERIFIER_ASSETS / "verifier-result.schema.json"
VERIFIER_INSTRUCTIONS = VERIFIER_ASSETS / "AGENTS.md"
IGNORED_RECIPE_DIRS = {"node_modules", ".venv", "__pycache__"}


def validate_verdict(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.keys() != {
        "verdict",
        "summary",
        "issues",
        "revision_instructions",
    }:
        raise ValueError("verifier result has an invalid shape")
    if payload["verdict"] not in {"approve", "reject"}:
        raise ValueError("verifier verdict must be approve or reject")
    if not isinstance(payload["summary"], str) or not isinstance(
        payload["revision_instructions"], str
    ):
        raise ValueError("verifier summary and revision instructions must be strings")
    issues = payload["issues"]
    if not isinstance(issues, list) or not all(isinstance(item, str) for item in issues):
        raise ValueError("verifier issues must be a list of strings")
    if payload["verdict"] == "reject" and not issues:
        raise ValueError("rejected candidates need at least one issue")
    return payload


def build_verifier_permissions(
    verifier_dir: Path, candidate: Path, working_recipe: Path, results_dir: Path
) -> list[str]:
    rules = {
        ":root": "deny",
        ":minimal": "read",
        ":tmpdir": "deny",
        ":slash_tmp": "deny",
        str(verifier_dir.resolve()): "read",
        str(candidate.resolve()): "read",
        str(working_recipe.resolve()): "read",
        str((results_dir / "epochs").resolve()): "read",
        str((results_dir / "references" / "baseline_recipe").resolve()): "read",
        str((results_dir / "workspace").resolve()): "read",
    }
    filesystem = ", ".join(
        f"{json.dumps(path)}={json.dumps(access)}" for path, access in rules.items()
    )
    return [
        'default_permissions="verifier"',
        f"permissions.verifier.filesystem={{{filesystem}}}",
        "permissions.verifier.network.enabled=false",
        'approval_policy="never"',
    ]


def run_verifier(repo_root: Path, run_id: str, epoch: int) -> Path:
    repo_root = repo_root.resolve()
    validate_run_id(run_id)
    results_dir = repo_root / "results" / "self-improvement" / run_id
    artifact_dir = results_dir / "metadata" / "researcher" / f"epoch-{epoch:03d}"
    researcher_path = artifact_dir / "run.json"
    researcher = json.loads(researcher_path.read_text(encoding="utf-8"))
    if researcher.get("status") not in {"pending_verification", "promoted"}:
        raise ValueError("researcher has no candidate pending verification")
    candidate = artifact_dir / "sandbox" / "recipe"
    snapshot = snapshot_paths(candidate)
    if snapshot != researcher.get("candidate_snapshot"):
        raise ValueError("candidate recipe changed after researcher completed")
    working_recipe = repo_root / load_runtime_metadata(repo_root, run_id).working_recipe
    run_config = json.loads((results_dir / "metadata" / "run.json").read_text(encoding="utf-8"))
    model = run_config["config"]["verifier"]["model"]
    reasoning_effort = run_config["config"]["verifier"].get("reasoning_effort")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("run metadata is missing required verifier.model")
    if reasoning_effort not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("run metadata is missing valid verifier.reasoning_effort")
    attempts_root = artifact_dir / "verifier"
    attempt = len(list(attempts_root.glob("attempt-*"))) + 1 if attempts_root.exists() else 1
    attempt_dir = attempts_root / f"attempt-{attempt:03d}"
    attempt_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(VERIFIER_INSTRUCTIONS, attempt_dir / "AGENTS.md")
    prior_recipe = attempts_root / "original_recipe"
    if not prior_recipe.is_dir():
        prior_recipe = working_recipe
    _write_candidate_diff(prior_recipe, candidate, attempt_dir / "candidate.diff")
    result_path = attempt_dir / "verifier_result.json"
    events_path = attempt_dir / "events.jsonl"
    prompt = (
        f"Review run {run_id}, epoch {epoch} using AGENTS.md and candidate.diff. "
        f"Candidate recipe: {candidate}. "
        f"Training epochs: {results_dir / 'epochs'}. "
        f"Research notes: {results_dir / 'workspace'}. "
        f"Starting recipe: {results_dir / 'references' / 'baseline_recipe'}. "
        f"Researcher report: {json.dumps(researcher['result'], sort_keys=True)}. "
        "Return the JSON verdict required by the output schema."
    )
    command = [
        str(repo_root / "node_modules" / ".bin" / "codex"),
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--skip-git-repo-check",
        "--ephemeral",
        "--json",
        "--output-schema",
        str(VERIFIER_SCHEMA),
        "--output-last-message",
        str(result_path),
        "-C",
        str(attempt_dir),
        "--model",
        model,
        "--config",
        f'model_reasoning_effort="{reasoning_effort}"',
    ]
    for override in build_verifier_permissions(attempt_dir, candidate, working_recipe, results_dir):
        command.extend(["--config", override])
    command.extend(
        [
            "--config",
            'shell_environment_policy.include_only=["PATH","HOME","TMPDIR","LANG","LC_ALL","TERM"]',
            prompt,
        ]
    )
    environment = _codex_environment(artifact_dir / "codex-home")
    with (
        events_path.open("w", encoding="utf-8") as events,
        (attempt_dir / "stderr.log").open("w", encoding="utf-8") as errors,
    ):
        completed = subprocess.run(
            command,
            cwd=attempt_dir,
            env=environment,
            stdout=events,
            stderr=errors,
            check=False,
            text=True,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"Codex verifier failed; see {attempt_dir / 'stderr.log'}")
    verdict = validate_verdict(json.loads(result_path.read_text(encoding="utf-8")))
    if snapshot_paths(candidate) != snapshot:
        raise RuntimeError("candidate changed during verifier review")
    (attempt_dir / "review.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "epoch": epoch,
                "model": model,
                "reasoning_effort": reasoning_effort,
                "candidate_snapshot": snapshot,
                "verdict": verdict["verdict"],
                "finished_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return result_path


def _write_candidate_diff(prior_recipe: Path, candidate: Path, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="verifier-candidate-") as temp:
        effective = Path(temp) / "recipe"
        shutil.copytree(candidate, effective, ignore=shutil.ignore_patterns(*IGNORED_RECIPE_DIRS))
        materialize_agent_configs(effective, prior_recipe)
        previous = _recipe_files(prior_recipe)
        proposed = _recipe_files(effective)
        sections: list[str] = []
        for relative in sorted(previous.keys() | proposed.keys()):
            old = previous[relative].read_bytes() if relative in previous else b""
            new = proposed[relative].read_bytes() if relative in proposed else b""
            if relative in previous and relative in proposed and old == new:
                continue
            try:
                old_text = old.decode("utf-8")
                new_text = new.decode("utf-8")
            except UnicodeDecodeError:
                sections.append(f"Binary file changed: {relative}\n")
                continue
            sections.extend(
                difflib.unified_diff(
                    old_text.splitlines(keepends=True),
                    new_text.splitlines(keepends=True),
                    fromfile=f"before/{relative}" if relative in previous else "/dev/null",
                    tofile=f"candidate/{relative}" if relative in proposed else "/dev/null",
                )
            )
        destination.write_text("".join(sections), encoding="utf-8")


def _recipe_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in IGNORED_RECIPE_DIRS]
        for filename in filenames:
            path = Path(directory) / filename
            files[path.relative_to(root).as_posix()] = path
    return files


def promote_candidate(
    repo_root: Path, run_id: str, epoch: int, require_verifier: bool = True
) -> Path:
    repo_root = repo_root.resolve()
    validate_run_id(run_id)
    results_dir = repo_root / "results" / "self-improvement" / run_id
    artifact_dir = results_dir / "metadata" / "researcher" / f"epoch-{epoch:03d}"
    researcher_path = artifact_dir / "run.json"
    researcher = json.loads(researcher_path.read_text(encoding="utf-8"))
    if researcher.get("status") != "pending_verification" or researcher.get("candidate_promoted"):
        raise ValueError("researcher has no unpromoted candidate")
    candidate = artifact_dir / "sandbox" / "recipe"
    current = snapshot_paths(candidate)
    if current != researcher.get("candidate_snapshot"):
        raise ValueError("candidate recipe changed after researcher completed")
    working_recipe = repo_root / load_runtime_metadata(repo_root, run_id).working_recipe
    preparation = json.loads((artifact_dir / "sandbox.json").read_text(encoding="utf-8"))
    previous = researcher.get("promoted_snapshot") or preparation["recipe_snapshot"]
    if snapshot_paths(working_recipe) != previous:
        raise ValueError("working recipe changed after candidate was staged")
    if require_verifier:
        reviews = sorted((artifact_dir / "verifier").glob("attempt-*/review.json"))
        if not reviews:
            raise ValueError("candidate has no verifier review")
        review = json.loads(reviews[-1].read_text(encoding="utf-8"))
        if review["verdict"] != "approve" or review["candidate_snapshot"] != current:
            raise ValueError("latest verifier did not approve this candidate")
    original_recipe = artifact_dir / "verifier" / "original_recipe"
    if not original_recipe.exists():
        original_recipe.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            working_recipe,
            original_recipe,
            ignore=shutil.ignore_patterns(*IGNORED_RECIPE_DIRS),
        )
    with tempfile.TemporaryDirectory(prefix="researcher-promote-") as temp:
        effective = Path(temp) / "recipe"
        shutil.copytree(candidate, effective)
        materialize_agent_configs(effective, working_recipe)
        effective_snapshot = snapshot_paths(effective)
        for relative in previous.keys() - effective_snapshot.keys():
            (working_recipe / relative).unlink()
        for relative, digest in effective_snapshot.items():
            if previous.get(relative) == digest:
                continue
            destination = working_recipe / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(effective / relative, destination)
    researcher["candidate_promoted"] = True
    researcher["promoted_snapshot"] = effective_snapshot
    researcher["status"] = "promoted"
    researcher["promoted_at"] = datetime.now(UTC).isoformat()
    researcher_path.write_text(
        json.dumps(researcher, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return working_recipe
