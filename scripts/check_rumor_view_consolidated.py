"""角色查看 + 傳聞選單／授權的指定離線回歸。"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    names=[
        'tests.test_rumor_view_consolidated_20261107',
        'tests.test_character_view_complete_20261107',
        'tests.test_rumor_integration_regression',
    ]
    result=subprocess.run([sys.executable,'-m','unittest',*names,'-v'],cwd=ROOT,check=False)
    return result.returncode

if __name__=='__main__':
    raise SystemExit(main())
