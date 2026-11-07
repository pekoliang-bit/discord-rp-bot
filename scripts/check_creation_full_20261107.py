"""創角離線合約與 Python 靜態安全檢查；不寫入正式資料庫。"""
from __future__ import annotations
import compileall
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    if not compileall.compile_dir(str(ROOT),quiet=1):
        raise SystemExit('FAIL: Python 語法編譯失敗')
    args=[sys.executable,'-m','unittest','discover','-s','tests',
          '-p','test_creation_full_contract_20261107.py','-v']
    result=subprocess.run(args,cwd=ROOT,check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    integration=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests',
                 '-p','test_creation_db_integration_20261107.py','-v'],cwd=ROOT,check=False)
    raise SystemExit(integration.returncode)
