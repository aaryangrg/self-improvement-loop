from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .candidate_validation import check_candidate, smoke_candidate
from .config import RunnerConfig
from .dashboard import start_dashboard
from .git_ops import GitOps
from .introspection import IntrospectionClient
from .progress import write_run_progress
from .researcher import ensure_codex_login, revise_researcher, run_researcher
from .self_improvement import (
    SelfImprovementConfig,
    SelfImprovementRun,
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
                diagnostics = {
                    "image_build_status": status,
                    "image_build_error": version.get("image_build_error_message"),
                    "recipe_validation": recipe.get("validation"),
                }
                raise RuntimeError(
                    f"runtime build for {commit} failed: {json.dumps(diagnostics, sort_keys=True)}"
                )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"no ready runtime version for commit {commit} within {timeout_seconds}s"
            )
        time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))


def review_and_promote(
    repo_root: Path,
    run_id: str,
    epoch: int,
    config: SelfImprovementConfig,
    git: GitOps,
    intro: IntrospectionClient,
) -> str | None:
    artifact_dir = run_researcher(repo_root, run_id, epoch)
    metadata_path = artifact_dir / "run.json"
    candidate = artifact_dir / "sandbox" / "recipe"
    code_revisions = 0
    verifier_rejections = 0

    def repair_no_edit() -> None:
        nonlocal code_revisions
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        while metadata["status"] == "needs_recipe_edit":
            if code_revisions >= config.loop.max_code_revisions:
                raise RuntimeError(
                    f"epoch {epoch} researcher made no recipe change after "
                    f"{code_revisions} code revisions: {metadata_path}"
                )
            code_revisions += 1
            feedback_dir = artifact_dir / "gates" / f"no-edit-{code_revisions:03d}"
            feedback_dir.mkdir(parents=True, exist_ok=False)
            feedback_path = _gate_feedback(
                feedback_dir,
                "no_recipe_edit",
                RuntimeError(
                    "No recipe change was observed. If you intended an edit, finish and save it. "
                    "Otherwise examine the train results and choose another generalizable "
                    "hypothesis. Make a concrete recipe change; research notes alone do not "
                    "advance the experiment."
                ),
            )
            revise_researcher(repo_root, run_id, epoch, feedback_path)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata["status"] != "pending_verification":
            raise RuntimeError(f"researcher stopped with status {metadata['status']}")

    repair_no_edit()
    max_attempts = 1 + config.loop.max_code_revisions + config.verifier.max_revision_attempts
    for attempt in range(max_attempts):
        gate_dir = artifact_dir / "gates" / f"attempt-{attempt + 1:03d}"
        gate_dir.mkdir(parents=True, exist_ok=False)
        feedback_path: Path | None = None
        try:
            runtime = load_runtime_metadata(repo_root, run_id)
            check_candidate(
                candidate,
                repo_root / runtime.working_recipe,
                repo_root / runtime.manifest,
                gate_dir / "check.json",
            )
        except RuntimeError as error:
            feedback_path = _gate_feedback(gate_dir, "check", error)
        else:
            promote_candidate(repo_root, run_id, epoch, require_verifier=False)
            commit = git.commit_paths(
                candidate_commit_paths(repo_root, run_id),
                f"Self-improvement {run_id} epoch {epoch:03d} candidate {attempt + 1}",
            )
            runtime = load_runtime_metadata(repo_root, run_id)
            if runtime.pr_branch is None or runtime.runtime_id is None:
                raise RuntimeError("candidate branch and runtime must be recorded")
            runtime_id = runtime.runtime_id
            git.push_branch(runtime.pr_branch)
            if runtime.pr_ref is None:
                pr = git.open_draft_pr(
                    runtime.pr_branch,
                    "main",
                    f"Self-improvement run {run_id}",
                    f"Recipe candidates and train results for {run_id}.",
                )
                runtime = record_pr(repo_root, run_id, pr.number, runtime.pr_branch, pr.url)
            if runtime.pr_ref is None:
                raise RuntimeError("PR ref was not recorded")
            try:
                version_id = wait_for_ready_version(intro, runtime_id, commit, runtime.pr_ref)
            except (RuntimeError, TimeoutError) as error:
                feedback_path = _gate_feedback(gate_dir, "build", error)
            else:
                try:
                    smoke_candidate(intro, version_id, RunnerConfig().agent, gate_dir / "smoke")
                except Exception as error:
                    feedback_path = _gate_feedback(gate_dir, "smoke", error)
                else:
                    feedback_path = None
                    if config.verifier.enabled:
                        verdict_path = run_verifier(repo_root, run_id, epoch)
                        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
                        if verdict["verdict"] == "reject":
                            feedback_path = verdict_path
                    if feedback_path is None:
                        intro.pin_runtime_version(version_id)
                        record_candidate(repo_root, run_id, commit, version_id)
                        return version_id
        if feedback_path is None:
            raise AssertionError("candidate gate did not return feedback")
        feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
        if feedback.get("verdict") == "reject":
            if verifier_rejections >= config.verifier.max_revision_attempts:
                raise RuntimeError(
                    f"epoch {epoch} verifier revision limit reached: {feedback_path}"
                )
            verifier_rejections += 1
        else:
            if code_revisions >= config.loop.max_code_revisions:
                raise RuntimeError(f"epoch {epoch} code revision limit reached: {feedback_path}")
            code_revisions += 1
        revise_researcher(repo_root, run_id, epoch, feedback_path)
        repair_no_edit()
    raise AssertionError("unreachable candidate gate state")


