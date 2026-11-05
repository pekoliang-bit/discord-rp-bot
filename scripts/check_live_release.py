"""Offline /網路直播發布前檢查：不連 Discord，不碰正式資料庫。

Run: python scripts/check_live_release.py
Checks full flow, PK/commerce isolation and SQLite unit regressions.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    ('官方商城直播實際結算', [sys.executable, 'scripts/check_live_final.py']),
    ('PK、商品授權隔離驗證', [sys.executable, 'scripts/check_live_pk_commerce.py']),
    ('直播核心、時效、收入、邀請回歸', [sys.executable, '-m', 'unittest',
        'tests.test_live_stream_core', 'tests.test_live_expiration_guards',
        'tests.test_live_income_frequency', 'tests.test_live_pk_recovery', '-v']),
)


def run():
    passed = 0
    for label, cmd in COMMANDS:
        print(f'\n===== {label} =====', flush=True)
        result = subprocess.run(cmd, cwd=ROOT, check=False)
        if result.returncode:
            print(f'\nFAIL: {label}（結束代碼：{result.returncode}）', flush=True)
            return result.returncode
        passed += 1
    print(f'\nPASS: 全部 {passed} 組離線直播發布檢查通過。', flush=True)
    print('注意：仍需於 Discord/Railway 驗證 UI、權限和真實事件。', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
