"""第六批回歸：可執行 python scripts/check_leaderboard_batch06.py。"""
from pathlib import Path
import subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
CASES=[f'tests/test_leaderboard_batch06_{part}.py' for part in ('identity','snapshot','recipient','readiness')]
CASES+=['tests/test_leaderboard_batch05_policy.py','tests/test_leaderboard_batch05_preview.py','tests/test_leaderboard_batch05_audit.py']
if __name__=='__main__':
    raise SystemExit(subprocess.call([sys.executable,'-m','pytest','-q',*CASES],cwd=ROOT))
