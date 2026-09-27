from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunIncidents:
    cli_retries: int = 0
    stream_reattaches: int = 0
    download_retries: int = 0
    platform_failures: list[str] = field(default_factory=list)

    def record_platform_failure(self, message: str) -> None:
        self.platform_failures.append(message[:1000])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_file(cls, path: Path) -> RunIncidents:
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            cli_retries=int(payload.get("cli_retries", 0)),
            stream_reattaches=int(payload.get("stream_reattaches", 0)),
            download_retries=int(payload.get("download_retries", 0)),
            platform_failures=list(payload.get("platform_failures", [])),
        )


def is_probably_transient(message: str) -> bool:
    lowered = message.lower()
    markers = (
        "500",
        "502",
        "503",
        "504",
        "bad gateway",
        "connection reset",
        "econnreset",
        "etimedout",
        "gateway timeout",
        "internal server error",
        "rate limit",
        "timeout",
        "too many requests",
        "temporarily unavailable",
    )
    return any(marker in lowered for marker in markers)
