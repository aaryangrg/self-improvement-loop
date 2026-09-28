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
        results = root / "results" / "self-improvement" / "run-001"
        (results / "metadata").mkdir(parents=True)
        (results / "metadata" / "run.json").write_text(
            json.dumps({"config": {"verifier": {"model": "gpt-6-sol"}}}), encoding="utf-8"
        )
        for relative in ("epochs", "workspace", "references/baseline_recipe"):
            (results / relative).mkdir(parents=True)
        candidate_dir = results / "metadata" / "researcher" / "epoch-001"
        candidate = candidate_dir / "sandbox" / "recipe"
        candidate.mkdir(parents=True)
        (candidate / "SYSTEM.md").write_text("general improvement\n", encoding="utf-8")
        (candidate / "helper.py").write_text("print('hello')\n", encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
