from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from runner.config import RunnerConfig
from runner.git_ops import PullRequestInfo
from runner.loop import (
    _resume_split,
    resume_loop,
    review_and_promote,
    start_loop,
    wait_for_ready_version,
)
from runner.self_improvement import (
    EvaluationConfig,
    LoopConfig,
    ResearcherConfig,
    RuntimeMetadata,
    SelfImprovementConfig,
    VerifierConfig,
    bootstrap_run,
    load_self_improvement_config,
    write_runtime_metadata,
)


class FakeClient:
    def list_runtime_versions(self, runtime_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "old",
                "recipe_ref": "pr/7",
                "recipe_id": "recipe-old",
                "image_build_status": "ready",
            },
            {
                "id": "new",
                "recipe_ref": "pr/7",
                "recipe_id": "recipe-new",
                "image_build_status": "ready",
            },
        ]

    def get_recipe(self, recipe_id: str) -> dict[str, Any]:
        return {
            "git_commit_sha": "wanted" if recipe_id == "recipe-new" else "previous",
            "validation": {"status": "valid"},
        }


class LoopTest(unittest.TestCase):
    def test_resume_archives_incomplete_split_before_replaying(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = root / "results" / "self-improvement" / "run-001" / "epochs" / "epoch-001"
            old.mkdir(parents=True)
            (old / "partial.txt").write_text("old task", encoding="utf-8")
            config = SelfImprovementConfig(
                id="smoke",
                description="",
                seed_recipe=Path("recipe"),
                split_config=Path("split"),
                loop=LoopConfig(),
                evaluation=EvaluationConfig(judges=("gpt-6-sol",)),
                researcher=ResearcherConfig(model="gpt-6-sol"),
                verifier=VerifierConfig(model="gpt-6-sol"),
            )

            def replay(*args: object) -> None:
                old.mkdir()
                (old / "fresh.txt").write_text("new task", encoding="utf-8")

            with patch("runner.loop.run_epoch_split", side_effect=replay) as run_epoch:
                _resume_split(root, config, "run-001", 1, "train", RunnerConfig(repo_root=root))

            run_epoch.assert_called_once()
            self.assertEqual((old / "fresh.txt").read_text(encoding="utf-8"), "new task")
            archive = (
                root
                / "results"
                / "self-improvement"
                / "run-001"
                / "metadata"
                / "replays"
                / "train"
                / "epoch-001"
                / "attempt-001"
            )
            self.assertEqual((archive / "partial.txt").read_text(encoding="utf-8"), "old task")

    def test_resume_reuses_baseline_and_pinned_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            recipe = root / "recipes" / "legal-agent"
            recipe.mkdir(parents=True)
            (recipe / "SYSTEM.md").write_text("baseline\n", encoding="utf-8")
            split = root / "experiment_configs" / "smoke.yaml"
            split.parent.mkdir()
            split.write_text("id: original\n", encoding="utf-8")
            config_path = root / "self-improvement-configs" / "smoke.yaml"
            config_path.parent.mkdir()
            config_path.write_text(
                "id: smoke\nseed_recipe: recipes/legal-agent\n"
                "split_config: experiment_configs/smoke.yaml\n"
                "evaluation:\n  judges: [gpt-6-sol]\n  reasoning_effort: medium\n"
                "loop:\n  max_epochs: 1\n  test_every: 1\n"
                "researcher:\n  model: gpt-6-sol\n  reasoning_effort: xhigh\n"
                "verifier:\n  model: gpt-6-sol\n  reasoning_effort: medium\n",
                encoding="utf-8",
            )
            config = load_self_improvement_config(config_path)
            run = bootstrap_run(root, config_path, config, "run-001")
            write_runtime_metadata(
                root,
                RuntimeMetadata(
                    run_id="run-001",
                    runtime_name="self-improvement-run-001",
                    environment="staging",
                    manifest=str(run.manifest.relative_to(root)),
                    working_recipe=str(run.working_recipe.relative_to(root)),
                    runtime_id="baseline-version",
                    pr_branch="self-improvement/run-001",
                ),
            )
            pinned_split = run.results_dir / "metadata" / "split_config.yaml"
            pinned_split.parent.mkdir(parents=True, exist_ok=True)
            pinned_split.write_text("id: original\n", encoding="utf-8")
            for parent in (run.results_dir / "epochs", run.results_dir / "test"):
                epoch = parent / "epoch-000"
                epoch.mkdir(exist_ok=True)
                (epoch / "aggregate.json").write_text('{"task_pass_rate":0.5}')
                (epoch / "progress.json").write_text('{"status":"completed"}')
            split.write_text("id: changed\n", encoding="utf-8")

            class Git:
                def current_branch(self) -> str:
                    return "self-improvement/run-001"

                def ensure_clean_worktree(self) -> None:
                    return None

            def run_split(*args: Any, **kwargs: Any) -> Path:
                epoch = kwargs.get("epoch", args[3] if len(args) > 3 else None)
                kind = kwargs.get("split_kind", args[4] if len(args) > 4 else None)
                target = (
                    run.results_dir
                    / ("epochs" if kind == "train" else "test")
                    / f"epoch-{epoch:03d}"
                )
                target.mkdir(parents=True)
                (target / "aggregate.json").write_text('{"task_pass_rate":0.5}')
                (target / "progress.json").write_text('{"status":"completed"}')
                return target

            with (
                patch("runner.loop.ensure_codex_login"),
                patch("runner.loop.start_dashboard", return_value="http://127.0.0.1:8501/"),
                patch("runner.loop.recover_researcher", return_value=run.results_dir / "metadata"),
                patch("runner.loop.review_and_promote", return_value="candidate-version") as review,
                patch("runner.loop.run_epoch_split", side_effect=run_split) as run_epoch,
                patch("runner.loop.refresh_metrics"),
                patch("runner.loop.update_best_candidate", return_value={"is_new_best": False}),
            ):
                resume_loop(root, "run-001", git_ops=Git(), client=object())

            review.assert_called_once()
            self.assertEqual(run_epoch.call_count, 2)
            for call in run_epoch.call_args_list:
                self.assertEqual(call.args[1].split_config, pinned_split.resolve())
                self.assertEqual(call.args[3], 1)

    def test_no_edit_revises_same_candidate_before_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "metadata" / "researcher" / "epoch-001"
            artifact.mkdir(parents=True)
            metadata_path = artifact / "run.json"
            metadata_path.write_text(json.dumps({"status": "needs_recipe_edit"}))
            config = SelfImprovementConfig(
                id="test",
                description="",
                seed_recipe=Path("recipe"),
                split_config=Path("split"),
                loop=LoopConfig(max_code_revisions=2),
                evaluation=EvaluationConfig(judges=("gpt-6-sol",)),
                researcher=ResearcherConfig(model="gpt-6-sol"),
                verifier=VerifierConfig(model="gpt-6-sol", enabled=False),
            )

            def revise(*args: object) -> None:
                feedback = json.loads(Path(args[3]).read_text())
                self.assertEqual(feedback["phase"], "no_recipe_edit")
                metadata_path.write_text(json.dumps({"status": "pending_verification"}))

            runtime = type(
                "Runtime",
                (),
                {
                    "runtime_id": "runtime-1",
                    "manifest": ".introspection/run-001.yaml",
                    "working_recipe": "recipe",
                    "pr_ref": "pr/7",
                    "pr_branch": "branch",
                },
            )()

            class Git:
                def commit_paths(self, *args: object) -> str:
                    return "commit-1"

                def push_branch(self, branch: str) -> None:
                    return None

            class Intro:
                def pin_runtime_version(self, version: str) -> None:
                    return None

            with (
                patch("runner.loop.run_researcher", return_value=artifact),
                patch("runner.loop.revise_researcher", side_effect=revise) as revise_mock,
                patch("runner.loop.check_candidate") as check,
                patch("runner.loop.promote_candidate"),
                patch("runner.loop.wait_for_ready_version", return_value="version-1"),
                patch("runner.loop.smoke_candidate"),
                patch("runner.loop.load_runtime_metadata", return_value=runtime),
                patch("runner.loop.candidate_commit_paths", return_value=("recipe",)),
                patch("runner.loop.record_candidate"),
            ):
                version = review_and_promote(root, "run-001", 1, config, Git(), Intro())

            self.assertEqual(version, "version-1")
            self.assertEqual(revise_mock.call_count, 1)
            self.assertEqual(check.call_count, 1)

    def test_no_edit_stops_after_code_revision_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "metadata" / "researcher" / "epoch-001"
            artifact.mkdir(parents=True)
            (artifact / "run.json").write_text(json.dumps({"status": "needs_recipe_edit"}))
            config = SelfImprovementConfig(
                id="test",
                description="",
                seed_recipe=Path("recipe"),
                split_config=Path("split"),
                loop=LoopConfig(max_code_revisions=2),
                evaluation=EvaluationConfig(judges=("gpt-6-sol",)),
                researcher=ResearcherConfig(model="gpt-6-sol"),
                verifier=VerifierConfig(model="gpt-6-sol", enabled=False),
            )
            with (
                patch("runner.loop.run_researcher", return_value=artifact),
                patch("runner.loop.revise_researcher") as revise,
                patch("runner.loop.check_candidate") as check,
                self.assertRaisesRegex(RuntimeError, "no recipe change after 2"),
            ):
                review_and_promote(root, "run-001", 1, config, object(), object())
            self.assertEqual(revise.call_count, 2)
            check.assert_not_called()

    def test_start_runs_baseline_and_configured_epochs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            recipe = root / "recipes" / "legal-agent"
            recipe.mkdir(parents=True)
            (recipe / "SYSTEM.md").write_text("baseline\n", encoding="utf-8")
            split = root / "experiment_configs" / "smoke.yaml"
            split.parent.mkdir()
            split.write_text("id: smoke\n", encoding="utf-8")
            config = root / "self-improvement-configs" / "smoke.yaml"
            config.parent.mkdir()
            config.write_text(
                "id: smoke\n"
                "seed_recipe: recipes/legal-agent\n"
                "split_config: experiment_configs/smoke.yaml\n"
                "evaluation:\n  judges: [gpt-6-sol]\n  reasoning_effort: medium\n"
                "loop:\n  max_epochs: 2\n  test_every: 3\n"
                "researcher:\n  model: gpt-6-sol\n  reasoning_effort: xhigh\n"
                "verifier:\n  model: gpt-6-sol\n  reasoning_effort: medium\n",
                encoding="utf-8",
            )

            class FakeGit:
                def __init__(self) -> None:
                    self.commits = 0

                def current_branch(self) -> str:
                    return "main"

                def ensure_clean_worktree(self) -> None:
                    return None

                def commit_paths(self, paths: tuple[str, ...], message: str) -> str:
                    self.commits += 1
                    return f"commit-{self.commits}"

                def push_branch(self, branch: str) -> None:
                    return None

                def create_branch(self, branch: str, base: str) -> None:
                    return None

                def open_draft_pr(
                    self, branch: str, base: str, title: str, body: str
                ) -> PullRequestInfo:
                    return PullRequestInfo(
                        number=7, url="https://example.test/pull/7", branch=branch
                    )

            class FakeIntro:
                def create_runtime(self, manifest: Path) -> dict[str, str]:
                    return {"id": "runtime-base"}

                def pin_runtime_version(self, runtime_id: str) -> dict[str, str]:
                    return {"id": runtime_id}

            with (
                patch("runner.loop.ensure_codex_login") as login,
                patch("runner.loop.start_dashboard", return_value="http://127.0.0.1:8501/") as ui,
                patch("runner.loop.run_epoch_split") as run_split,
                patch("runner.loop.review_and_promote", side_effect=["runtime-candidate", None]),
                patch("runner.loop.refresh_metrics"),
                patch(
                    "runner.loop.update_best_candidate",
                    return_value={
                        "is_new_best": False,
                        "best_epoch": 0,
                    },
                ),
            ):
                result = start_loop(root, config, "run-001", client=FakeIntro(), git_ops=FakeGit())

            login.assert_called_once_with(root.resolve())
            ui.assert_called_once_with(root.resolve(), "run-001")
            self.assertEqual(result, root.resolve() / "results" / "self-improvement" / "run-001")
            calls = [
                (call.args[3], call.args[4], call.args[5].runtime_id)
                for call in run_split.call_args_list
            ]
            self.assertEqual(
                calls,
                [
                    (0, "train", "runtime-base"),
                    (0, "test", "runtime-base"),
                    (1, "train", "runtime-candidate"),
                    (2, "train", "runtime-candidate"),
                    (2, "test", "runtime-candidate"),
                ],
            )

    def test_wait_matches_the_exact_commit(self) -> None:
        version = wait_for_ready_version(FakeClient(), "runtime", "wanted", "pr/7", 1, 0)
        self.assertEqual(version, "new")

    def test_wait_reports_recipe_validation_diagnostics(self) -> None:
        class InvalidClient(FakeClient):
            def get_recipe(self, recipe_id: str) -> dict[str, Any]:
                return {
                    "git_commit_sha": "wanted",
                    "validation": {"status": "invalid", "diagnostics": ["unknown tool"]},
                }

        with self.assertRaisesRegex(RuntimeError, "unknown tool"):
            wait_for_ready_version(InvalidClient(), "runtime", "wanted", "pr/7", 1, 0)

    def test_verifier_rejection_resumes_researcher_then_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "metadata" / "researcher" / "epoch-001"
            artifact.mkdir(parents=True)
            (artifact / "run.json").write_text(
                json.dumps({"status": "pending_verification"}), encoding="utf-8"
            )
            rejected = root / "rejected.json"
            rejected.write_text('{"verdict":"reject"}', encoding="utf-8")
            approved = root / "approved.json"
            approved.write_text('{"verdict":"approve"}', encoding="utf-8")
            config = SelfImprovementConfig(
                id="test",
                description="",
                seed_recipe=Path("recipe"),
                split_config=Path("split"),
                loop=LoopConfig(max_code_revisions=3),
                evaluation=EvaluationConfig(judges=("gpt-6-sol",)),
                researcher=ResearcherConfig(model="gpt-6-sol"),
                verifier=VerifierConfig(model="gpt-6-sol", max_revision_attempts=1),
            )
            with (
                patch("runner.loop.run_researcher", return_value=artifact),
                patch(
                    "runner.loop.check_candidate",
                    side_effect=[RuntimeError("invalid manifest"), None, None, None, None],
                ) as check,
                patch("runner.loop.run_verifier", side_effect=[rejected, approved]) as verify,
                patch("runner.loop.revise_researcher") as revise,
                patch("runner.loop.promote_candidate") as promote,
                patch(
                    "runner.loop.wait_for_ready_version",
                    side_effect=[
                        RuntimeError("image build failed"),
                        "version-1",
                        "version-2",
                        "version-3",
                    ],
                ),
                patch(
                    "runner.loop.smoke_candidate",
                    side_effect=[RuntimeError("startup failed"), None, None],
                ) as smoke,
                patch("runner.loop.load_runtime_metadata") as runtime,
                patch("runner.loop.record_pr") as record_pr,
                patch("runner.loop.candidate_commit_paths", return_value=("recipe",)),
                patch("runner.loop.record_candidate"),
            ):
                runtime.return_value = type(
                    "Runtime",
                    (),
                    {
                        "runtime_id": "runtime-1",
                        "manifest": ".introspection/run-001.yaml",
                        "working_recipe": "recipe",
                        "pr_ref": "pr/7",
                        "pr_branch": "branch",
                        "candidate_commit": None,
                    },
                )()
                record_pr.return_value = runtime.return_value

                class Git:
                    def commit_paths(self, *args: object) -> str:
                        return "commit-1"

                    def push_branch(self, branch: str) -> None:
                        return None

                class Intro:
                    def pin_runtime_version(self, version: str) -> None:
                        return None

                self.assertEqual(
                    review_and_promote(root, "run-001", 1, config, Git(), Intro()), "version-3"
                )
            self.assertEqual(check.call_count, 5)
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(revise.call_count, 4)
            phases = [
                json.loads(call.args[3].read_text()).get("phase") for call in revise.call_args_list
            ]
            self.assertEqual(phases[:3], ["check", "build", "smoke"])
            promote.assert_called_with(root, "run-001", 1, require_verifier=False)
            self.assertEqual(smoke.call_count, 3)


if __name__ == "__main__":
    unittest.main()
