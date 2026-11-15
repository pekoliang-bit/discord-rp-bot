"""《流光城》V2.3 第五區：24 個指令及跨系統的唯讀發行盤點。

完全不開啟資料庫或 Discord 連線，不修改玩家狀態。
使用：python tools/audit_v23_release_readiness.py [--json reports/readiness.json] [--strict]
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = (
    '查看','抽獎','傳聞','創角','登記','公司','官方','劇情','群主指引',
    '商城','申請','審核','娛樂城','惡意','任務','總覽','排行','系統狀態',
    '銀行','陣營','網路','行動','通知','資產',
)


def source(relative):
    p = ROOT / relative
    return p.read_text(encoding='utf-8') if p.is_file() else ''


def json_file(relative):
    try:
        return json.loads((ROOT / relative).read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        return None


def literal_assignment(relative, name):
    try:
        tree = ast.parse(source(relative))
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                names = ([t.id for t in node.targets if isinstance(t, ast.Name)]
                         if isinstance(node, ast.Assign)
                         else [node.target.id] if isinstance(node.target, ast.Name) else [])
                if name in names:
                    return ast.literal_eval(node.value)
    except (SyntaxError, ValueError, TypeError):
        pass
    return None


def check(name, ok, detail, *, gate=True):
    return {'name': name, 'ok': bool(ok), 'detail': detail, 'release_gate': gate}


def audit():
    checks = []
    registered = literal_assignment('core/official_commands.py', 'OFFICIAL_COMMANDS')
    # 若正式清單是間接建構，保留待確認，絕不憑原始碼字串聲稱已註冊。
    reg = set(registered) if isinstance(registered, (list,tuple,set,frozenset,dict)) else set()
    checks.append(check('24個正式指令白名單', reg == set(OFFICIAL),
                        {'declared': len(reg), 'missing':sorted(set(OFFICIAL)-reg),
                         'unexpected':sorted(reg-set(OFFICIAL))}))
    groups = literal_assignment('modules/overview_catalog.py', 'SYSTEMS')
    listed = [cmd for values in groups.values() for cmd in values] if isinstance(groups,dict) else []
    checks.append(check('總覽導覽完整且不重複', set(listed)==set(OFFICIAL) and len(listed)==len(set(listed)),
                        {'listed':len(listed),'missing':sorted(set(OFFICIAL)-set(listed)),
                         'extra':sorted(set(listed)-set(OFFICIAL))}))
    manifest = json_file('assets/history_v23/current_adopted_580.json')
    summary = {'rows': len(manifest) if isinstance(manifest,list) else 0}
    if isinstance(manifest,list):
        ids = [row.get('asset_id') for row in manifest if isinstance(row,dict)]
        prefixes = {k:sum(isinstance(i,str) and i.startswith(k+'-') for i in ids) for k in ('WN','CN','HB','HE')}
        summary.update({'unique':len(set(ids)),'groups':prefixes,
                        'approved_ready':sum(row.get('approved') is True and row.get('import_ready') is True for row in manifest if isinstance(row,dict))})
    checks.append(check('現行580筆素材合法可解析',summary.get('rows')==580 and summary.get('unique')==580
                        and summary.get('groups')=={'WN':200,'CN':100,'HB':50,'HE':230}
                        and summary.get('approved_ready')==580,summary))
    npc = source('modules/npc_history_v23.py')
    checks.append(check('NPC玩家/官方類型選單',
                        'discord.ui.Select' in npc and 'player_npc' in npc and 'official_npc' in npc,
                        '僅驗證選單程式存在；Discord 顯示待實機'))
    activation = source('core/character_activation_v23.py')
    grant = source('core/npc_initial_grants_v23.py')
    checks.append(check('NPC已核准起始待遇及交易防重',
                        all(x in activation for x in ('activate_in_transaction','_npc_attributes_present'))
                        and all(x in grant for x in ('configure','award_in_transaction')),
                        '起始金額仍必須由群主依核准值設定'))
    profile = source('modules/profile.py')
    forum = source('modules/character_forums.py')
    notifications = source('modules/notifications.py')
    checks.append(check('角色即時論壇及通知修復掛鉤',
                        all('ensure_activated_forum' in x for x in (profile,npc))
                        and 'create_forum' in forum
                        and 'ensure_activated_forum' in notifications,
                        '程式掛鉤存在，論壇權限與投遞仍須 Discord 實機'))
    history_notice = source('core/history_v23_delivery.py')
    checks.append(check('五筆歷史通知',
                        'queue_history_notice' in history_notice
                        and 'queue_history_notice' in activation,
                        '只代表有程式接線'))
    payout = source('modules/leaderboard_payout_policy.py')
    payout_open = literal_assignment('modules/leaderboard_payout_policy.py','PAYOUT_ENABLED')
    checks.append(check('排行榜安全發獎政策',
                        'require_payout_permission' in payout and payout_open is False,
                        '自動正式發獎仍關閉；不能當作已入帳', gate=False))
    checks.append(check('排行榜正式自動發獎可啟用',payout_open is True,
                        '發獎開關關閉且部分榜單缺少核准獎勵/適配器'))
    activity = source('modules/event_project_manager.py')
    checks.append(check('活動企劃模組存在', 'CREATE TABLE' in activity.upper() and 'rp_event_project_audit' in activity,
                        '靜態檢查；不是 SQLite 實際驗證'))
    # 不把自動化靜態檢查等同於真實 Discord 或 Railway 服務健康。
    checks.append(check('Discord論壇/通知/指令實機驗證',False,'需要測試伺服器、權限與 Bot Token'))
    checks.append(check('Railway資料庫與重啟驗證',False,'需要實際部署、資料庫備份、重啟與日志核對'))
    gates=[c for c in checks if c['release_gate']]
    return {'project':'流光城','version':'20261115-063325','checks':checks,
            'passing':sum(c['ok'] for c in checks),
            'remaining_checks':sum(not c['ok'] for c in checks),
            'release_ready':all(c['ok'] for c in gates),
            'notes':['靜態檢查與隔離測試不能代替 Discord/Railway 實機。',
                     '正式排行榜發獎維持關閉，不會自行打開。',
                     '本工具不連線 Discord、不開啟或修改 SQLite。']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json',type=Path)
    parser.add_argument('--strict',action='store_true')
    args=parser.parse_args(argv)
    report=audit()
    for item in report['checks']:
        print(('PASS' if item['ok'] else 'PENDING')+' '+item['name']+'：'+str(item['detail']))
    print(f"總共 {len(report['checks'])} 項；通過 {report['passing']} 項；待處理 {report['remaining_checks']} 項。")
    print('正式上線準備：'+('已達門檻' if report['release_ready'] else '未達門檻'))
    if args.json:
        args.json.parent.mkdir(parents=True,exist_ok=True)
        args.json.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 1 if args.strict and not report['release_ready'] else 0


if __name__=='__main__':
    raise SystemExit(main())
