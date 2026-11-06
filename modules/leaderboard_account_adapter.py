"""排行榜收款帳戶唯讀驗證。不可在預覽階段建立任何帳戶或轉帳。"""
from __future__ import annotations

from modules.leaderboard_payout_identity import validate_identity


class AccountAdapterError(ValueError):
    pass


# 只允許已明確與現有 accounts 帳本對應的業務類型。
# 個人商鋪銷售帳本、陣營 pool、CP 與社群帳號不是任意 accounts 子帳戶。
ACCOUNT_ROUTES = {
    'character': ('character', 'wallet'),
    'company': ('organization', 'treasury'),
    'group': ('organization', 'treasury'),
}


def _table_exists(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _entity_exists(db, guild_id, kind, entity_id):
    gid = str(guild_id)
    if kind == 'character':
        table, sql = 'characters', "SELECT 1 FROM characters WHERE guild_id=? AND id=? AND status='active'"
    elif kind in ('company','group'):
        table, sql = 'rp_business_entities', "SELECT 1 FROM rp_business_entities WHERE guild_id=? AND business_id=? AND kind=? AND status='active'"
    elif kind == 'shop':
        table, sql = 'personal_shops', "SELECT 1 FROM personal_shops WHERE guild_id=? AND id=? AND status='open'"
    elif kind == 'faction':
        table, sql = 'faction_groups', 'SELECT 1 FROM faction_groups WHERE guild_id=? AND id=?'
    else:
        return False
    if not _table_exists(db, table):
        return False
    args = (gid, entity_id, kind) if kind in ('company','group') else (gid, entity_id)
    return db.execute(sql,args).fetchone() is not None


def inspect_account(db, guild_id, board, entity_key):
    """傳入已開啟的資料庫連線；僅 SELECT。缺失即報原因，不自行補帳戶。"""
    parsed = validate_identity(board, entity_key)
    kind, eid = parsed['entity_type'], str(parsed['entity_id'])
    result = {'guild_id':str(guild_id),'entity_type':kind,'entity_id':eid,
              'exists':False,'account_id':None,'owner_type':None,'account_type':None,
              'can_credit':False,'issues':[]}
    if not _entity_exists(db, guild_id, kind, eid):
        result['issues'].append('獲獎單位不存在、不屬於此伺服器或尚未啟用')
        return result
    result['exists'] = True
    if kind not in ACCOUNT_ROUTES:
        result['issues'].append('此單位使用非標準帳本，尚無核准的金幣入帳適配器')
        return result
    owner_type, account_type = ACCOUNT_ROUTES[kind]
    result['owner_type'], result['account_type'] = owner_type, account_type
    if not _table_exists(db,'accounts'):
        result['issues'].append('正式帳戶資料表不存在')
        return result
    row = db.execute("SELECT id FROM accounts WHERE guild_id=? AND owner_type=? AND owner_id=? AND account_type=?",
                     (str(guild_id),owner_type,eid,account_type)).fetchone()
    if row is None:
        result['issues'].append('正式金流帳戶尚未開立；預覽不得自行建立')
        return result
    result['account_id'] = int(row['id'])
    result['can_credit'] = True
    return result
