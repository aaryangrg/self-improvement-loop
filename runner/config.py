from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunnerConfig:
    repo_root: Path = Path(".")
    harvey_repo: Path = Path("harvey-labs")
    results_root: Path = Path("results/tasks")
    upload_cache_path: Path = Path("results/cache/introspection-files.json")
    runtime_name: str = "legal-agent"
    runtime_id: str | None = None
    environment: str = "staging"
    agent: str = "agent"
    enable_eval: bool = True
    eval_judges: tuple[str, ...] = ("gpt-4.1",)
    eval_parallel: int = 2
    eval_reasoning_effort: str = "low"
    cli_retries: int = 2
    download_retries: int = 2
    stream_reattaches: int = 2
    retry_backoff_seconds: float = 2.0
