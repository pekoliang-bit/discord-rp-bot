"""標籤 V2.1 核准版離線驗收：語法與創角、標籤 DB/效果合約。"""
from __future__ import annotations
import ast
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def main() -> int:
    paths=list(ROOT.rglob('*.py'))
    for p in paths:
        ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p))
    print(f'PASS syntax: {len(paths)} Python files')
    modules=[
        'tests.test_impression_tag_v21_20261107',
        'tests.test_tag_effects_approved_20261107',
        'tests.test_creation_full_contract_20261107',
        'tests.test_creation_db_integration_20261107',
    ]
    result=subprocess.run([sys.executable,'-m','unittest',*modules,'-q'],cwd=ROOT,check=False)
    return result.returncode
if __name__=='__main__':raise SystemExit(main())
