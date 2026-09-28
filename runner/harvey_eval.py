"""Run Harvey's evaluator with GPT-6 Sol request compatibility."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from threading import Lock
from typing import Any, cast

from lab_core.evaluation import run_eval  # type: ignore[import-not-found]
from lab_core.evaluation.judge import _VERDICT_SCHEMA, Judge  # type: ignore[import-not-found]


class CompatibleJudge(Judge):  # type: ignore[misc]
    usage_records: list[dict[str, Any]] = []
    usage_lock = Lock()

    def _evaluate_openai(self, prompt: str, temperature: float, _retries: int) -> dict[str, Any]:
        if self.model != "gpt-6-sol":
            return cast(dict[str, Any], super()._evaluate_openai(prompt, temperature, _retries))

        last_error: Exception | None = None
        for attempt in range(_retries):
            kwargs = {
                "model": self.model,
                "input": prompt,
                "max_output_tokens": 16384,
                "reasoning": {"effort": os.environ.get("HARVEY_JUDGE_REASONING_EFFORT", "low")},
            }
            if attempt < _retries - 1:
                kwargs["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "verdict",
                        "schema": _VERDICT_SCHEMA,
                        "strict": True,
                    }
                }
            try:
                response = self.client.responses.create(**kwargs)
            except Exception as error:
                last_error = error
                continue
            usage = getattr(response, "usage", None)
            if usage is not None:
                record = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
                record["model"] = self.model
                with self.usage_lock:
                    self.usage_records.append(record)
            try:
                return cast(dict[str, Any], self._parse_json(response.output_text or ""))
            except (ValueError, json.JSONDecodeError) as error:
                last_error = error

        raise ValueError(
            f"Judge returned unparseable response after {_retries} attempts: {last_error}"
        )


def main() -> None:
    run_eval.Judge = CompatibleJudge
    run_eval.main()
    if "--run-id" in sys.argv:
        run_id = sys.argv[sys.argv.index("--run-id") + 1]
        path = Path(run_eval.RESULTS_DIR) / run_id / "judge_usage.json"
        path.write_text(
            json.dumps(
                {
                    "reasoning_effort": os.environ.get("HARVEY_JUDGE_REASONING_EFFORT", "low"),
                    "responses": CompatibleJudge.usage_records,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
