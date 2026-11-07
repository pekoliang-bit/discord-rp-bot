
"""Repeatable offline release gate; no Discord connection required."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHECKS = (
    ('960 comments and installation', ('scripts/check_project_installation.py',)),
    ('Release readiness', ('scripts/check_release_readiness.py',)),
    ('Leaderboard policy and recipients', ('scripts/check_leaderboard_batch07.py',)),
    ('Company regression', ('scripts/check_company_release.py',)),
    ('Action and hospital regression', ('scripts/check_action_execution_release.py',)),
    ('Live / PK / commerce regression', ('scripts/check_live_release.py',)),
    ('Consolidation contracts', ('-m', 'unittest', 'tests.test_one_shot_release_consistency_20261107', '-v')),
)


def main():
    for name, args in CHECKS:
        print(f'\n=== {name} ===', flush=True)
        result = subprocess.run((sys.executable, *args), cwd=ROOT, check=False)
        if result.returncode != 0:
            print(f'FAILED: {name} (exit {result.returncode})', flush=True)
            return result.returncode
    print('\nPASS: All selected offline release gates; Discord and Railway remain untested.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
