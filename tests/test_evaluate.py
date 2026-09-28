import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runner.config import RunnerConfig
from runner.evaluate import evaluate_outputs
from runner.harvey import HarveyTask


class EvaluateOutputsTest(unittest.TestCase):
    def test_passes_judge_effort_to_harvey_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outputs = root / "outputs"
            outputs.mkdir()
            (outputs / "memo.txt").write_text("result", encoding="utf-8")
            task = HarveyTask("sample", "", {}, root, [])
            config = RunnerConfig(
                repo_root=root,
                harvey_repo=root / "harvey-labs",
                eval_judges=("gpt-6-sol",),
                eval_reasoning_effort="medium",
            )
            with patch("runner.evaluate.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = ""
                run.return_value.stderr = ""
                evaluate_outputs(task, outputs, root / "trial-001", config)

            command = run.call_args.args[0]
            self.assertIn("runner.harvey_eval", command)
            self.assertEqual(
                run.call_args.kwargs["env"]["HARVEY_JUDGE_REASONING_EFFORT"],
                "medium",
            )


if __name__ == "__main__":
    unittest.main()
