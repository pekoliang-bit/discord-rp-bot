"""核准商業開戶申請時，把授權操作人與權限存到獨立持久表。

僅儲存允許操作的權限，業務端必須在實際存提款／轉帳時檢查此表，
不能依賴 UI 顯示就視為已授權。
"""
from __future__ import annotations
import json

ALLOWED=frozenset({'view','deposit','withdraw','transfer','statements'})


def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_bank_authorized_operators (
        guild_id TEXT NOT NULL, account_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        permissions_json TEXT NOT NULL,
        application_id INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,account_id,character_id)
    )''')


def save_from_application(db,gid,account_id,app_id,user_id,kind,title,raw):
    from modules.business_bank_request_ui import resolve_target,validate_operator
    data=json.loads(raw)
    if not isinstance(data,dict) or data.get('bank_v2') is not True:
        raise ValueError('舊版銀行申請缺少授權操作人資料，請重新送件。')
    target,target_kind=resolve_target(db,gid,kind,title,user_id)
    if str(target)!=str(title):raise ValueError('申請主體資料已改變，拒絕核准。')
    ops=data.get('operators')
    if not isinstance(ops,list) or not ops:
        raise ValueError('至少需要一位授權操作人。')
    setup(db)
    for item in ops:
        cid,_=validate_operator(db,gid,item['character_id'])
        permissions=item.get('permissions')
        if not isinstance(permissions,list) or not permissions or not set(permissions)<=ALLOWED:
            raise ValueError('授權權限無效。')
        db.execute('''INSERT INTO rp_bank_authorized_operators(guild_id,account_id,character_id,permissions_json,application_id)
            VALUES(?,?,?,?,?) ON CONFLICT(guild_id,account_id,character_id)
            DO UPDATE SET permissions_json=excluded.permissions_json,application_id=excluded.application_id''',
            (str(gid),int(account_id),cid,json.dumps(sorted(set(permissions))),int(app_id)))


def can_operate(db,guild_id,account_id,user_id,character_id,permission):
    """日後銀行面板在每次實際金流交易前呼叫，不得只檢查畫面選單。"""
    if permission not in ALLOWED:
        return False
    setup(db)
    character=db.execute('''SELECT 1 FROM characters
        WHERE guild_id=? AND id=? AND owner_id=? AND status='active' ''',
        (str(guild_id),int(character_id),str(user_id))).fetchone()
    if character is None:
        return False
    row=db.execute('''SELECT permissions_json FROM rp_bank_authorized_operators
        WHERE guild_id=? AND account_id=? AND character_id=?''',
        (str(guild_id),int(account_id),int(character_id))).fetchone()
    if row is None:
        return False
    try:
        return permission in json.loads(row['permissions_json'])
    except (ValueError,TypeError):
        return False
