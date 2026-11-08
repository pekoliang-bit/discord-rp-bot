"""四項玩家功能部署前離線驗收。

用法：python scripts/check_four_categories_preflight_20261108.py
全部在隔離的 Python 子行程執行；不使用 Discord Token，也不修改正式資料庫。
測試通過不代表場地內容已核准、Discord 權限正確或 Railway 已實機通過。
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SUITES = {
    '公司／銀行核心': ['tests/test_company*'],
    '傳聞交易／界面': [
        'tests/test_rumor_auto_delivery.py',
        'tests/test_rumor_auto_linked_cases.py',
        'tests/test_rumor_due_preview.py',
        'tests/test_rumor_pr_integrity_audit.py',
        'tests/test_rumor_reward_sqlite.py',
        'tests/test_rumor_view_consolidated_20261107.py',
    ],
    '行動／醫療規則': [
        'tests/test_action*',
        'tests/test_location_action_execution.py',
        'tests/test_medical_service_rules.py',
    ],
    '網路／論壇／直播交易': [
        'tests/test_network*',
        'tests/test_live_market_rank_v11_20261108.py',
    ],
    '跨系統入口契約': ['tests/test_four_category_preflight_20261108.py'],
}


def main() -> int:
    sources = list(ROOT.rglob('*.py'))
    try:
        for source in sources:
            ast.parse(source.read_text(encoding='utf-8-sig'), filename=str(source))
    except (SyntaxError, UnicodeError, OSError) as exc:
        print(f'❌ Python 語法：{exc}', flush=True)
        return 1
    print(f'✅ Python 語法：{len(sources)} 個檔案', flush=True)
    failed = []
    for label, patterns in SUITES.items():
        tests = []
        for pattern in patterns:
            tests.extend(sorted(ROOT.glob(pattern)))
        if not tests:
            print(f'❌ {label}：缺少測試檔', flush=True)
            failed.append(label)
            continue
        command = [sys.executable, '-m', 'pytest', '-q',
                   *(str(p.relative_to(ROOT)) for p in dict.fromkeys(tests)),
                   '--disable-warnings', '--tb=short']
        # 獨立執行，避免舊測試全域 sys.modules 注入污染另一分類。
        try:
            result = subprocess.run(command, cwd=ROOT, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=50, check=False)
        except subprocess.TimeoutExpired:
            print(f'❌ {label}：超時（50 秒）', flush=True)
            failed.append(label)
            continue
        outcome = '✅' if result.returncode == 0 else '❌'
        print(f'{outcome} {label}\n{result.stdout[-2200:]}', flush=True)
        if result.returncode:
            failed.append(label)

    # Reporting approval status is read-only. No auto-approval of pending action JSON.
    pending = []
    for path in sorted((ROOT / 'assets/action_locations').glob('*pending.json')):
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        for location in data.get('locations', []):
            for record in location.get('actions', []):
                if isinstance(record, (dict, list)) and (not isinstance(record, dict) or record.get('approved') is not True):
                    pending.append((path.name, location.get('name', '')))
    print(f'ℹ️ 待核准的原始行動紀錄：{len(pending)} 件；本工具不會自動批准。', flush=True)
    print('ℹ️ Discord 導入前需檢查：群主與機器人權限、資料庫持久化、論壇頻道、'
          'RP_PROXY_MESSAGE_CONTENT_ENABLED 以及 Discord Developer Portal Message Content Intent。', flush=True)
    if failed:
        print('❌ 離線檢查未通過：' + '、'.join(failed), flush=True)
        return 1
    print('✅ 指定離線驗收通過；待核准的行動內容及實機互動仍不視為通過。', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
