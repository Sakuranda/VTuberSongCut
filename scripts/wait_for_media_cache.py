#!/usr/bin/env python3
"""Reuse an in-flight Windows media build instead of starting it from scratch."""
import argparse
import json
import os
import subprocess
import time

PREPARE = "Validate complete media/source pair and preserve logs for caching"
SAVE = "Save completed media build before application packaging"


def cache_state(jobs):
    windows = next((job for job in jobs if "Windows-x64" in job["name"]), None)
    if windows is None:
        return "waiting"
    steps = {step["name"]: step for step in windows.get("steps", [])}
    if steps.get(PREPARE, {}).get("conclusion") == "success" and steps.get(SAVE, {}).get("conclusion") in {"success", "skipped"}:
        return "ready"
    if windows["status"] == "completed":
        return "failed"
    return "waiting"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    if not args.run.isdecimal():
        raise SystemExit("A numeric source-build run ID is required")
    repo = os.environ["GH_REPO"]
    deadline = time.monotonic() + args.timeout
    failures = 0
    print(f"Waiting for verified Windows media/source cache from run {args.run}; other platforms continue in parallel", flush=True)
    while time.monotonic() < deadline:
        try:
            raw = subprocess.check_output(["gh", "api", f"repos/{repo}/actions/runs/{args.run}/jobs"], text=True, encoding="utf-8", timeout=45)
            state = cache_state(json.loads(raw)["jobs"])
            failures = 0
        except (subprocess.SubprocessError, ValueError):
            failures += 1
            if failures >= 5:
                raise SystemExit("Cannot inspect the prior media cache; refusing to start another cold Windows build")
            time.sleep(20)
            continue
        if state == "ready":
            print("Verified media/source cache is available; proceeding to restore it", flush=True)
            return
        if state == "failed":
            raise SystemExit("Prior Windows job finished before saving a verified media/source cache")
        time.sleep(30)
    raise SystemExit("Timed out waiting for the existing media build; no duplicate compilation was started")


if __name__ == "__main__":
    main()
