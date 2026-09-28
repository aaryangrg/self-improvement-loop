from __future__ import annotations

import json
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from runner.researcher import snapshot_paths
from runner.self_improvement import RuntimeMetadata, write_runtime_metadata
from runner.verifier import promote_candidate, run_verifier


class VerifierTest(unittest.TestCase):
    def make_candidate(self, root: Path) -> tuple[Path, Path]:
        working = root / "recipes" / "self-improvement" / "run-001" / "legal-agent"
        working.mkdir(parents=True)
        (working / "SYSTEM.md").write_text("old\n", encoding="utf-8")
        (working / "package.json").write_text(
            '{"name":"agent","pi":{"agents":["./agents/*.yaml"]}}'
        )
        (working / "agents").mkdir()
        (working / "agents" / "agent.yaml").write_text(
            "name: agent\nai:\n  model: anthropic/claude-sonnet-4-6\ntools: [bash]\nskills: []\n"
        )
        results = root / "results" / "self-improvement" / "run-001"
        (results / "metadata").mkdir(parents=True)
        (results / "metadata" / "run.json").write_text(
            json.dumps(
                {"config": {"verifier": {"model": "gpt-6-sol", "reasoning_effort": "medium"}}}
            ),
            encoding="utf-8",
        )
        for relative in ("epochs", "workspace", "references/baseline_recipe"):
            (results / relative).mkdir(parents=True)
        candidate_dir = results / "metadata" / "researcher" / "epoch-001"
        candidate = candidate_dir / "sandbox" / "recipe"
        candidate.mkdir(parents=True)
        (candidate / "SYSTEM.md").write_text("general improvement\n", encoding="utf-8")
        (candidate / "helper.py").write_text("print('hello')\n", encoding="utf-8")
        (candidate / "package.json").write_bytes((working / "package.json").read_bytes())
        (candidate / "agents").mkdir()
        (candidate / "agents" / "agent.yaml").write_text("name: agent\ntools: [bash]\nskills: []\n")
        (candidate_dir / "sandbox.json").write_text(
            json.dumps({"recipe_snapshot": snapshot_paths(working)}), encoding="utf-8"
        )
        (candidate_dir / "run.json").write_text(
            json.dumps(
                {
                    "status": "pending_verification",
                    "candidate_promoted": False,
                    "candidate_snapshot": snapshot_paths(candidate),
                    "result": {"hypothesis": "Improve general completeness."},
                }
            ),
            encoding="utf-8",
        )
        write_runtime_metadata(
            root,
            RuntimeMetadata(
                run_id="run-001",
                runtime_name="self-improvement-run-001",
                environment="staging",
                manifest=".introspection/run-001.yaml",
                working_recipe="recipes/self-improvement/run-001/legal-agent",
            ),
        )
        return candidate, working

    def test_verifier_is_read_only_and_approved_candidate_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate, working = self.make_candidate(root)

            def fake_codex(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                self.assertIn("--ephemeral", command)
                self.assertEqual(command[command.index("--model") + 1], "gpt-6-sol")
                self.assertIn('model_reasoning_effort="medium"', command)
                attempt_dir = Path(command[command.index("-C") + 1])
                instructions = (attempt_dir / "AGENTS.md").read_text(encoding="utf-8")
                diff = (attempt_dir / "candidate.diff").read_text(encoding="utf-8")
                self.assertIn("training-sample overfitting", instructions)
                self.assertIn("-old", diff)
                self.assertIn("+general improvement", diff)
                self.assertIn("candidate.diff", command[-1])
                config = tomllib.loads(
                    "\n".join(command[i + 1] for i, arg in enumerate(command) if arg == "--config")
                )
                rules = config["permissions"]["verifier"]["filesystem"]
                self.assertEqual(rules[str(candidate.resolve())], "read")
                self.assertNotIn("write", rules.values())
                result_path = Path(command[command.index("--output-last-message") + 1])
                result_path.write_text(
                    json.dumps(
                        {
                            "verdict": "approve",
                            "summary": "General change.",
                            "issues": [],
                            "revision_instructions": "",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0)

            with patch("runner.verifier.subprocess.run", side_effect=fake_codex):
                review = run_verifier(root, "run-001", 1)
            self.assertEqual(json.loads(review.read_text())["verdict"], "approve")
            self.assertEqual((working / "SYSTEM.md").read_text(), "old\n")

            promote_candidate(root, "run-001", 1)
            self.assertEqual((working / "SYSTEM.md").read_text(), "general improvement\n")
            original = candidate.parent.parent / "verifier" / "original_recipe" / "SYSTEM.md"
            self.assertEqual(original.read_text(encoding="utf-8"), "old\n")
            self.assertIn(
                "model: anthropic/claude-sonnet-4-6",
                (working / "agents" / "agent.yaml").read_text(),
            )
            self.assertTrue((working / "helper.py").exists())

    def test_promotion_rejects_candidate_changed_since_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate, working = self.make_candidate(root)
            reviews = candidate.parent.parent / "verifier" / "attempt-001"
            reviews.mkdir(parents=True)
            (reviews / "review.json").write_text(
                json.dumps({"verdict": "approve", "candidate_snapshot": snapshot_paths(candidate)}),
                encoding="utf-8",
            )
            (candidate / "SYSTEM.md").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "candidate recipe changed"):
                promote_candidate(root, "run-001", 1)
            self.assertEqual((working / "SYSTEM.md").read_text(), "old\n")

    def test_revised_candidate_can_replace_previously_promoted_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate, working = self.make_candidate(root)
            promote_candidate(root, "run-001", 1, require_verifier=False)
            (candidate / "SYSTEM.md").write_text("fixed improvement\n", encoding="utf-8")
            metadata_path = candidate.parent.parent / "run.json"
            metadata = json.loads(metadata_path.read_text())
            metadata["status"] = "pending_verification"
            metadata["candidate_promoted"] = False
            metadata["candidate_snapshot"] = snapshot_paths(candidate)
            metadata_path.write_text(json.dumps(metadata))
            promote_candidate(root, "run-001", 1, require_verifier=False)
            self.assertEqual((working / "SYSTEM.md").read_text(), "fixed improvement\n")

    def test_verifier_accepts_promoted_candidate_for_post_smoke_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate, _ = self.make_candidate(root)
            promote_candidate(root, "run-001", 1, require_verifier=False)

            def fake_codex(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                attempt_dir = Path(command[command.index("-C") + 1])
                diff = (attempt_dir / "candidate.diff").read_text(encoding="utf-8")
                self.assertIn("-old", diff)
                self.assertIn("+general improvement", diff)
                Path(command[command.index("--output-last-message") + 1]).write_text(
                    json.dumps(
                        {
                            "verdict": "approve",
                            "summary": "General change.",
                            "issues": [],
                            "revision_instructions": "",
                        }
                    )
                )
                return subprocess.CompletedProcess(command, 0)

            with patch("runner.verifier.subprocess.run", side_effect=fake_codex):
                result = run_verifier(root, "run-001", 1)
            self.assertEqual(json.loads(result.read_text())["verdict"], "approve")

    def test_post_revision_diff_is_against_original_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate, working = self.make_candidate(root)
            promote_candidate(root, "run-001", 1, require_verifier=False)
            (candidate / "SYSTEM.md").write_text("revised improvement\n", encoding="utf-8")
            metadata_path = candidate.parent.parent / "run.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["status"] = "pending_verification"
            metadata["candidate_promoted"] = False
            metadata["candidate_snapshot"] = snapshot_paths(candidate)
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            promote_candidate(root, "run-001", 1, require_verifier=False)

            def fake_codex(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                attempt_dir = Path(command[command.index("-C") + 1])
                diff = (attempt_dir / "candidate.diff").read_text(encoding="utf-8")
                self.assertIn("-old", diff)
                self.assertIn("+revised improvement", diff)
                self.assertNotIn("-general improvement", diff)
                Path(command[command.index("--output-last-message") + 1]).write_text(
                    json.dumps(
                        {
                            "verdict": "approve",
                            "summary": "General change.",
                            "issues": [],
                            "revision_instructions": "",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0)

            with patch("runner.verifier.subprocess.run", side_effect=fake_codex):
                run_verifier(root, "run-001", 1)
            self.assertEqual(
                (working / "SYSTEM.md").read_text(encoding="utf-8"),
                "revised improvement\n",
            )


if __name__ == "__main__":
    unittest.main()
