"""正式開群前的唯讀可玩性盤點；不預設開放或改寫玩家資料。"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from core.database import get_connection

ROOT = Path(__file__).resolve().parent.parent
# 抽獎、劇情是後續更新的內容，不作為開群阻擋條件。
OPTIONAL_CONTENT = frozenset({'抽獎', '劇情'})
REQUIRED_COMMANDS = (
    '創角', '查看', '登記', '公司', '官方', '商城', '申請', '審核',
    '娛樂城', '惡意', '任務', '總覽', '排行', '系統狀態',
    '銀行', '陣營', '網路', '行動', '傳聞', '群主指引',
)


def _check(title, ok, info, *, blocking=True):
    return {'title': title, 'passed': bool(ok), 'detail': str(info), 'blocking': blocking}


def _tables(db):
    return {str(r[0]) for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _read_csv(path):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def scan(guild_id: int | str, registered_commands, *, database=None):
    """檢查實際註冊入口、資料表、內容；無任何玩家資料寫入。"""
    registered = set(registered_commands)
    results = []
    missing = sorted(set(REQUIRED_COMMANDS) - registered)
    results.append(_check('玩家指令入口', not missing,
                          '缺少：' + '、'.join(missing) if missing else f'{len(REQUIRED_COMMANDS)} 個核心入口已註冊'))
    optional = sorted(OPTIONAL_CONTENT - registered)
    results.append(_check('抽獎及劇情', True,
                          ('後續更新，不阻擋開群；尚缺：' + '、'.join(optional)) if optional else '不要求預先上架活動', blocking=False))
    tagfile = ROOT / 'assets/impression_tags/catalog_v2_1.csv'
    try:
        rows = _read_csv(tagfile)
        tags_ok = len(rows) == 162 and all(r.get('效果審核狀態') == '已核准' for r in rows)
        detail = f'{len(rows)} 個標籤；' + ('效果全部核准' if tags_ok else '數量或核准狀態異常')
    except (OSError, ValueError, UnicodeError) as exc:
        tags_ok, detail = False, f'無法讀取標籤：{type(exc).__name__}'
    results.append(_check('創角大眾印象標籤', tags_ok, detail))
    try:
        from modules.leaderboard_catalog import BOARDS
        from modules.leaderboard_payout_policy import PAYOUT_ENABLED
        boards_ok = len(BOARDS) == 28
        results.append(_check('排行榜清單', boards_ok, f'{len(BOARDS)} 個排行榜'))
        results.append(_check('排行榜獎勵正式入帳', bool(PAYOUT_ENABLED),
                              '獎勵仍處安全鎖，不能宣稱已全面啟用' if not PAYOUT_ENABLED else '全局開關已開；仍須檢查每筆派發資格'))
    except (ImportError, AttributeError, ValueError) as exc:
        results.append(_check('排行榜核心', False, f'{type(exc).__name__}: {exc}'))
    templatefile = ROOT / 'assets/rumor_templates/rumor_templates_100.json'
    try:
        obj = json.loads(templatefile.read_text(encoding='utf-8'))
        if isinstance(obj, list): count = len(obj)
        elif isinstance(obj, dict):
            count = len(next((v for v in obj.values() if isinstance(v, list)), []))
        else: count = 0
        results.append(_check('傳聞模板', count >= 100, f'目前讀取到 {count} 則'))
    except (OSError, ValueError) as exc:
        results.append(_check('傳聞模板', False, f'無法載入：{type(exc).__name__}'))

    # Count the actual approved chat comment files, not the manifest's advertised count.
    comment_dir = ROOT / 'assets/internet_comment_library'
    categories, ids, duplicate = {}, set(), False
    try:
        for file in sorted(comment_dir.glob('comments_part_*.json')):
            raw = json.loads(file.read_text(encoding='utf-8'))
            for comment in raw.get('approved', []):
                if comment.get('status') != 'approved':
                    continue
                ident = comment.get('id')
                if ident in ids:
                    duplicate = True
                ids.add(ident)
                category = int(comment['category_id'])
                categories[category] = categories.get(category, 0) + 1
        comments_ok = len(ids) >= 960 and len(categories) == 12 and all(
            count >= 80 for count in categories.values()) and not duplicate
        results.append(_check('網路評論可用庫', comments_ok,
                              f'{len(ids)} 則核准評論／{len(categories)} 分類；'
                              + ('每類至少80則' if comments_ok else '缺少類別、內容或出現重複編號')))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        results.append(_check('網路評論可用庫', False, f'內容無法讀取：{type(exc).__name__}'))

    def scan_db(db):
        table_names = _tables(db)
        needed = ('characters', 'attributes', 'accounts', 'shop_products')
        absent = [table for table in needed if table not in table_names]
        results.append(_check('角色、金流與商城資料表', not absent,
                              '缺少：' + '、'.join(absent) if absent else '必要資料表存在'))
        # New guilds might have no players. Missing players is not lack of gameplay.
        # Approved actions, however, must exist before promising playability.
        # A table's existence does NOT prove that players can execute any action.
        # Count only explicitly approved entries for this guild, then combine
        # with approved bundled actions scoped to their designated guild.
        approved = 0
        for table in ('action_locations', 'rp_actions', 'action_configs'):
            if table not in table_names:
                continue
            columns = {str(row[1]) for row in db.execute(f'PRAGMA table_info({table})')}
            if {'guild_id', 'approved'} <= columns:
                try:
                    approved += int(db.execute(
                        f'SELECT COUNT(*) FROM {table} WHERE guild_id=? AND approved=1',
                        (str(guild_id),),
                    ).fetchone()[0])
                except Exception:
                    # An unknown schema must never be treated as approved content.
                    continue
        for file in (ROOT / 'assets/action_locations').glob('*.json'):
            try:
                raw = json.loads(file.read_text(encoding='utf-8'))
                if str(raw.get('target_guild_id')) != str(guild_id):
                    continue
                for loc in raw.get('locations', []):
                    if not isinstance(loc, dict):
                        continue
                    for action in loc.get('actions', []):
                        if isinstance(action, dict) and action.get('approved') is True:
                            approved += 1
            except (OSError, ValueError, AttributeError, TypeError):
                continue
        results.append(_check('可遊玩行動內容', approved > 0,
                              f'本伺服器找到 {approved} 筆明確核准行動；僅有待審資料不算可遊玩'))
        if 'shop_products' in table_names:
            try:
                available = db.execute(
                    "SELECT COUNT(*) FROM shop_products WHERE guild_id=? AND active=1 AND (stock IS NULL OR stock>0)",
                    (str(guild_id),),
                ).fetchone()[0]
                results.append(_check('商城實際商品', available > 0,
                                      f'本伺服器目前 {available} 個可購買商品；空商城不可視作已可玩'))
            except Exception as exc:
                results.append(_check('商城實際商品', False, f'商品查詢失敗：{type(exc).__name__}'))
    try:
        if database is not None:
            scan_db(database)
        else:
            with get_connection() as db: scan_db(db)
    except Exception as exc:
        results.append(_check('正式資料庫連接', False, f'{type(exc).__name__}: {exc}'))
    return {'guild_id': str(guild_id), 'checks': results,
            'passed': all(r['passed'] for r in results if r['blocking']),
            'blocking': [r for r in results if r['blocking'] and not r['passed']]}


def render(result):
    lines = ['🚪 **正式開群｜系統可玩性檢查**',
             '✅ 通過開群基礎檢查' if result['passed'] else '⛔ 尚有未通過項目，不可宣稱正式驗收完成', '']
    for r in result['checks']:
        symbol = '✅' if r['passed'] else ('⛔' if r['blocking'] else 'ℹ️')
        lines.append(f"{symbol} **{r['title']}**：{r['detail']}")
    lines.extend(['', '此為只讀預檢；Discord 實際點擊、權限、跨模組交易及 14/30 天排程仍需實機驗證。'])
    return '\n'.join(lines)[:3900]
