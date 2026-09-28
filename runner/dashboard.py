"""Launch the local read-only monitoring dashboard."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def start_dashboard(repo_root: Path, run_id: str | None = None) -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["SELF_IMPROVEMENT_REPO_ROOT"] = str(repo_root.resolve())
    if run_id:
        env["SELF_IMPROVEMENT_RUN_ID"] = run_id
    log_dir = (
        repo_root / "results" / "self-improvement" / run_id / "metadata"
        if run_id
        else repo_root / "results"
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "dashboard.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(repo_root / "ui" / "app.py"),
                "--server.headless=true",
                "--server.address=127.0.0.1",
                f"--server.port={port}",
                "--browser.gatherUsageStats=false",
            ],
            cwd=repo_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    for _ in range(30):
        if process.poll() is not None:
            raise RuntimeError(f"dashboard failed to start; see {log_dir / 'dashboard.log'}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return (
                    f"http://127.0.0.1:{port}/?run={run_id}"
                    if run_id
                    else f"http://127.0.0.1:{port}/"
                )
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"dashboard did not become ready; see {log_dir / 'dashboard.log'}")
