from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ProbeFailure(RuntimeError):
    """保存 probe 最後一次 report"""

    label: str
    report: dict

    def __str__(self) -> str:
        failure = self.report.get("failure") or {}
        category = failure.get("category", "unknown")
        reason = failure.get("reason") or self.report.get("error") or "No probe report was produced"
        return f"{self.label} failed [{category}]: {reason}"


def run_probe(command: list[str], result_dir: Path, label: str, mode: str, url: str = "", attempts: int = 3) -> dict:
    """執行 application probe, 保存每次 JSON 和 log"""
    result_dir.mkdir(parents=True, exist_ok=True)
    last_report = {}
    for attempt in range(1, attempts + 1):
        environment = {**os.environ, "MOCHISTAR_SYSTEM_TEST": mode, "MOCHISTAR_SYSTEM_TEST_URL": url}
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=environment, check=False)
        (result_dir / f"{label}-{attempt}.json").write_text(completed.stdout, encoding="utf-8")
        (result_dir / f"{label}-{attempt}.log").write_text(completed.stderr, encoding="utf-8")
        try:
            last_report = json.loads(completed.stdout)
        except json.JSONDecodeError:
            last_report = {"error": f"Probe exited with code {completed.returncode} and invalid JSON output"}
        if completed.returncode == 0 and last_report.get("success"):
            (result_dir / f"{label}.json").write_text(completed.stdout, encoding="utf-8")
            return last_report
        if attempt < attempts: time.sleep(attempt * 5)

    for attempt in range(1, attempts + 1):
        print(f"::group::{label} attempt {attempt}")
        print((result_dir / f"{label}-{attempt}.log").read_text(encoding="utf-8"), end="")
        print("::endgroup::")
    raise ProbeFailure(label, last_report)
