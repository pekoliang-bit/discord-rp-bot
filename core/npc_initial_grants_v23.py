"""V2.3 NPC 起始金幣／永久免費行動值的核准與一次性入帳。

金額由伺服器群主依既有核准版本設定；本服務絕不自行推算。
須在正式啟用交易內發放；欠缺核准資料則拒絕啟用。
"""
from __future__ import annotations

from core.database import transaction, get_or_create_account
from core.economy import _payment
from core.permissions import InvalidState

MAX_COINS = 10**12
MAX_ACTIONS = 100000


def init(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_npc_initial_grants_v23 (
       guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
       coins INTEGER NOT NULL CHECK(coins BETWEEN 0 AND 1000000000000),
       free_actions INTEGER NOT NULL CHECK(free_actions BETWEEN 0 AND 100000),
       approved_by TEXT NOT NULL, approved_at TEXT DEFAULT CURRENT_TIMESTAMP,
       applied_at TEXT, PRIMARY KEY(guild_id,character_id),
       FOREIGN KEY(guild_id,character_id) REFERENCES characters(guild_id,id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS rp_character_free_actions_v23 (
       guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
       balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0),
       PRIMARY KEY(guild_id,character_id),
       FOREIGN KEY(guild_id,character_id) REFERENCES characters(guild_id,id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS rp_character_free_action_events_v23 (
       guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
       source_key TEXT NOT NULL, delta INTEGER NOT NULL,
       created_at TEXT DEFAULT CURRENT_TIMESTAMP,
       PRIMARY KEY(guild_id,source_key)
    )""")


def configure(guild_id, character_id, owner_id, coins, free_actions):
    """此服務只供群主已通過的 Discord 管理入口呼叫。"""
    gid,cid,uid=str(guild_id),int(character_id),str(owner_id)
    if type(coins) is not int or type(free_actions) is not int:
        raise ValueError('金幣和行動值必須是非負整數。')
    if not (0<=coins<=MAX_COINS and 0<=free_actions<=MAX_ACTIONS):
        raise ValueError('金幣或行動值超過安全限制。')
    with transaction() as db:
        init(db)
        c=db.execute("""SELECT character_type,status FROM characters
           WHERE guild_id=? AND id=?""",(gid,cid)).fetchone()
        if not c or c['character_type'] not in ('player_npc','official_npc'):
            raise ValueError('只能設定 NPC 初始待遇。')
        if c['status']!='approved' and not (c['character_type']=='official_npc' and c['status']=='active'):
            raise ValueError('玩家NPC須完成二審；官方NPC須群主登記啟用後，才能設定待遇。')
        old=db.execute('SELECT applied_at FROM rp_npc_initial_grants_v23 WHERE guild_id=? AND character_id=?',(gid,cid)).fetchone()
        if old and old['applied_at']:
            raise ValueError('初始待遇已發放，禁止重新設定。')
        db.execute("""INSERT INTO rp_npc_initial_grants_v23
           (guild_id,character_id,coins,free_actions,approved_by)
           VALUES (?,?,?,?,?) ON CONFLICT(guild_id,character_id) DO UPDATE SET
           coins=excluded.coins,free_actions=excluded.free_actions,
           approved_by=excluded.approved_by,approved_at=CURRENT_TIMESTAMP""",
           (gid,cid,coins,free_actions,uid))
        # 官方 NPC 由群主登記時已立即 active；核准後直接在同筆交易內入帳。
        if c['character_type']=='official_npc' and c['status']=='active':
            award_in_transaction(db,gid,cid)
    return {'coins':coins,'free_actions':free_actions}


def award_in_transaction(db,guild_id,character_id):
    """必須在啟用的同一個 transaction() 內呼叫。"""
    gid,cid=str(guild_id),int(character_id)
    init(db)
    r=db.execute('SELECT * FROM rp_npc_initial_grants_v23 WHERE guild_id=? AND character_id=?',(gid,cid)).fetchone()
    if r is None:
        raise InvalidState('NPC 尚未由群主設定已核准的初始金幣與行動值，禁止啟用。')
    if r['applied_at']:
        raise InvalidState('NPC 初始待遇已發放，禁止再次發放。')
    coins,actions=int(r['coins']),int(r['free_actions'])
    if coins:
        source=get_or_create_account(db,gid,'system','issuance','treasury')
        dest=get_or_create_account(db,gid,'character',str(cid),'wallet')
        outcome=_payment(db,gid,f'npc-initial-v23:{gid}:{cid}',source,dest,coins,'NPC 二審核准初始金幣')
        if outcome.duplicated:
            raise InvalidState('發現已存在 NPC 初始金幣紀錄，請管理員先查帳。')
    if actions:
        key=f'npc-initial-v23:{gid}:{cid}:actions'
        cursor=db.execute('INSERT OR IGNORE INTO rp_character_free_action_events_v23 (guild_id,character_id,source_key,delta) VALUES (?,?,?,?)',(gid,cid,key,actions))
        if cursor.rowcount!=1:
            raise InvalidState('發現已存在 NPC 初始行動值紀錄，請管理員先查帳。')
        db.execute("""INSERT INTO rp_character_free_actions_v23(guild_id,character_id,balance)
            VALUES (?,?,?) ON CONFLICT(guild_id,character_id)
            DO UPDATE SET balance=balance+excluded.balance""",(gid,cid,actions))
    updated=db.execute('UPDATE rp_npc_initial_grants_v23 SET applied_at=CURRENT_TIMESTAMP WHERE guild_id=? AND character_id=? AND applied_at IS NULL',(gid,cid))
    if updated.rowcount!=1:raise InvalidState('NPC 初始待遇狀態異常。')
    return {'coins':coins,'free_actions':actions}


def get_balance(db,guild_id,character_id):
    init(db)
    row=db.execute('SELECT balance FROM rp_character_free_actions_v23 WHERE guild_id=? AND character_id=?',(str(guild_id),int(character_id))).fetchone()
    return int(row['balance']) if row else 0
