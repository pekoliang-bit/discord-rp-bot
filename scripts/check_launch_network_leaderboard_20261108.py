
"""開群、排行榜、網路帳號：不使用正式 Token 或資料庫的離線回歸檢查。"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main():
    errors=[];total=0
    for file in ROOT.rglob('*.py'):
        try:
            ast.parse(file.read_text(encoding='utf-8-sig'), filename=str(file))
            total += 1
        except Exception as exc:
            errors.append(f'{file.relative_to(ROOT)}: {exc}')
    if errors:
        print('Syntax errors:\n'+'\n'.join(errors))
        return 1
    print(f'Python parse: {total} files OK')
    names=[
        'tests.test_launch_network_leaderboard_20261108',
        'tests.test_network_groups',
        'tests.test_network_account_backfill',
        'tests.test_leaderboard_batch05_policy',
        'tests.test_leaderboard_batch06_identity',
        'tests.test_leaderboard_batch06_readiness',
        'tests.test_leaderboard_batch07_accounts',
        'tests.test_leaderboard_batch07_readiness',
        'tests.test_company_view_leaderboard_4pages_20261108',
    ]
    result=subprocess.run([sys.executable,'-m','unittest',*names,'-q'],cwd=ROOT,check=False)
    return result.returncode


if __name__=='__main__':
    raise SystemExit(main())
