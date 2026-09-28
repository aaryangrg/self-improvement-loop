"""Read local self-improvement results for the monitoring UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .self_improvement import validate_run_id


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def list_runs(repo_root: Path) -> list[str]:
    root = repo_root / "results" / "self-improvement"
    if not root.exists():
        return []
    runs = [path for path in root.iterdir() if path.is_dir() and (path / "metadata").exists()]
    return [path.name for path in sorted(runs, key=lambda path: path.stat().st_mtime, reverse=True)]


def load_run(repo_root: Path, run_id: str) -> dict[str, Any]:
    validate_run_id(run_id)
    root = repo_root / "results" / "self-improvement" / run_id
    if not root.is_dir():
        raise FileNotFoundError(root)
    metrics: list[dict[str, Any]] = []
    path = root / "metadata" / "metrics.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                metrics.append(row)

    splits: list[dict[str, Any]] = []
    for kind, folder in (("train", "epochs"), ("test", "test")):
        for epoch_dir in sorted((root / folder).glob("epoch-[0-9][0-9][0-9]")):
            progress = read_json(epoch_dir / "progress.json")
            aggregate = read_json(epoch_dir / "aggregate.json")
            if not progress and not aggregate:
                continue
            trials = progress.get("trials", [])
            if not trials:
                trials = _trials_from_summaries(epoch_dir)
            splits.append(
                {
                    "kind": kind,
                    "epoch": int(epoch_dir.name.removeprefix("epoch-")),
                    "status": progress.get("status") or ("completed" if aggregate else "running"),
                    "trials": trials,
                    "aggregate": aggregate,
                    "generation_cost_usd": _generation_cost(epoch_dir),
                    "judge_cost_usd": _judge_cost(epoch_dir),
                }
            )

    return {
        "run_id": run_id,
        "root": root,
        "metadata": read_json(root / "metadata" / "run.json"),
        "progress": read_json(root / "metadata" / "progress.json"),
        "metrics": metrics,
        "splits": splits,
        "timeline": _timeline(root),
    }


def _trials_from_summaries(epoch_dir: Path) -> list[dict[str, Any]]:
    trials = []
    for path in epoch_dir.glob("*/trial-*/trial_summary.json"):
        summary = read_json(path)
        trials.append(
            {
                "task_id": summary.get("task_id", path.parent.parent.name),
                "trial": summary.get("trial", 1),
                "status": summary.get("status", "unknown"),
            }
        )
    return trials


def _generation_cost(epoch_dir: Path) -> float:
    total = 0.0
    for path in epoch_dir.glob("*/trial-*/conversation.json"):
        cost = read_json(path).get("conversation", {}).get("cost", {}).get("usd")
        if isinstance(cost, (float, int)):
            total += float(cost)
    return total


def _judge_cost(epoch_dir: Path) -> float | None:
    paths = list(epoch_dir.glob("*/trial-*/evaluation/judge_usage.json"))
    if not paths:
        return None
    total = 0.0
    for path in paths:
        for usage in read_json(path).get("responses", []):
            if usage.get("model") != "gpt-6-sol":
                continue
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
            details = usage.get("input_tokens_details") or {}
            cached_tokens = int(details.get("cached_tokens", 0))
            write_tokens = int(details.get("cache_write_tokens", 0))
            long_context = input_tokens > 272_000
            total += (
                (input_tokens - cached_tokens - write_tokens) * (4.0 if long_context else 2.0)
                + cached_tokens * (0.4 if long_context else 0.2)
                + write_tokens * (5.0 if long_context else 2.5)
                + output_tokens * (15.0 if long_context else 10.0)
            ) / 1_000_000
    return total


def _timeline(root: Path) -> list[dict[str, Any]]:
    items = []
    for path in sorted((root / "metadata" / "researcher").glob("epoch-*/**/*_result.json")):
        if "sandbox" in path.parts or "workspace" in path.parts:
            continue
        result = read_json(path)
        if not result:
            continue
        events = path.with_name("events.jsonl")
        items.append(
            {
                "epoch": (
                    path.parts[-4] if path.parent.name.startswith("attempt-") else path.parent.name
                ),
                "role": "verifier" if path.name.startswith("verifier") else "researcher",
                "result": result,
                "usage": _codex_usage(events),
                "path": str(path.relative_to(root)),
            }
        )
    return items


def _codex_usage(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    usage: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") != "turn.completed":
            continue
        payload = event.get("usage", {})
        if isinstance(payload, dict):
            usage = {key: int(value) for key, value in payload.items() if isinstance(value, int)}
    return usage