def _gate_feedback(gate_dir: Path, phase: str, error: Exception) -> Path:
    path = gate_dir / "feedback.json"
    path.write_text(
        json.dumps({"phase": phase, "message": str(error)}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


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
    ensure_codex_login(repo_root)

    run = bootstrap_run(repo_root, config_path, config, run_id)
    write_run_progress(run.results_dir, epoch=0, phase="bootstrap")
    print(f"Dashboard: {start_dashboard(repo_root, run_id)}", flush=True)
    try:
        return _execute_loop(repo_root, config, run, run_id, git, intro)
    except BaseException as error:
        current_path = run.results_dir / "metadata" / "progress.json"
        current = json.loads(current_path.read_text(encoding="utf-8"))
        write_run_progress(
            run.results_dir,
            epoch=int(current["epoch"]),
            phase=str(current["phase"]),
            status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
            message=str(error),
        )
        raise


def _execute_loop(
    repo_root: Path,
    config: SelfImprovementConfig,
    run: SelfImprovementRun,
    run_id: str,
    git: GitOps,
    intro: IntrospectionClient,
) -> Path:
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
    write_run_progress(run.results_dir, epoch=0, phase="train")
    run_epoch_split(repo_root, config, run_id, 0, "train", runner_config)
    write_run_progress(run.results_dir, epoch=0, phase="test")
    run_epoch_split(repo_root, config, run_id, 0, "test", runner_config)
    refresh_metrics(repo_root, run_id)
    update_best_candidate(repo_root, run_id)

    branch = f"self-improvement/{run_id}"
    git.create_branch(branch, base="main")
    record_pr_branch(repo_root, run_id, branch)
    for epoch in range(1, config.loop.max_epochs + 1):
        write_run_progress(run.results_dir, epoch=epoch, phase="research")
        version_id = review_and_promote(repo_root, run_id, epoch, config, git, intro)
        if version_id is not None:
            runner_config = runner_config_with_runtime_metadata(
                repo_root, run_id, RunnerConfig(repo_root=repo_root, runtime_id=version_id)
            )
        write_run_progress(run.results_dir, epoch=epoch, phase="train")
        run_epoch_split(repo_root, config, run_id, epoch, "train", runner_config)
        if epoch % config.loop.test_every == 0 or epoch == config.loop.max_epochs:
            write_run_progress(run.results_dir, epoch=epoch, phase="test")
            run_epoch_split(repo_root, config, run_id, epoch, "test", runner_config)
        refresh_metrics(repo_root, run_id)
        best = update_best_candidate(repo_root, run_id)
        if best["is_new_best"] and best["best_epoch"] == epoch and version_id is not None:
            _record_checkpoint(run.results_dir, best, load_runtime_metadata(repo_root, run_id))
    write_run_progress(
        run.results_dir, epoch=config.loop.max_epochs, phase="complete", status="completed"
    )
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
