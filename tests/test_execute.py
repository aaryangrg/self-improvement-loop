from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runner.config import RunnerConfig
from runner.execute import run_one
from runner.harvey import HarveyTask
from runner.introspection import TaskHandle


class AgentExecutionFailureTests(unittest.TestCase):
    def test_failed_root_agent_span_is_saved_and_not_evaluated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = HarveyTask(
                task_id="example/task",
                instructions="Do the task",
                deliverables={},
                document_root=root,
                documents=[],
            )
            conversation = {
                "items": [
                    {
                        "name": "invoke_agent agent",
                        "status": {"code": "Error", "message": "Model stream idle"},
                    }
                ]
            }
            with (
                patch("runner.execute.load_task", return_value=task),
                patch("runner.execute.IntrospectionClient") as client_type,
                patch("runner.execute.evaluate_outputs") as evaluate,
            ):
                client = client_type.return_value
                client.create_task.return_value = TaskHandle("task-id", "run-id")
                client.get_conversation.return_value = conversation
                client.get_task.return_value = {"id": "task-id", "files": []}

                with self.assertRaisesRegex(RuntimeError, "Model stream idle"):
                    run_one(
                        "example/task",
                        1,
                        RunnerConfig(repo_root=root, results_root=root / "results" / "tasks"),
                    )

            evaluate.assert_not_called()
            trial_dir = root / "results" / "tasks" / "example__task" / "trial-001"
            self.assertEqual(
                json.loads((trial_dir / "conversation.json").read_text(encoding="utf-8")),
                conversation,
            )
            self.assertEqual(
                json.loads((trial_dir / "execution_error.json").read_text(encoding="utf-8"))[
                    "message"
                ],
                "Model stream idle",
            )


if __name__ == "__main__":
    unittest.main()
