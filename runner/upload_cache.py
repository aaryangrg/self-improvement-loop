from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CachedUpload:
    file_id: str
    file_name: str
    size: int
    uploaded_at: str


class UploadCache:
    _lock = threading.Lock()

    def __init__(self, path: Path) -> None:
        self.path = path
        self._files = self._read()

    def get(self, cache_key: str, size: int) -> CachedUpload | None:
        with self._lock:
            self._files = self._read()
        cached = self._files.get(cache_key)
        if cached is None or cached.size != size:
            return None
        return cached

    def put(self, cache_key: str, file_id: str, file_name: str, size: int) -> CachedUpload:
        cached = CachedUpload(
            file_id=file_id,
            file_name=file_name,
            size=size,
            uploaded_at=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            self._files = self._read()
            self._files[cache_key] = cached
            self.write()
        return cached

    def remove(self, cache_key: str) -> None:
        with self._lock:
            self._files = self._read()
            if cache_key in self._files:
                del self._files[cache_key]
                self.write()

    def write(self) -> None:
        payload = {
            "version": 1,
            "files": {key: asdict(value) for key, value in sorted(self._files.items())},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        temp_path.replace(self.path)

    def _read(self) -> dict[str, CachedUpload]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text())
        files = payload.get("files", {})
        if not isinstance(files, dict):
            return {}
        cache: dict[str, CachedUpload] = {}
        for key, value in files.items():
            if isinstance(key, str) and isinstance(value, dict):
                parsed = self._parse_entry(value)
                if parsed is not None:
                    cache[key] = parsed
        return cache

    def _parse_entry(self, value: dict[str, Any]) -> CachedUpload | None:
        try:
            return CachedUpload(
                file_id=str(value["file_id"]),
                file_name=str(value["file_name"]),
                size=int(value["size"]),
                uploaded_at=str(value["uploaded_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
