"""/網路 入口、論壇、直播核心測試；不使用正式 Discord Token。"""
from __future__ import annotations
import ast
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    total=0
    for path in ROOT.rglob('*.py'):
        ast.parse(path.read_text(encoding='utf-8-sig'),filename=str(path))
        total+=1
    print(f'Python 語法檢查：{total} 檔通過')
    suites=[
        'tests.test_network_forum_social_20261108',
        'tests.test_network_account_home_recovery_20261108',
        'tests.test_network_account_backfill',
        'tests.test_network_groups',
        'tests.test_live_stream_core',
        'tests.test_live_income_frequency',
        'tests.test_live_pk_recovery',
    ]
    return subprocess.run([sys.executable,'-m','unittest',*suites,'-q'],cwd=ROOT,check=False).returncode

if __name__=='__main__':
    raise SystemExit(main())
