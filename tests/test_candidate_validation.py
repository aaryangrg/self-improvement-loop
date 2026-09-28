from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runner.candidate_validation import check_candidate, smoke_candidate


class CandidateValidationTest(unittest.TestCase):
    def test_check_uses_disposable_manifest_pointing_at_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "package.json").write_text(
                '{"name":"agent","pi":{"agents":["./agents/*.yaml"]}}'
            )
            (candidate / "agents").mkdir()
            (candidate / "agents" / "agent.yaml").write_text(
                "name: agent\ntools: [bash]\nskills: []\n"
            )
            trusted = root / "trusted"
            (trusted / "agents").mkdir(parents=True)
            (trusted / "package.json").write_bytes((candidate / "package.json").read_bytes())
            (trusted / "agents" / "agent.yaml").write_text(
                "name: agent\nai:\n  model: anthropic/claude-sonnet-4-6\n"
                "tools: [bash]\nskills: []\n"
            )
            (candidate / "python" / ".venv").mkdir(parents=True)
            (candidate / "python" / ".venv" / "generated.txt").write_text("ignored")
            manifest_source = root / "real-manifest.yaml"
            manifest_source.write_text(
                "name: real-agent\npath: recipes/working\nruntime:\n  llm_mode: managed\n"
            )
            record = root / "check.json"

            def run(command: list[str], **kwargs: object) -> object:
                work_dir = Path(command[command.index("--work-dir") + 1])
                self.assertEqual(
                    (work_dir / "recipe" / "package.json").read_bytes(),
                    (candidate / "package.json").read_bytes(),
                )
                self.assertFalse((work_dir / "recipe" / "python" / ".venv").exists())
                self.assertIn(
                    "model: anthropic/claude-sonnet-4-6",
                    (work_dir / "recipe" / "agents" / "agent.yaml").read_text(),
                )
                manifest = work_dir / ".introspection" / "candidate.yaml"
                self.assertIn("path: recipe", manifest.read_text())
                self.assertIn("name: real-agent", manifest.read_text())
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with patch("runner.candidate_validation.subprocess.run", side_effect=run):
                check_candidate(candidate, trusted, manifest_source, record)
            self.assertEqual(json.loads(record.read_text())["status"], "passed")
            self.assertNotIn("ai:", (candidate / "agents" / "agent.yaml").read_text())

    def test_smoke_requires_successful_agent_span(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            class Client:
                def create_task(self, **kwargs: object) -> object:
                    self.assertEqual(kwargs["runtime_id"], "version-1")
                    return type("Handle", (), {"task_id": "task-1", "run_id": "run-1"})()

                def stream_until_finished(self, task_id: str, run_id: str) -> None:
                    return None

                def get_conversation(self, task_id: str) -> object:
                    return {
                        "items": [
                            {
                                "name": "invoke_agent agent",
                                "status": {"code": "error", "message": "extension startup failed"},
                            }
                        ]
                    }

                def get_task(self, task_id: str) -> object:
                    return {"id": task_id}

            client = Client()
            client.assertEqual = self.assertEqual  # type: ignore[attr-defined]
            output_dir = root / "smoke"
            with self.assertRaisesRegex(RuntimeError, "extension startup failed"):
                smoke_candidate(client, "version-1", "agent", output_dir)
            self.assertTrue((output_dir / "conversation.json").is_file())
            self.assertEqual(
                json.loads((output_dir / "smoke.json").read_text())["status"], "failed"
            )
