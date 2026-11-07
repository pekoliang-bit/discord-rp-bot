"""不啟動 Discord、不觸碰正式資料庫的／行動點數與醫院離線回歸檢查。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    'tests.test_action_professional_points',
    'tests.test_location_action_execution',
    'tests.test_action_medical_defaults',
    'tests.test_medical_service_rules',
)


def main():
    result = subprocess.run([sys.executable, '-m', 'unittest', *TESTS, '-v'], cwd=ROOT)
    if result.returncode:
        return result.returncode
    print('通過：專業點數正式分配、行動交易與醫院安全規則（仍待 Discord 實機驗證）。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
