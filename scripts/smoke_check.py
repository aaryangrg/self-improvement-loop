import argparse
from pathlib import Path

from runner.config import RunnerConfig
from runner.harvey import load_task
from runner.paths import task_result_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()

    config = RunnerConfig(repo_root=Path("."))
    task = load_task(config, args.task_id)
    result_dir = task_result_dir(config.results_root, task.task_id, 1)
    print(f"task_id={task.task_id}")
    print(f"instructions_chars={len(task.instructions)}")
    print(f"documents={len(task.documents)}")
    print(f"result_dir={result_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
