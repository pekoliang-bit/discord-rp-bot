"""新手指令獎勵（成功操作後觸發，按角色/指令防重複）。

每一種獎勵都使用來源唯一鍵或同交易收據；中途失敗可安全重試。
陣營獎勵須角色已經加入陣營，未加入時保留待發放，不會虛報成功。
"""
from __future__ import annotations

import logging
from core.database import get_connection, transaction, get_or_create_account
from core.economy import issue_system_reward, _payment

LOG = logging.getLogger(__name__)

REWARDS = {
    '查看': ('free',30,'自由點數 +30'),
    '登記': ('skill',30,'技能點數 +30'),
    '公司': ('coins',500,'金幣 +500'),
    '商城': ('coins',1000,'金幣 +1,000'),
    '申請': ('ticket',1,'普通抽獎券 ×1'),
    '銀行': ('bank',1000,'銀行活期存款 +1,000 金幣'),
    '陣營': ('faction',500,'所屬陣營經費 +500 金幣'),
    '網路': ('fans',10,'粉絲 +10'),
    '劇情': ('credit',1500,'商城禮金 +1,500'),
    '傳聞': ('heat',50,'流量熱度 +50'),
    '娛樂城': ('coins',2000,'金幣 +2,000'),
    '惡意': ('fans',30,'粉絲 +30'),
    '排行': ('actions',2,'行動值 +2'),
    '總覽': ('actions',2,'行動值 +2'),
    '抽獎': ('ticket',1,'普通抽獎券 ×1'),
}


