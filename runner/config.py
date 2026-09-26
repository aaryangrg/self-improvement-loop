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
    environment: str = "development"
    agent: str = "agent"
