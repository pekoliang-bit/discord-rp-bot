"""／行動取得的專業點數：正式消費、唯一請求及角色權限檢查。

專業點數只能提升已核准技能，不能提升一般屬性或影響指數。
沿用 core.action_execution 的餘額表，不另創第二種可用餘額。
"""
from __future__ import annotations

from core.database import get_connection, transaction
from core.action_execution import initialize
from modules.leaderboard_points_catalog import validate_target


def verify_owner(guild_id, character_id, user_id):
    with get_connection() as db:
        char = db.execute("""SELECT owner_id,status FROM characters
            WHERE guild_id=? AND id=?""", (str(guild_id), int(character_id))).fetchone()
    if char is None or char['status'] != 'active':
        raise ValueError('角色不存在或尚未啟用')
    if str(char['owner_id']) != str(user_id):
        raise PermissionError('只能分配自己的角色點數')


def get_balance(guild_id, character_id):
    with get_connection() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_action_professional_points'").fetchone() is None:
            return 0
        row = db.execute("""SELECT balance FROM rp_action_professional_points
            WHERE guild_id=? AND character_id=?""", (str(guild_id), int(character_id))).fetchone()
        return int(row['balance']) if row else 0


def allocate(guild_id, character_id, user_id, target, amount, request_key):
    if type(amount) is not int or amount <= 0:
        raise ValueError('分配的專業點數必須是正整數')
    if not isinstance(request_key, str) or not request_key.strip():
        raise ValueError('缺少唯一操作識別碼')
    validate_target('skill', 'skill', target)
    guild_id, character_id = str(guild_id), int(character_id)
    with transaction() as db:
        initialize(db)
        db.execute("""CREATE TABLE IF NOT EXISTS rp_action_professional_history (
            guild_id TEXT NOT NULL, request_key TEXT NOT NULL,
            character_id INTEGER NOT NULL, target TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK(amount>0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,request_key))""")
        char = db.execute("""SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?""",
                          (guild_id, character_id)).fetchone()
        if char is None or char['status'] != 'active':
            raise ValueError('角色不存在或尚未啟用')
        if str(char['owner_id']) != str(user_id):
            raise PermissionError('只能分配自己的角色點數')
        old = db.execute("""SELECT character_id,target,amount FROM rp_action_professional_history
            WHERE guild_id=? AND request_key=?""", (guild_id, request_key)).fetchone()
        if old:
            if (int(old['character_id']),old['target'],int(old['amount'])) != (character_id,target,amount):
                raise ValueError('相同操作識別碼已用於不同分配')
            return {'status':'already_processed', 'balance': get_balance_from_db(db,guild_id,character_id)}
        cursor = db.execute("""UPDATE rp_action_professional_points SET balance=balance-?
            WHERE guild_id=? AND character_id=? AND balance>=?""",
            (amount,guild_id,character_id,amount))
        if cursor.rowcount != 1:
            raise ValueError('專業點數餘額不足')
        db.execute("""INSERT INTO character_skills(guild_id,character_id,skill_name,skill_value)
            VALUES(?,?,?,?) ON CONFLICT(guild_id,character_id,skill_name)
            DO UPDATE SET skill_value=skill_value+excluded.skill_value""",
            (guild_id,character_id,target,amount))
        db.execute("""INSERT INTO rp_action_professional_history
            (guild_id,request_key,character_id,target,amount) VALUES(?,?,?,?,?)""",
            (guild_id,request_key,character_id,target,amount))
        return {'status':'allocated','balance':get_balance_from_db(db,guild_id,character_id),
                'target':target,'amount':amount}


def get_balance_from_db(db, guild_id, character_id):
    row=db.execute("""SELECT balance FROM rp_action_professional_points
        WHERE guild_id=? AND character_id=?""",(guild_id,character_id)).fetchone()
    return int(row['balance']) if row else 0