# 簡潔操作說明會和獎勵一起展示；沒有填入的外部資料不得視為成功。
EXPLANATIONS = {
    '查看':'開啟角色面板、檢查資料與屬性',
    '登記':'查看或使用角色登記入口',
    '公司':'開啟公司中心、了解應徵流程',
    '商城':'開啟商城並瀏覽商品',
    '申請':'開啟申請中心，了解案件分類',
    '銀行':'開啟銀行、查看帳戶功能',
    '陣營':'查看陣營並完成加入（經費入池）',
    '網路':'開啟網路帳號與互動功能',
    '劇情':'瀏覽劇情創作入口',
    '傳聞':'查看傳聞頁與相關規則',
    '娛樂城':'查看娛樂城玩法與風險',
    '惡意':'開啟惡意行動並了解限制',
    '任務':'閱讀任務種類和回報規定',
    '排行':'查看排行榜與自己的位置',
    '總覽':'瀏覽主要系統與當前狀態',
    '抽獎':'查看獎池與抽獎券使用方法',
}


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_newbie_bonus_receipts (
        guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
        command TEXT NOT NULL, reward_kind TEXT NOT NULL, amount INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('done','pending')),
        message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,character_id,command))''')


def _receipt(gid,cid,command,kind,amount):
    with transaction() as db:
        initialize(db)
        db.execute('''INSERT INTO rp_newbie_bonus_receipts(guild_id,character_id,command,reward_kind,amount,status)
           VALUES (?,?,?,?,?,'done')
           ON CONFLICT(guild_id,character_id,command)
           DO UPDATE SET status='done',message='',reward_kind=excluded.reward_kind,amount=excluded.amount''',
           (gid,cid,command,kind,amount))


def status(guild_id,character_id):
    with get_connection() as db:
        initialize(db)
        return {r['command']: dict(r) for r in db.execute(
            'SELECT * FROM rp_newbie_bonus_receipts WHERE guild_id=? AND character_id=?',
            (str(guild_id),int(character_id)))}


def deliver(guild_id, character_id, user_id, command):
    """只有可信任的成功指令紀錄可以呼叫；重試不重複發獎。"""
    if command not in REWARDS:
        return 'none'
    gid,cid,uid = str(guild_id), int(character_id), str(user_id)
    kind,amount,label=REWARDS[command]
    from modules.task_repository import get_events
    if not any(r['event_key']=='newbie:'+command for r in get_events(gid,cid)):
        raise PermissionError('未取得這項新手任務的正式成功紀錄')
    with get_connection() as db:
        row=db.execute("SELECT 1 FROM characters WHERE guild_id=? AND id=? AND owner_id=? AND status='active'",(gid,cid,uid)).fetchone()
        if not row:raise PermissionError('新手獎勵只能給本人已啟用角色')
    key=f'newbie:{gid}:{cid}:{command}'
    if kind in ('free','skill'):
        from modules.leaderboard_points_storage import grant_points
        grant_points(gid,cid,kind,amount,key)
    elif kind=='coins':
        issue_system_reward(gid,key,'character',cid,amount,'新手探索完成：/'+command)
    elif kind=='ticket':
        from core.ordinary_lottery_tickets import grant_newbie
        grant_newbie(gid,uid,cid,key,amount)
    elif kind=='credit':
        from core.newbie_shop_credit import grant
        grant(gid,cid,amount,key)
    elif kind in ('fans','heat'):
        from core.stat_rewards import grant_stats
        grant_stats(gid,cid,{'粉絲' if kind=='fans' else '流量熱度':amount},
                    source='mission_reward',source_id=key,reason='新手探索完成：/'+command)
    elif kind=='bank':
        with transaction() as db:
            src=get_or_create_account(db,gid,'system','issuance','treasury')
            dst=get_or_create_account(db,gid,'character',str(cid),'bank')
            _payment(db,gid,key,src,dst,amount,'新手銀行活期存款獎勵')
    elif kind=='faction':
        from modules.faction_core import initialize as init_factions
        init_factions()
        with transaction() as db:
            initialize(db)
            existing=db.execute('SELECT 1 FROM rp_newbie_bonus_receipts WHERE guild_id=? AND character_id=? AND command=? AND status="done"',(gid,cid,command)).fetchone()
            if existing:return 'already'
            member=db.execute('SELECT faction_id FROM faction_members WHERE guild_id=? AND character_id=?',(gid,cid)).fetchone()
            if not member:
                raise ValueError('請先加入陣營，陣營經費獎勵會保留待領')
            period=db.execute('SELECT sequence FROM faction_periods WHERE guild_id=?',(gid,)).fetchone()
            if not period:raise ValueError('陣營期別尚未啟用')
            cur=db.execute('UPDATE faction_groups SET pool=pool+? WHERE guild_id=? AND id=?',
                           (amount,gid,int(member['faction_id'])))
            if cur.rowcount!=1:raise ValueError('陣營經費入帳失敗')
            db.execute('''INSERT INTO faction_ledger(guild_id,faction_id,character_id,source,amount,points,period,memo)
                VALUES (?,?,?,'group_award',?,0,?,?)''',
                (gid,int(member['faction_id']),cid,amount,int(period['sequence']),'新手指令：/'+command))
            db.execute('''INSERT INTO rp_newbie_bonus_receipts(guild_id,character_id,command,reward_kind,amount,status)
                VALUES (?,?,?,?,?,'done')''',(gid,cid,command,kind,amount))
        return 'done'
    elif kind=='actions':
        with transaction() as db:
            initialize(db)
            existing=db.execute('SELECT 1 FROM rp_newbie_bonus_receipts WHERE guild_id=? AND character_id=? AND command=? AND status="done"',(gid,cid,command)).fetchone()
            if existing:return 'already'
            db.execute('''CREATE TABLE IF NOT EXISTS vip_balances (
                guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
                free_points INTEGER NOT NULL DEFAULT 0,
                bonus_actions INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(guild_id,character_id),CHECK(free_points>=0),CHECK(bonus_actions>=0))''')
            db.execute('''INSERT INTO vip_balances(guild_id,character_id,bonus_actions)
                VALUES (?,?,?) ON CONFLICT(guild_id,character_id)
                DO UPDATE SET bonus_actions=bonus_actions+excluded.bonus_actions''',(gid,cid,amount))
            db.execute('''INSERT INTO rp_newbie_bonus_receipts(guild_id,character_id,command,reward_kind,amount,status)
                VALUES (?,?,?,?,?,'done')''',(gid,cid,command,kind,amount))
        return 'done'
    _receipt(gid,cid,command,kind,amount)
    return 'done'


def retry_completed(guild_id,character_id,user_id):
    from modules.task_newbie import completed_commands
    results={}
    for command in completed_commands(guild_id,character_id):
        if command in REWARDS:
            try:results[command]=deliver(guild_id,character_id,user_id,command)
            except Exception as exc:
                results[command]='pending'
                LOG.warning('新手獎勵待補發 guild=%s role=%s cmd=%s: %s',guild_id,character_id,command,exc)
    return results
