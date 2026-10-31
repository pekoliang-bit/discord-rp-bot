"""娛樂城部署前唯讀驗收工具。

執行方式（專案根目錄）：python scripts/casino_release_check.py
檢查不連 Discord、不連資料庫、不變更玩家資料。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / 'modules'
REQUIRED = [
    'casino_repository.py', 'casino_service.py', 'casino_scratch.py',
    'casino_lottery.py', 'casino_gacha.py', 'casino_games.py',
    'casino_effects.py', 'casino_commands.py', 'casino_shop_bridge.py',
    'casino_gacha_batch.py', 'casino_wager_service.py',
    'casino_notice_dispatch.py', 'casino_risk_bridge.py',
    'casino_private_notice.py', 'casino_risk_executor.py',
    'casino_table_service.py', 'casino_pvp_service.py',
]


def check() -> int:
    issues: list[str] = []
    warnings: list[str] = []
    print(f'[資訊] 專案路徑：{ROOT}')
    if not (ROOT / 'main.py').is_file():
        issues.append('找不到根目錄 main.py')
    main_source = (ROOT / 'main.py').read_text(encoding='utf-8') if (ROOT / 'main.py').exists() else ''
    if "casino_commands" not in main_source and "casino_" not in main_source:
        warnings.append('請確認 main.py 有正確載入 casino_commands')
    for name in REQUIRED:
        path = MOD / name
        if not path.is_file():
            issues.append(f'缺少 modules/{name}')
            continue
        try:
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        except (SyntaxError, UnicodeError) as exc:
            issues.append(f'語法錯誤 modules/{name}：{exc}')
    commands_path = MOD / 'casino_commands.py'
    if commands_path.exists():
        code = commands_path.read_text(encoding='utf-8')
        if code.count("name='娛樂城'") + code.count('name="娛樂城"') != 1:
            issues.append('應恰好宣告一次 /娛樂城')
        if '娛樂城排行' in code and '@app_commands.command' in code:
            warnings.append('確認未額外註冊 /娛樂城排行；正式排行應走 /娛樂城 按鈕')
        if 'casino_table_service' not in code:
            warnings.append('簡易牌桌尚未接入 /娛樂城')
        if 'pvp' in code.lower():
            warnings.append('雙人對賭正式開放前須驗證加入、取消、逾時退款與押金總額')
    if 'glob("*.py")' in main_source and 'casino_' not in main_source:
        issues.append('main.py 可能會將 casino_ 服務檔誤載成 Extension')
    for warning in warnings:
        print('[注意]', warning)
    for issue in issues:
        print('[失敗]', issue)
    if issues:
        print(f'驗收未通過：{len(issues)} 個問題；請勿開放下注。')
        return 1
    print('靜態檔案與語法檢查通過。')
    print('仍須在測試伺服器驗證扣款、賠付、重啟、對賭退款與通知權限。')
    return 0


if __name__ == '__main__':
    sys.exit(check())
