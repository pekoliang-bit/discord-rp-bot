
"""公關專用的離線驗收入口，不連接 Discord 或正式玩家資料。

使用方法：
python scripts/verify_pr_release.py
"""

from __future__ import annotations

import os
import subprocess
import sys

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

GROUPS = [
    "test_pr_*.py",
    "test_rumor_pr_*.py",
    "test_rumor_due_preview.py",
]


def main() -> int:
    if os.getenv(
        "RUMOR_FINALIZATION_ENABLED",
        "0",
    ) == "1":
        print(
            "BLOCKED: "
            "RUMOR_FINALIZATION_ENABLED=1; "
            "尚未完成結算驗收，請先關閉。"
        )
        return 2

    for pattern in GROUPS:
        print(
            f"\n=== {pattern} ===",
            flush=True,
        )

        cmd = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            pattern,
            "-v",
        ]

        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            env={
                **os.environ,
                "RUMOR_FINALIZATION_ENABLED": "0",
            },
            check=False,
        )

        if completed.returncode:
            print(f"FAILED: {pattern}")
            return completed.returncode

    print(
        "\nPASS: 公關選定單元測試通過；"
        "此結果不代表 Discord 線上驗收通過。"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
