
"""驗收腳本在正式結算開啟時必須阻擋。"""

import os
import subprocess
import sys
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SCRIPT = (
    ROOT
    / "scripts"
    / "verify_pr_release.py"
)


class ReleaseGuard(unittest.TestCase):

    def test_script_exists(self):
        self.assertTrue(
            SCRIPT.is_file(),
            f"缺少 {SCRIPT}",
        )

    def test_enabled_must_block(self):
        env = {
            **os.environ,
            "RUMOR_FINALIZATION_ENABLED": "1",
        }

        run = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(
            run.returncode,
            2,
            run.stdout + run.stderr,
        )

        self.assertIn(
            "BLOCKED",
            run.stdout,
        )


if __name__ == "__main__":
    unittest.main()
