from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .self_improvement import SelfImprovementConfig


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _rate(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return f"{value:.1%}" if isinstance(value, (int, float)) else "—"


def render_pr_body(config: SelfImprovementConfig, run_id: str, results_dir: Path) -> str:
    saved_config = _read_json(results_dir / "metadata" / "run.json").get("config", {})
    split_config = (
        saved_config.get("split_config", config.split_config)
        if isinstance(saved_config, dict)
        else config.split_config
    )
    metrics_path = results_dir / "metadata" / "metrics.jsonl"
    rows = (
        [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").splitlines() if line]
        if metrics_path.is_file()
        else []
    )
    scored_epochs = sum(int(row["epoch"]) > 0 for row in rows)
    scored_epoch_ids = {int(row["epoch"]) for row in rows}
    cadence = (
        "every epoch" if config.loop.test_every == 1 else f"every {config.loop.test_every} epochs"
    )
    lines = [
        "## Experiment",
        "",
        (
            f"Run `{run_id}` starts from `{config.seed_recipe}` and uses "
            f"`{split_config}`. The researcher sees training evidence; "
            "held-out scores are reported here for human review."
        ),
        "",
        f"**Progress:** {scored_epochs}/{config.loop.max_epochs} research epochs scored "
        f"(test cadence: {cadence}, plus baseline and final).",
        "",
        "## Scores",
        "",
        "| Epoch | Train tasks | Train rubrics | Train scored | "
        "Test tasks | Test rubrics | Test scored |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        epoch = int(row["epoch"])
        label = "0 (baseline)" if epoch == 0 else str(epoch)
        train_scored = (
            f"{row.get('train_number_of_evaluated_tasks', 0)}/{row.get('train_number_of_tasks', 0)}"
        )
        test_scored = (
            f"{row['test_number_of_evaluated_tasks']}/{row['test_number_of_tasks']}"
            if "test_number_of_tasks" in row
            else "—"
        )
        lines.append(
            f"| {label} | {_rate(row, 'train_task_pass_rate')} | "
            f"{_rate(row, 'train_rubric_pass_rate')} | {train_scored} | "
            f"{_rate(row, 'test_task_pass_rate')} | "
            f"{_rate(row, 'test_rubric_pass_rate')} | {test_scored} |"
        )
    if not rows:
        lines.append("| Pending baseline | — | — | — | — | — | — |")

    lines.extend(["", "## Research history", ""])
    research_root = results_dir / "metadata" / "researcher"
    found = False
    for epoch_dir in sorted(research_root.glob("epoch-*")):
        result = _read_json(epoch_dir / "researcher_result.json")
        if not result:
            continue
        found = True
        epoch = int(epoch_dir.name.removeprefix("epoch-"))
        candidate = _read_json(epoch_dir / "candidate.json")
        reviews = sorted((epoch_dir / "verifier").glob("attempt-*/verifier_result.json"))
        review = _read_json(reviews[-1]) if reviews else {}
        commit = candidate.get("commit")
        suffix = (
            f" · commit `{commit[:7]}`"
            if isinstance(commit, str)
            else " · scored" if epoch in scored_epoch_ids else " · scoring pending"
        )
        lines.extend([f"### Epoch {epoch}{suffix}", ""])
        if result.get("hypothesis"):
            lines.extend([f"**Hypothesis:** {result['hypothesis']}", ""])
        for change in result.get("changes_made", []):
            lines.append(f"- {change}")
        if result.get("changes_made"):
            lines.append("")
        if review.get("verdict"):
            lines.extend([f"**Verifier:** {review['verdict']}", ""])
    if not found:
        lines.extend(["First candidate pending.", ""])

    lines.extend(
        [
            "## Review notes",
            "",
            "This draft PR pins Introspection staging builds to candidate commits. "
            "Scores can change as later epochs complete; task-level evidence, conversations, "
            "and costs remain in the local run dashboard. Review coverage and held-out "
            "transfer before treating a candidate as a performance improvement.",
            "",
        ]
    )
    return "\n".join(lines)
