"""Read-only, offline regression tests for leaderboard payout release guards."""
from __future__ import annotations
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = [
    "tests/test_leaderboard_batch05_policy.py",
    "tests/test_leaderboard_batch05_preview.py",
    "tests/test_leaderboard_batch05_audit.py",
]

def main():
    cmd = [sys.executable, "-m", "pytest", "-q", *TESTS]
    return subprocess.call(cmd, cwd=ROOT)

if __name__ == "__main__":
    raise SystemExit(main())
