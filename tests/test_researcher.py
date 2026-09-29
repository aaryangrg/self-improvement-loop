from __future__ import annotations

import json
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from runner.researcher import (
    _research_prompt,
    build_permission_overrides,
    changed_paths,
    ensure_codex_login,
    prepare_research_workspace,
    recover_researcher,
    revise_researcher,
    run_researcher,
    snapshot_paths,
    validate_research_result,
)
from runner.self_improvement import RuntimeMetadata, write_runtime_metadata


class ResearcherWorkspaceTest(unittest.TestCase):
    def test_start_login_checks_dedicated_profile_and_prompts_when_needed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            responses = [
                subprocess.CompletedProcess([], 1, stdout="Not logged in", stderr=""),
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0, stdout="Logged in", stderr=""),
            ]
            with (
                patch.dict("os.environ", {"HOME": str(root), "OPENAI_API_KEY": "unused"}),
                patch("runner.researcher.subprocess.run", side_effect=responses) as run,
                patch("runner.researcher.sys.stdin") as stdin,
            ):
                stdin.isatty.return_value = True
                ensure_codex_login(root)

            self.assertEqual(run.call_count, 3)
            self.assertEqual(run.call_args_list[0].args[0][-2:], ["login", "status"])
            self.assertEqual(run.call_args_list[1].args[0][-1], "login")
            for call in run.call_args_list:
                self.assertEqual(
                    call.kwargs["env"]["CODEX_HOME"],
                    str((root / ".self-improvement-loop" / "codex").resolve()),
                )
                self.assertNotIn("OPENAI_API_KEY", call.kwargs["env"])

    def test_start_login_reports_command_without_tty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            response = subprocess.CompletedProcess([], 1, stdout="Not logged in", stderr="")
            with (
                patch.dict("os.environ", {"HOME": str(root)}),
                patch("runner.researcher.subprocess.run", return_value=response) as run,
                patch("runner.researcher.sys.stdin") as stdin,
            ):
                stdin.isatty.return_value = False
                with self.assertRaisesRegex(RuntimeError, "codex login"):
                    ensure_codex_login(root)
            self.assertEqual(run.call_count, 1)

    def make_run(self, root: Path) -> Path:
        recipe = root / "recipes" / "self-improvement" / "run-001" / "legal-agent"
        recipe.mkdir(parents=True)
        (recipe / "SYSTEM.md").write_text("working prompt\n", encoding="utf-8")
        (recipe / "package.json").write_text(
            '{"name":"agent","pi":{"agents":["./agents/*.yaml"]}}', encoding="utf-8"
        )
        (recipe / "agents").mkdir()
        (recipe / "agents" / "agent.yaml").write_text(
            "name: agent\nai:\n  model: anthropic/claude-sonnet-4-6\ntools: [bash]\nskills: []\n",
            encoding="utf-8",
        )
        baseline = (
            root / "results" / "self-improvement" / "run-001" / "references" / "baseline_recipe"
        )
        baseline.mkdir(parents=True)
        (baseline / "SYSTEM.md").write_text("baseline prompt\n", encoding="utf-8")
        results = root / "results" / "self-improvement" / "run-001"
        for epoch in range(2):
            epoch_dir = results / "epochs" / f"epoch-{epoch:03d}"
            epoch_dir.mkdir(parents=True)
            (epoch_dir / "aggregate.json").write_text(
                json.dumps({"epoch": epoch, "task_pass_rate": 0.5}), encoding="utf-8"
            )
        task_dir = results / "epochs" / "epoch-000" / "sample-task"
        trial_dir = task_dir / "trial-001"
        trial_dir.mkdir(parents=True)
        (task_dir / "task_summary.json").write_text(
            json.dumps(
                {
                    "task_id": "sample-task",
                    "tasks_passed": 0,
                    "tasks_evaluated": 1,
                    "rubrics_passed": 1,
                    "rubrics_evaluated": 2,
                    "score_files": ["trial-001/evaluation/scores.json"],
                }
            ),
            encoding="utf-8",
        )
        (trial_dir / "trial_summary.json").write_text(
            json.dumps({"task_id": "sample-task", "trial": 1, "rubrics_passed": 1}),
            encoding="utf-8",
        )
        (trial_dir / "conversation.json").write_text('{"messages": []}', encoding="utf-8")
        evaluator_dir = trial_dir / "evaluation" / "gpt-6-sol"
        evaluator_dir.mkdir(parents=True)
        (evaluator_dir / "scores.json").write_text(
            '{"criteria_results": ["evaluator-only criteria text"]}', encoding="utf-8"
        )
        (trial_dir / "evaluation.json").write_text(
            '{"output_files": ["evaluation/gpt-6-sol/scores.json"]}', encoding="utf-8"
        )
        test_dir = results / "test" / "epoch-000"
        test_dir.mkdir(parents=True)
        (test_dir / "secret.json").write_text("held out", encoding="utf-8")
        workspace = results / "workspace"
        workspace.mkdir()
        (workspace / "journal.md").write_text("journal\n", encoding="utf-8")
        metadata = RuntimeMetadata(
            run_id="run-001",
            runtime_name="self-improvement-run-001",
            environment="staging",
            manifest=".introspection/self-improvement-run-001.yaml",
            working_recipe="recipes/self-improvement/run-001/legal-agent",
        )
        write_runtime_metadata(root, metadata)
        (results / "metadata" / "run.json").write_text(
            json.dumps(
                {
                    "config": {
                        "researcher": {
                            "model": "gpt-6-sol",
                            "reasoning_effort": "xhigh",
                            "epoch_session_mode": "fresh",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return results

    def test_prepare_workspace_stages_only_recipe_and_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results = self.make_run(root)
            legacy_workspace = results / "metadata" / "researcher" / "epoch-002" / "workspace"
            legacy_workspace.mkdir(parents=True)
            (legacy_workspace / "unsanitized-evaluation.json").write_text(
                "legacy evaluator payload", encoding="utf-8"
            )

            workspace = prepare_research_workspace(root, "run-001", 2)

            self.assertEqual((workspace / "recipe" / "SYSTEM.md").read_text(), "working prompt\n")
            staged_agent = (workspace / "recipe" / "agents" / "agent.yaml").read_text()
            self.assertNotIn("ai:", staged_agent)
            self.assertIn("tools:", staged_agent)
            self.assertFalse((workspace / "references").exists())
            self.assertFalse((workspace / "epochs").exists())
            self.assertFalse((workspace / "workspace").exists())
            self.assertTrue(
                (
                    results
                    / "epochs"
                    / "epoch-000"
                    / "sample-task"
                    / "trial-001"
                    / "evaluation"
                    / "gpt-6-sol"
                    / "scores.json"
                ).exists()
            )
            self.assertFalse((workspace / "test").exists())
            self.assertFalse((workspace / "epochs" / "epoch-002").exists())
            instructions = (workspace / "AGENTS.md").read_text(encoding="utf-8")
            for skill_name in ("self-improvement-research", "introspection-recipe-editing"):
                skill_path = workspace / ".agents" / "skills" / skill_name / "SKILL.md"
                self.assertTrue(skill_path.is_file())
                self.assertIn(str(skill_path.relative_to(workspace)), instructions)
            self.assertNotEqual(workspace, legacy_workspace)
            self.assertTrue((legacy_workspace / "unsanitized-evaluation.json").exists())
            self.assertTrue((results / "workspace" / "journal.md").exists())
            self.assertTrue((results / "metadata" / "researcher" / "epoch-002").exists())

    def test_research_prompt_contains_epoch_context_and_defers_static_guidance(self) -> None:
        results = Path("/tmp/results/self-improvement/run-001")
        prompt = _research_prompt(
            "run-001",
            2,
            {"task_pass_rate": 0.5, "rubric_pass_rate": 0.75, "number_of_evaluated_tasks": 2},
            results,
        )

        self.assertIn("Train epoch 1 just completed", prompt)
        self.assertIn("task_pass_rate", prompt)
        self.assertIn(str(results / "epochs"), prompt)
        self.assertIn("Follow `AGENTS.md`", prompt)
        self.assertNotIn("held-out", prompt)

    def test_prepare_rejects_workspace_from_old_staging_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_run(root)
            workspace = prepare_research_workspace(root, "run-001", 1)
            record = workspace.parent / "sandbox.json"
            metadata = json.loads(record.read_text(encoding="utf-8"))
            metadata["format_version"] = 3
            record.write_text(json.dumps(metadata), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "metadata does not match"):
                prepare_research_workspace(root, "run-001", 1)

    def test_permission_profile_limits_write_roots_to_recipe_and_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = (root / "codex-workspace").resolve()
            results = root / "results" / "self-improvement" / "run-001"
            for name in ("recipe", "instructions", ".agents"):
                (workspace / name).mkdir(parents=True)

            overrides = build_permission_overrides(workspace, results)
            results = results.resolve()

            self.assertIn('default_permissions="researcher"', overrides)
            self.assertFalse(any("extends" in override for override in overrides))
            config = tomllib.loads("\n".join(overrides))
            filesystem = config["permissions"]["researcher"]["filesystem"]
            self.assertEqual(filesystem[":root"], "deny")
            self.assertEqual(filesystem[str(workspace / "recipe")], "write")
            self.assertEqual(filesystem[str(results / "epochs")], "read")
            self.assertEqual(filesystem[str(results / "references" / "baseline_recipe")], "read")
            self.assertEqual(filesystem[str(results / "workspace")], "write")
            self.assertNotIn(str(results / "test"), filesystem)
            self.assertNotIn(str(results / "metadata"), filesystem)

    def test_changed_paths_are_relative_and_detect_read_only_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "recipe").mkdir()
            (root / "epochs" / "epoch-000").mkdir(parents=True)
            (root / "recipe" / "SYSTEM.md").write_text("new", encoding="utf-8")
            (root / "epochs" / "epoch-000" / "aggregate.json").write_text("same")
            before = snapshot_paths(root)
            (root / "recipe" / "SYSTEM.md").write_text("newer", encoding="utf-8")
            (root / "epochs" / "epoch-000" / "aggregate.json").write_text("tampered")

            actual = changed_paths(root, before)

            self.assertEqual(actual, {"recipe/SYSTEM.md", "epochs/epoch-000/aggregate.json"})

    def test_research_result_requires_expected_shape(self) -> None:
        valid = {
            "hypothesis": "A general workflow improvement.",
            "changes_made": ["Added a coverage check because reviews omitted source issues."],
            "expected_outcome": "Fewer material omissions on the next train epoch.",
        }

        validate_research_result(valid)

        with self.assertRaises(ValueError):
            validate_research_result({**valid, "unmodeled": True})
        with self.assertRaisesRegex(ValueError, "expected_outcome"):
            validate_research_result(
                {key: value for key, value in valid.items() if key != "expected_outcome"}
            )
        with self.assertRaisesRegex(ValueError, "changes_made"):
            validate_research_result({**valid, "changes_made": [{"path": "recipe/SYSTEM.md"}]})

    def test_researcher_stages_recipe_and_writes_notes_directly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results = self.make_run(root)
            prepare_research_workspace(root, "run-001", 1)
            result = {
                "hypothesis": "A general instruction will reduce omissions.",
                "changes_made": ["Clarified the workflow to cover all source issues."],
                "expected_outcome": "Fewer omissions on the next train epoch.",
            }

            def fake_codex(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
                dedicated_home = root / ".self-improvement-loop" / "codex"
                self.assertEqual(kwargs["env"]["CODEX_HOME"], str(dedicated_home.resolve()))
                self.assertTrue(dedicated_home.is_dir())
                self.assertNotEqual(kwargs["env"]["HOME"], kwargs["env"]["CODEX_HOME"])
                self.assertIn("--ignore-user-config", command)
                self.assertIn("--strict-config", command)
                self.assertEqual(command[command.index("--model") + 1], "gpt-6-sol")
                self.assertIn('model_reasoning_effort="xhigh"', command)
                stdout = kwargs["stdout"]
                stdout.write('{"type":"thread.started","thread_id":"thread-123"}\n')
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text(json.dumps(result), encoding="utf-8")
                workspace = Path(str(kwargs["cwd"]))
                (workspace / "recipe" / "SYSTEM.md").write_text("improved recipe\n")
                (results / "workspace" / "journal.md").write_text("new research note\n")
                self.assertIn(str(results / "epochs"), command[-1])
                self.assertIn(str(results / "references" / "baseline_recipe"), command[-1])
                return subprocess.CompletedProcess(command, 0)

            with (
                patch.dict(
                    "os.environ",
                    {
                        "HOME": str(root),
                        "CODEX_HOME": str(root / "personal-codex"),
                        "OPENAI_API_KEY": "unused",
                    },
                ),
                patch("runner.researcher.subprocess.run", side_effect=fake_codex),
            ):
                artifacts = run_researcher(root, "run-001", 1)

            self.assertEqual(
                (root / "recipes/self-improvement/run-001/legal-agent/SYSTEM.md").read_text(),
                "working prompt\n",
            )
            self.assertEqual(
                (artifacts / "sandbox" / "recipe" / "SYSTEM.md").read_text(),
                "improved recipe\n",
            )
            self.assertEqual(
                (results / "workspace" / "journal.md").read_text(), "new research note\n"
            )
            run_metadata = json.loads((artifacts / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_metadata["thread_id"], "thread-123")
            self.assertEqual(run_metadata["model"], "gpt-6-sol")
            self.assertEqual(run_metadata["reasoning_effort"], "xhigh")
            self.assertEqual(run_metadata["status"], "pending_verification")
            self.assertEqual(run_metadata["result"], result)
            self.assertEqual(
                run_metadata["changed_paths"], ["recipe/SYSTEM.md", "workspace/journal.md"]
            )
            self.assertTrue((artifacts / "events.jsonl").is_file())
            with self.assertRaises(FileExistsError):
                run_researcher(root, "run-001", 1)

    def test_researcher_rejects_read_only_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_run(root)

            def fake_codex(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                kwargs["stdout"].write('{"type":"thread.started","thread_id":"thread-123"}\n')
                result_path = Path(command[command.index("--output-last-message") + 1])
                result_path.write_text(
                    json.dumps(
                        {
                            "hypothesis": "None.",
                            "changes_made": [],
                            "expected_outcome": "None.",
                        }
                    ),
                    encoding="utf-8",
                )
                (Path(kwargs["cwd"]) / "AGENTS.md").write_text("tampered\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)

            with (
                patch("runner.researcher.subprocess.run", side_effect=fake_codex),
                self.assertRaisesRegex(RuntimeError, "changed read-only paths"),
            ):
                run_researcher(root, "run-001", 1)

            recipe = root / "recipes" / "self-improvement" / "run-001" / "legal-agent"
            self.assertEqual((recipe / "SYSTEM.md").read_text(encoding="utf-8"), "working prompt\n")

    def test_recover_completed_researcher_without_reinvoking_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results = self.make_run(root)
            workspace = prepare_research_workspace(root, "run-001", 1)
            artifact = workspace.parent
            (workspace / "recipe" / "SYSTEM.md").write_text("improved\n", encoding="utf-8")
            (results / "workspace" / "journal.md").write_text("hypothesis\n", encoding="utf-8")
            (artifact / "researcher_result.json").write_text(
                json.dumps(
                    {
                        "status": "ready_for_review",
                        "summary": "Added a coverage procedure.",
                        "hypothesis": "A coverage procedure reduces omissions.",
                        "changes": [
                            {"path": "recipe/SYSTEM.md", "reason": "Added a coverage step."},
                            {"path": "../workspace/journal.md", "reason": "Recorded evidence."},
                        ],
                        "next_experiment": "Check whether omissions decrease.",
                    }
                ),
                encoding="utf-8",
            )
            (artifact / "run.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "return_code": 0,
                        "thread_id": "thread-123",
                        "error": "researcher result contains disallowed path",
                    }
                ),
                encoding="utf-8",
            )

            with patch("runner.researcher.subprocess.run") as codex:
                recovered = recover_researcher(root, "run-001", 1)

            codex.assert_not_called()
            self.assertEqual(recovered, artifact)
            metadata = json.loads((artifact / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "pending_verification")
            self.assertEqual(
                metadata["result"]["hypothesis"], "A coverage procedure reduces omissions."
            )
            self.assertEqual(metadata["result"]["changes_made"], ["Added a coverage step."])
            self.assertEqual(
                metadata["changed_paths"], ["recipe/SYSTEM.md", "workspace/journal.md"]
            )

    def test_researcher_requests_revision_when_only_notes_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results = self.make_run(root)

            def fake_codex(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                kwargs["stdout"].write('{"type":"thread.started","thread_id":"thread-123"}\n')
                result_path = Path(command[command.index("--output-last-message") + 1])
                result_path.write_text(
                    json.dumps(
                        {
                            "hypothesis": "A general improvement could help.",
                            "changes_made": ["Recorded an alternative approach in research notes."],
                            "expected_outcome": "No score change until the recipe is edited.",
                        }
                    ),
                    encoding="utf-8",
                )
                (results / "workspace" / "journal.md").write_text("new analysis\n")
                return subprocess.CompletedProcess(command, 0)

            with patch("runner.researcher.subprocess.run", side_effect=fake_codex):
                artifact_dir = run_researcher(root, "run-001", 1)
            metadata = json.loads((artifact_dir / "run.json").read_text())
            self.assertEqual(metadata["status"], "needs_recipe_edit")
            self.assertEqual(metadata["thread_id"], "thread-123")

    def test_revision_resumes_same_thread_and_checks_observed_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results = self.make_run(root)
            workspace = prepare_research_workspace(root, "run-001", 1)
            artifact_dir = workspace.parent
            (artifact_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "pending_verification",
                        "thread_id": "thread-123",
                        "candidate_snapshot": snapshot_paths(workspace / "recipe"),
                        "notes_snapshot": snapshot_paths(results / "workspace"),
                    }
                ),
                encoding="utf-8",
            )
            feedback = artifact_dir / "feedback.json"
            feedback.write_text(
                json.dumps(
                    {
                        "verdict": "reject",
                        "issues": ["The wording refers to a sample."],
                    }
                ),
                encoding="utf-8",
            )

            def fake_codex(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                self.assertEqual(command[1:4], ["exec", "resume", "thread-123"])
                self.assertNotIn("--ephemeral", command)
                self.assertEqual(command[command.index("--model") + 1], "gpt-6-sol")
                self.assertIn('model_reasoning_effort="xhigh"', command)
                (workspace / "recipe" / "SYSTEM.md").write_text("generalized guidance\n")
                Path(command[command.index("--output-last-message") + 1]).write_text(
                    json.dumps(
                        {
                            "hypothesis": "General guidance improves completeness.",
                            "changes_made": ["Removed sample-specific wording for generality."],
                            "expected_outcome": "Improved issue coverage across tasks.",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0)

            with patch("runner.researcher.subprocess.run", side_effect=fake_codex):
                revision_dir = revise_researcher(root, "run-001", 1, feedback)
            self.assertTrue((revision_dir / "researcher_result.json").exists())
            metadata = json.loads((artifact_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "pending_verification")
            self.assertEqual(metadata["revision_count"], 1)

    def test_revision_without_recipe_edit_requests_another_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results = self.make_run(root)
            workspace = prepare_research_workspace(root, "run-001", 1)
            artifact_dir = workspace.parent
            (artifact_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "needs_recipe_edit",
                        "thread_id": "thread-123",
                        "candidate_snapshot": snapshot_paths(workspace / "recipe"),
                        "notes_snapshot": snapshot_paths(results / "workspace"),
                    }
                ),
                encoding="utf-8",
            )
            feedback = artifact_dir / "feedback.json"
            feedback.write_text(
                json.dumps({"phase": "no_recipe_edit", "message": "No recipe edit observed."})
            )

            def fake_codex(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                self.assertEqual(command[1:4], ["exec", "resume", "thread-123"])
                (results / "workspace" / "journal.md").write_text("new analysis\n")
                Path(command[command.index("--output-last-message") + 1]).write_text(
                    json.dumps(
                        {
                            "hypothesis": "General improvement.",
                            "changes_made": ["Recorded the analysis without editing the recipe."],
                            "expected_outcome": "No recipe effect yet.",
                        }
                    )
                )
                return subprocess.CompletedProcess(command, 0)

            with patch("runner.researcher.subprocess.run", side_effect=fake_codex):
                revise_researcher(root, "run-001", 1, feedback)

            metadata = json.loads((artifact_dir / "run.json").read_text())
            self.assertEqual(metadata["status"], "needs_recipe_edit")
            self.assertEqual(metadata["revision_count"], 1)

    def test_revision_rejects_read_only_workspace_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results = self.make_run(root)
            workspace = prepare_research_workspace(root, "run-001", 1)
            artifact_dir = workspace.parent
            (artifact_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "pending_verification",
                        "thread_id": "thread-123",
                        "candidate_snapshot": snapshot_paths(workspace / "recipe"),
                        "notes_snapshot": snapshot_paths(results / "workspace"),
                    }
                ),
                encoding="utf-8",
            )
            feedback = artifact_dir / "feedback.json"
            feedback.write_text(json.dumps({"verdict": "reject"}), encoding="utf-8")

            def fake_codex(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                (workspace / "AGENTS.md").write_text("tampered\n", encoding="utf-8")
                Path(command[command.index("--output-last-message") + 1]).write_text(
                    json.dumps(
                        {
                            "hypothesis": "General guidance improves coverage.",
                            "changes_made": ["Clarified the instructions."],
                            "expected_outcome": "Fewer omissions.",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0)

            with (
                patch("runner.researcher.subprocess.run", side_effect=fake_codex),
                self.assertRaisesRegex(RuntimeError, "changed read-only paths"),
            ):
                revise_researcher(root, "run-001", 1, feedback)
            metadata = json.loads((artifact_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "revision_failed")

    def test_invalid_revision_returns_to_candidate_check_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results = self.make_run(root)
            workspace = prepare_research_workspace(root, "run-001", 1)
            artifact_dir = workspace.parent
            (artifact_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "pending_verification",
                        "thread_id": "thread-123",
                        "candidate_snapshot": snapshot_paths(workspace / "recipe"),
                        "notes_snapshot": snapshot_paths(results / "workspace"),
                    }
                ),
                encoding="utf-8",
            )
            feedback = artifact_dir / "feedback.json"
            feedback.write_text(json.dumps({"phase": "check", "message": "Revise candidate."}))

            def fake_codex(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                (workspace / "recipe" / "agents" / "agent.yaml").write_text(
                    "name: agent\nai:\n  model: openai/other\ntools: [bash]\n"
                )
                Path(command[command.index("--output-last-message") + 1]).write_text(
                    json.dumps(
                        {
                            "hypothesis": "A tool change might help.",
                            "changes_made": ["Changed tools to improve document parsing."],
                            "expected_outcome": "More complete source extraction.",
                        }
                    )
                )
                return subprocess.CompletedProcess(command, 0)

            with patch("runner.researcher.subprocess.run", side_effect=fake_codex):
                revise_researcher(root, "run-001", 1, feedback)

            metadata = json.loads((artifact_dir / "run.json").read_text())
            self.assertEqual(metadata["status"], "pending_verification")

    def test_runtime_failure_revises_promoted_candidate_in_same_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results = self.make_run(root)
            workspace = prepare_research_workspace(root, "run-001", 1)
            artifact_dir = workspace.parent
            (artifact_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "promoted",
                        "candidate_promoted": True,
                        "thread_id": "thread-123",
                        "candidate_snapshot": snapshot_paths(workspace / "recipe"),
                        "notes_snapshot": snapshot_paths(results / "workspace"),
                    }
                )
            )
            feedback = artifact_dir / "build-failure.json"
            feedback.write_text(json.dumps({"phase": "build", "message": "bad extension"}))

            def fake_codex(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                self.assertEqual(command[1:4], ["exec", "resume", "thread-123"])
                (workspace / "recipe" / "SYSTEM.md").write_text("repaired\n")
                Path(command[command.index("--output-last-message") + 1]).write_text(
                    json.dumps(
                        {
                            "hypothesis": "Fix extension.",
                            "changes_made": ["Repaired startup instructions."],
                            "expected_outcome": "Startup smoke completes.",
                        }
                    )
                )
                return subprocess.CompletedProcess(command, 0)

            with patch("runner.researcher.subprocess.run", side_effect=fake_codex):
                revise_researcher(root, "run-001", 1, feedback)
            metadata = json.loads((artifact_dir / "run.json").read_text())
            self.assertEqual(metadata["status"], "pending_verification")
            self.assertFalse(metadata["candidate_promoted"])


if __name__ == "__main__":
    unittest.main()
