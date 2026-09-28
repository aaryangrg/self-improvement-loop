from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import RunnerConfig
from .git_ops import GitOps
from .introspection import IntrospectionClient
from .researcher import revise_researcher, run_researcher
from .self_improvement import (
    SelfImprovementConfig,
    bootstrap_run,
    candidate_commit_paths,
    generate_run_id,
    load_runtime_metadata,
    load_self_improvement_config,
    record_bootstrap_commit,
    record_candidate,
    record_pr,
    record_pr_branch,
    record_runtime,
    refresh_metrics,
    run_epoch_split,
    runner_config_with_runtime_metadata,
    update_best_candidate,
    validate_run_id,
)
from .verifier import promote_candidate, run_verifier


def wait_for_ready_version(
    client: IntrospectionClient,
    runtime_id: str,
    commit: str,
    pr_ref: str,
    timeout_seconds: float = 900,
    poll_seconds: float = 15,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    while True:
        for version in client.list_runtime_versions(runtime_id):
            if version.get("recipe_ref") != pr_ref:
                continue
            recipe_id = version.get("recipe_id")
            if not isinstance(recipe_id, str):
                continue
            recipe = client.get_recipe(recipe_id)
            if recipe.get("git_commit_sha") != commit:
                continue
            status = version.get("image_build_status")
            if status == "ready" and recipe.get("validation", {}).get("status") == "valid":
                version_id = version.get("id")
                if not isinstance(version_id, str):
                    raise ValueError("ready runtime version has no id")
                return version_id
            if status in {"failed", "error"} or recipe.get("validation", {}).get("status") in {
                "invalid",
                "failed",
            }:
                raise RuntimeError(
                    f"runtime build for {commit} failed: {version.get('image_build_error_message')}"
                )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"no ready runtime version for commit {commit} within {timeout_seconds}s"
            )
        time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))


def review_and_promote(
    repo_root: Path, run_id: str, epoch: int, config: SelfImprovementConfig
) -> bool:
    artifact_dir = run_researcher(repo_root, run_id, epoch)
    metadata_path = artifact_dir / "run.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata["status"] == "complete":
        return False
    if metadata["status"] != "pending_verification":
        raise RuntimeError(f"researcher stopped with status {metadata['status']}")
    if config.verifier.enabled:
        for revision in range(config.verifier.max_revision_attempts + 1):
            feedback_path = run_verifier(repo_root, run_id, epoch)
            feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
            if feedback["verdict"] == "approve":
                break
            if revision == config.verifier.max_revision_attempts:
                raise RuntimeError(
                    f"verifier rejected epoch {epoch} after {revision + 1} attempts: "
                    f"{feedback_path}"
                )
            revise_researcher(repo_root, run_id, epoch, feedback_path)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata["status"] == "complete":
                return False
    promote_candidate(repo_root, run_id, epoch, require_verifier=config.verifier.enabled)
    return True


def start_loop(
    repo_root: Path,
    config_path: Path,
    run_id: str | None = None,
    *,
    client: IntrospectionClient | None = None,
    git_ops: GitOps | None = None,
) -> Path:
    repo_root = repo_root.resolve()
    config_path = config_path.resolve()
    config = load_self_improvement_config(config_path)
    run_id = run_id or generate_run_id()
    validate_run_id(run_id)
    git = git_ops or GitOps(repo_root)
    intro = client or IntrospectionClient(repo_root)
    if git.current_branch() != "main":
        raise RuntimeError("self-improve start must begin on main")
    git.ensure_clean_worktree()

    run = bootstrap_run(repo_root, config_path, config, run_id)
    bootstrap_commit = git.commit_paths(
        candidate_commit_paths(repo_root, run_id), f"Bootstrap self-improvement run {run_id}"
    )
    git.push_branch("main")
    record_bootstrap_commit(repo_root, run_id, bootstrap_commit)
    payload = intro.create_runtime(run.manifest.relative_to(repo_root))
    runtime_id = _find_id(payload, ("runtime_id", "id"))
    if runtime_id is None:
        raise RuntimeError(f"runtime creation did not return an id: {payload}")
    record_runtime(repo_root, run_id, runtime_id)
    runner_config = runner_config_with_runtime_metadata(
        repo_root, run_id, RunnerConfig(repo_root=repo_root, runtime_id=runtime_id)
    )
    run_epoch_split(repo_root, config, run_id, 0, "train", runner_config)
    run_epoch_split(repo_root, config, run_id, 0, "test", runner_config)
    refresh_metrics(repo_root, run_id)
    update_best_candidate(repo_root, run_id)

    branch = f"self-improvement/{run_id}"
    git.create_branch(branch, base="main")
    record_pr_branch(repo_root, run_id, branch)
    for epoch in range(1, config.loop.max_epochs + 1):
        changed = review_and_promote(repo_root, run_id, epoch, config)
        if changed:
            commit = git.commit_paths(
                candidate_commit_paths(repo_root, run_id),
                f"Self-improvement {run_id} epoch {epoch:03d}",
            )
            git.push_branch(branch)
            runtime = load_runtime_metadata(repo_root, run_id)
            if runtime.pr_ref is None:
                pr = git.open_draft_pr(
                    branch,
                    "main",
                    f"Self-improvement run {run_id}",
                    f"Recipe candidates and train results for {run_id}.",
                )
                runtime = record_pr(repo_root, run_id, pr.number, branch, pr.url)
            if runtime.pr_ref is None:
                raise RuntimeError("PR ref was not recorded")
            version_id = wait_for_ready_version(intro, runtime_id, commit, runtime.pr_ref)
            intro.pin_runtime_version(version_id)
            record_candidate(repo_root, run_id, commit, version_id)
            runner_config = runner_config_with_runtime_metadata(
                repo_root, run_id, RunnerConfig(repo_root=repo_root, runtime_id=version_id)
            )
        run_epoch_split(repo_root, config, run_id, epoch, "train", runner_config)
        if epoch % config.loop.test_every == 0 or epoch == config.loop.max_epochs:
            run_epoch_split(repo_root, config, run_id, epoch, "test", runner_config)
        refresh_metrics(repo_root, run_id)
        best = update_best_candidate(repo_root, run_id)
        if best["is_new_best"] and best["best_epoch"] == epoch and changed:
            _record_checkpoint(run.results_dir, best, load_runtime_metadata(repo_root, run_id))
    return run.results_dir


def _find_id(payload: object, names: tuple[str, ...]) -> str | None:
    if isinstance(payload, dict):
        for name in names:
            value = payload.get(name)
            if isinstance(value, str) and value:
                return value
        for value in payload.values():
            found = _find_id(value, names)
            if found is not None:
                return found
    return None


def _record_checkpoint(results_dir: Path, best: dict[str, Any], runtime: Any) -> None:
    best.update(
        {
            "checkpoint_created": True,
            "checkpoint_commit": runtime.candidate_commit,
            "pr_created": runtime.pr_url is not None,
            "pr_url": runtime.pr_url,
        }
    )
    path = results_dir / "metadata" / "best_candidate.json"
    path.write_text(json.dumps(best, indent=2, sort_keys=True) + "\n", encoding="utf-8")
