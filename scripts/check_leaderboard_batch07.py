"""排行榜第七批驗收：帳戶路由唯讀與第五／六批相容測試。"""
from __future__ import annotations
import subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FILES=['tests.test_leaderboard_batch07_accounts','tests.test_leaderboard_batch07_readiness',
       'tests.test_leaderboard_batch07_recipient','tests.test_leaderboard_batch07_preview',
       'tests.test_leaderboard_batch07_no_mutation',
       'tests.test_leaderboard_batch06_identity','tests.test_leaderboard_batch06_recipient',
       'tests.test_leaderboard_batch05_policy']
if __name__=='__main__':
 raise SystemExit(subprocess.call([sys.executable,'-m','unittest','-v',*FILES],cwd=ROOT))
