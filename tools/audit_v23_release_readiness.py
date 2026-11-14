"""《流光城》版本整合盤點：只讀專案檔案，不連線 Discord、不操作正式資料庫。

Usage: python tools/audit_v23_release_readiness.py [--json PATH] [--strict]
--strict: 如仍有待整合項目，回傳非零退出碼，方便 CI。
"""
from __future__ import annotations
import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = (
    '查看','抽獎','傳聞','創角','登記','公司','官方','劇情','群主指引',
    '商城','申請','審核','娛樂城','惡意','任務','總覽','排行','系統狀態',
    '銀行','陣營','網路','行動','通知','資產',
)


def _source(relative):
    path = ROOT / relative
    return path.read_text(encoding='utf-8') if path.is_file() else ''


def _table(relative):
    path = ROOT / relative
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeError, json.JSONDecodeError):
        return None


def _ast(relative):
    try:
        return ast.parse(_source(relative))
    except (SyntaxError, ValueError):
        return None


def _command_catalog():
    tree = _ast('modules/overview_catalog.py')
    systems = None
    if tree:
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SYSTEMS' for t in node.targets):
                try:
                    systems = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
                break
    actual = [cmd for commands in systems.values() for cmd in commands] if isinstance(systems, dict) else []
    return {'expected': len(TOP_LEVEL), 'listed': len(actual),
            'missing': sorted(set(TOP_LEVEL) - set(actual)),
            'extra': sorted(set(actual) - set(TOP_LEVEL)),
            'duplicates': sorted({cmd for cmd in actual if actual.count(cmd) > 1})}


def _check(name, ok, detail, severity='error'):
    return {'name': name, 'ok': bool(ok), 'detail': detail, 'severity': severity}


def audit():
    checks = []
    catalog = _command_catalog()
    checks.append(_check('總覽24指令', not any(catalog[k] for k in ('missing','extra','duplicates')),
                         catalog))
    npc = _source('modules/npc_history_v23.py')
    checks.append(_check('NPC玩家/官方選單', 'discord.ui.Select' in npc and 'player_npc' in npc and 'official_npc' in npc,
                         '類型必須以 Discord 選單選擇，不再要求輸入英文'))
    history = _table('assets/history_v23/catalog_580_inventory.json')
    if isinstance(history, list):
        total = len(history)
        ready = sum(item.get('import_ready') is True for item in history if isinstance(item,dict))
        candidates = sum(item.get('candidate_available') is True and item.get('import_ready') is not True for item in history if isinstance(item,dict))
        missing = total-ready-candidates
    else:
        total=ready=candidates=missing=0
    checks.append(_check('歷史素材總表', total == 580 and ready == 55,
                         {'total':total,'import_ready':ready,'candidate_only':candidates,'no_full_candidate':missing}))
    tests = (
        'tests/verify_overview_npc_import_20261114.py',
        'tests/verify_history_v23_materials_one_pass.py',
        'tests/verify_history_v23_delivery.py',
        'tests/verify_npc_birthday_activation_v23.py',
    )
    checks.append(_check('既有離線測試檔', all((ROOT/p).is_file() for p in tests),
                         {'missing':[p for p in tests if not (ROOT/p).is_file()]}))
    activation = _source('core/character_activation_v23.py')
    checks.append(_check('角色啟用安全防護',
                         all(s in activation for s in ('allowed_types','normalize_birthday','_npc_attributes_present','issue_in_transaction')),
                         '審核狀態、生日、NPC初始屬性、編號檢查'))
    rewards_integrated = ('grant_initial_npc_resources' in activation and 'grant_initial_npc_resources' in npc)
    checks.append(_check('NPC初始資源完整接入', rewards_integrated,
                         '目前缺少已核准NPC資源正式發放接口；不可推測金錢或行動值', 'pending'))
    # 初始論壇必須由 async Discord 服務建立；僅有通知 queue 不等於論壇已建立。
    profile = _source('modules/profile.py')
    forum_service = _source('modules/character_forums.py')
    forum_hook = 'ensure_forum_after_activation' in profile and 'ensure_forum_after_activation' in npc
    checks.append(_check('啟用時建立角色專屬論壇', forum_hook and 'create_forum' in forum_service,
                         '專屬論壇建立需在Discord互動上下文完成，不能在SQLite交易中直接建立', 'pending'))
    notice = _source('core/history_v23_delivery.py')
    checks.append(_check('五筆歷史通知服務', 'queue_history_notice' in notice and 'queue_history_notice' in activation,
                         '已串接詳細五筆通知；實際送達仍需Discord驗證'))
    payout = _source('modules/leaderboard_payout_policy.py')
    checks.append(_check('排行榜正式發獎開關', 'PAYOUT_ENABLED = True' in payout,
                         '目前關閉；必須先完成收款適配與發獎安全驗證', 'pending'))
    overview_tree = _ast('modules/overview_data.py')
    seed_called = False
    if overview_tree is not None:
        for node in ast.walk(overview_tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'seed' and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == 'tag_library'):
                seed_called = True
    checks.append(_check('總覽標籤查詢', overview_tree is not None and not seed_called,
                         'AST 檢查實際函式呼叫，不會把註解誤認為寫入', 'pending'))
    return {'project':'流光城', 'source_version':'20261114-153103', 'checks':checks,
            'passing':sum(c['ok'] for c in checks),
            'remaining_checks':sum(not c['ok'] for c in checks),
            'materials':{'approved_slots':580,'import_ready':ready,'source_missing':580-ready},
            'note':'本工具只盤點程式接線與檔案；無法代替完整功能或Discord實機驗證。'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json',type=Path,help='另存JSON報告；不寫資料庫')
    parser.add_argument('--strict',action='store_true',help='有未通過項目即退出碼1')
    args=parser.parse_args(argv)
    report=audit()
    for result in report['checks']:
        print(('PASS' if result['ok'] else 'PENDING')+': '+result['name'])
    print(f"核對項目：{len(report['checks'])}，已通過：{report['passing']}，尚未通過：{report['remaining_checks']}")
    print(f"素材原文可入庫：{report['materials']['import_ready']}/580；尚缺：{report['materials']['source_missing']}")
    if args.json:
        args.json.parent.mkdir(parents=True,exist_ok=True)
        args.json.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 1 if args.strict and report['remaining_checks'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
