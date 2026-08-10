from __future__ import annotations

import argparse
import sys
from pathlib import Path

from local_media_server import start_local_media_server
from probe_runner import ProbeFailure, run_probe


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MochiStar probes from source")
    parser.add_argument("--media-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    command = [sys.executable, str(ROOT / "src" / "main.py")]
    failures = []

    try:
        run_probe(command, arguments.output, "update", "update")
    except ProbeFailure as error:
        print(f"::error::{error}")
        failures.append(str(error))

    server, thread, local_url = start_local_media_server()
    try:
        try:
            run_probe(command, arguments.output, "media-local", "media-analysis", local_url)
        except ProbeFailure as error:
            print(f"::error::{error}")
            failures.append(str(error))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    try:
        run_probe(command, arguments.output, "media-external", "media-analysis", arguments.media_url)
    except ProbeFailure as error:
        print(f"::error::{error}")
        failures.append(str(error))

    if failures:
        print("Source probes failed: " + "; ".join(failures))
        return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
