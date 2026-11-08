"""本批 /商城 /網路 系統離線檢查；不連線正式 Discord。"""
from pathlib import Path
import ast
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]

def main():
    count=0
    for p in ROOT.rglob('*.py'):
        ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p))
        count+=1
    print(f'Python syntax: {count} files OK')
    names=['tests.test_live_market_rank_v11_20261108', 'tests.test_live_stream_core',
           'tests.test_live_income_frequency','tests.test_live_expiration_guards',
           'tests.test_live_pk_recovery','tests.test_network_forum_social_20261108',
           'tests.test_network_account_home_recovery_20261108']
    return subprocess.run([sys.executable,'-m','unittest',*names,'-q'],cwd=ROOT,check=False).returncode
if __name__=='__main__':raise SystemExit(main())
