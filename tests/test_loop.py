from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from runner.git_ops import PullRequestInfo
from runner.loop import review_and_promote, start_loop, wait_for_ready_version
from runner.self_improvement import (
    EvaluationConfig,
    LoopConfig,
    ResearcherConfig,
    SelfImprovementConfig,
    VerifierConfig,
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
