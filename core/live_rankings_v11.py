"""直播熱度榜 V1.1：五日週期、事件冪等、永久熱度、宣傳扣款。

排名每次讀取按 UTC 整點快照計算；未核准名次獎勵只封存，不發角色屬性。
"""
from __future__ import annotations
import time
from core.database import transaction, get_connection, get_or_create_account, account_balance
from core.economy import _payment
from core import live_stream

PERIOD = 120 * 3600
LEVEL_THRESHOLDS = (0, 5000, 20000, 50000, 100000, 200000, 400000, 700000, 1100000, 1600000)
START_BONUSES = (0, 50, 100, 200, 300, 500, 800, 1100, 1500, 2000)
AUDIENCE_BONUSES = ((5, 50), (10, 100), (15, 500), (16, 700), (17, 900), (18, 1200), (19, 1500), (20, 3000))
BRAND_BONUSES = {'平價': 500, '小資': 1000, '輕奢': 1500, '奢侈': 2000, '高奢': 2000}
FIRST_AWARD = {'流量熱度': 1500, '知名度': 1000, '粉絲': 500, '黑粉': 200}

DDL = (
    """CREATE TABLE IF NOT EXISTS rp_live_heat_events (
        guild_id TEXT NOT NULL, event_key TEXT NOT NULL, session_id INTEGER NOT NULL,
        host_character_id INTEGER NOT NULL, amount INTEGER NOT NULL CHECK(amount>=0),
        source TEXT NOT NULL, created_at INTEGER NOT NULL,
        PRIMARY KEY(guild_id,event_key))""",
    """CREATE INDEX IF NOT EXISTS idx_rp_live_heat_period ON rp_live_heat_events(guild_id,created_at,host_character_id)""",
    """CREATE TABLE IF NOT EXISTS rp_live_heat_reversals (
        guild_id TEXT NOT NULL, event_key TEXT NOT NULL, original_event_key TEXT NOT NULL,
        amount INTEGER NOT NULL CHECK(amount>=0), created_at INTEGER NOT NULL,
        PRIMARY KEY(guild_id,event_key), UNIQUE(guild_id,original_event_key))""",
    """CREATE TABLE IF NOT EXISTS rp_live_heat_followers (
        guild_id TEXT NOT NULL, follower_character_id INTEGER NOT NULL, host_character_id INTEGER NOT NULL,
        created_at INTEGER NOT NULL, PRIMARY KEY(guild_id,follower_character_id,host_character_id))""",
    """CREATE TABLE IF NOT EXISTS rp_live_heat_period_snapshots (
        guild_id TEXT NOT NULL, period_start INTEGER NOT NULL, character_id INTEGER NOT NULL,
        heat INTEGER NOT NULL, position INTEGER NOT NULL, finalized_at INTEGER NOT NULL,
        reward_status TEXT NOT NULL DEFAULT 'pending_approval',
        PRIMARY KEY(guild_id,period_start,character_id))""",
)

def initialize(db):
    live_stream._init(db)
    for ddl in DDL: db.execute(ddl)

def level_for_heat(total):
    return max(i+1 for i,threshold in enumerate(LEVEL_THRESHOLDS) if total >= threshold)

def audience_bonus(lv):
    n=max(1,min(20,int(lv)))
    return next(amount for upper,amount in AUDIENCE_BONUSES if n <= upper)

def _record(db,gid,key,sid,amount,source,ts):
    if int(amount) < 0: raise ValueError('熱度增量不可為負')
    session=db.execute('SELECT host_character_id FROM rp_live_sessions WHERE guild_id=? AND id=?', (str(gid),int(sid))).fetchone()
    if not session: raise LookupError('找不到直播場次')
    result=db.execute('''INSERT OR IGNORE INTO rp_live_heat_events
        (guild_id,event_key,session_id,host_character_id,amount,source,created_at)
        VALUES(?,?,?,?,?,?,?)''', (str(gid),str(key),int(sid),int(session['host_character_id']),int(amount),str(source),int(ts)))
    return bool(result.rowcount)

def total_heat(db,gid,host):
    initialize(db)
    sql='''SELECT COALESCE(SUM(e.amount-COALESCE(r.amount,0)),0)
        FROM rp_live_heat_events e LEFT JOIN rp_live_heat_reversals r
        ON r.guild_id=e.guild_id AND r.original_event_key=e.event_key
        WHERE e.guild_id=? AND e.host_character_id=?'''
    return max(0,int(db.execute(sql,(str(gid),int(host))).fetchone()[0]))

def reverse_heat_in_transaction(db,gid,original_event_key,reversal_key,now):
    initialize(db)
    event=db.execute('SELECT amount FROM rp_live_heat_events WHERE guild_id=? AND event_key=?',(str(gid),str(original_event_key))).fetchone()
    if not event:return False
    result=db.execute('INSERT OR IGNORE INTO rp_live_heat_reversals(guild_id,event_key,original_event_key,amount,created_at) VALUES (?,?,?,?,?)',
        (str(gid),str(reversal_key),str(original_event_key),int(event['amount']),int(now)))
    return result.rowcount>0

def start_bonus_in_transaction(db,gid,sid,host,ts):
    """防重開洗熱度：每個直播間 UTC 日曆日最多一次。"""
    initialize(db)
    lv=level_for_heat(total_heat(db,gid,host))
    day=int(ts)//86400
    bonus=START_BONUSES[lv-1]
    if bonus: return _record(db,gid,f'start:{host}:{day}',sid,bonus,'opening_bonus',ts)
    return False

def view(gid,host,session_id,viewer_character_id,*,now=None):
    ts=int(time.time() if now is None else now)
    with transaction() as db:
        initialize(db)
        s=db.execute("SELECT * FROM rp_live_sessions WHERE guild_id=? AND id=? AND status='live' AND (ends_at IS NULL OR ends_at>?)", (str(gid),int(session_id),ts)).fetchone()
        if not s or int(s['host_character_id'])!=int(host): raise ValueError('直播不存在或已結束')
        visitor=db.execute("SELECT id,owner_id FROM characters WHERE guild_id=? AND id=? AND status='active'", (str(gid),int(viewer_character_id))).fetchone()
        if not visitor: raise ValueError('參觀角色不存在或尚未啟用')
        if int(visitor['id'])==int(host) or str(visitor['owner_id'])==str(s['owner_id']): return 0
        status=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rp_character_status'").fetchone()
        lv=1
        if status:
            row=db.execute('SELECT level FROM rp_character_status WHERE guild_id=? AND character_id=?', (str(gid),int(viewer_character_id))).fetchone()
            if row: lv=max(1,min(20,int(row['level'])))
        amount=10+audience_bonus(lv)
        key=f'view:{host}:{viewer_character_id}:{ts//3600}'
        return amount if _record(db,gid,key,session_id,amount,'audience_entry',ts) else 0

def follow(gid, user_id, follower_character_id, host_character_id, *, now=None):
    ts=int(time.time() if now is None else now)
    with transaction() as db:
        initialize(db)
        owned=db.execute("SELECT id,owner_id FROM characters WHERE guild_id=? AND id=? AND status='active'", (str(gid),int(follower_character_id))).fetchone()
        host=db.execute("SELECT id,owner_id FROM characters WHERE guild_id=? AND id=? AND status='active'", (str(gid),int(host_character_id))).fetchone()
        if not owned or str(owned['owner_id'])!=str(user_id): raise PermissionError('只能使用自己的角色關注')
        if not host: raise LookupError('目標角色不存在')
        if str(host['owner_id'])==str(user_id): raise ValueError('不能關注自己的直播間')
        result=db.execute('INSERT OR IGNORE INTO rp_live_heat_followers VALUES (?,?,?,?)', (str(gid),int(follower_character_id),int(host_character_id),ts))
        if result.rowcount:
            s=db.execute("SELECT id FROM rp_live_sessions WHERE guild_id=? AND host_character_id=? AND status='live' ORDER BY id DESC LIMIT 1", (str(gid),int(host_character_id))).fetchone()
            if s: _record(db,gid,f'follow:{host_character_id}:{follower_character_id}',s['id'],5,'new_follow',ts)
        return bool(result.rowcount)

def followers(gid,host):
    with get_connection() as db:
        initialize(db)
        return [dict(r) for r in db.execute('SELECT follower_character_id FROM rp_live_heat_followers WHERE guild_id=? AND host_character_id=?', (str(gid),int(host))).fetchall()]

def promote(gid,user_id,host,sid,coins,request_key,*,now=None):
    """官方合法曝光：金幣轉給平台；不是買粉或假流量。"""
    if type(coins) is not int or coins<100 or coins>1_000_000 or coins%100: raise ValueError('每次須支付 100～1,000,000 金幣，且為100的倍數')
    ts=int(time.time() if now is None else now)
    with transaction() as db:
        initialize(db)
        s=db.execute("SELECT * FROM rp_live_sessions WHERE guild_id=? AND id=? AND status='live'", (str(gid),int(sid))).fetchone()
        if not s or int(s['host_character_id'])!=int(host) or str(s['owner_id'])!=str(user_id): raise PermissionError('只能為自己進行中的直播購買曝光')
        key=f'promote:{request_key}'
        exists=db.execute('SELECT amount FROM rp_live_heat_events WHERE guild_id=? AND event_key=?', (str(gid),key)).fetchone()
        if exists: return {'heat':int(exists['amount']), 'already_done':True}
        payer=get_or_create_account(db,str(gid),'character',int(host),'wallet')
        target=get_or_create_account(db,str(gid),'system','live_platform','wallet')
        if account_balance(db,str(gid),payer)<coins: raise ValueError('金幣不足')
        _payment(db,str(gid),f'live:{key}',payer,target,coins,'直播間官方曝光推廣')
        heat=coins//100*10
        _record(db,gid,key,sid,heat,'paid_promotion',ts)
        return {'heat':heat,'already_done':False}

def period_rows(gid,*,now=None):
    ts=int(time.time() if now is None else now)
    period_start=(ts//PERIOD)*PERIOD
    hourly_cutoff=(ts//3600)*3600
    with transaction() as db:
        initialize(db)
        # Backfill older periods with real events, including after lengthy downtime.
        old_periods=db.execute("""SELECT DISTINCT (created_at / ?) * ? AS period_start
            FROM rp_live_heat_events WHERE guild_id=? AND created_at<?
            ORDER BY period_start LIMIT 300""",(PERIOD,PERIOD,str(gid),period_start)).fetchall()
        for past in old_periods:
            old_start=int(past['period_start'])
            if db.execute('SELECT 1 FROM rp_live_heat_period_snapshots WHERE guild_id=? AND period_start=? LIMIT 1',
                          (str(gid),old_start)).fetchone():
                continue
            old=db.execute("""SELECT e.host_character_id AS character_id,SUM(e.amount-COALESCE(r.amount,0)) AS heat
                FROM rp_live_heat_events e LEFT JOIN rp_live_heat_reversals r
                ON r.guild_id=e.guild_id AND r.original_event_key=e.event_key
                WHERE e.guild_id=? AND e.created_at>=? AND e.created_at<? GROUP BY e.host_character_id
                ORDER BY heat DESC,e.host_character_id""",(str(gid),old_start,old_start+PERIOD)).fetchall()
            for i,r in enumerate(old):
                db.execute('INSERT OR IGNORE INTO rp_live_heat_period_snapshots(guild_id,period_start,character_id,heat,position,finalized_at) VALUES (?,?,?,?,?,?)',
                    (str(gid),old_start,int(r['character_id']),max(0,int(r['heat'])),i+1,ts))
        rows=db.execute('''SELECT e.host_character_id AS character_id,SUM(e.amount-COALESCE(r.amount,0)) AS heat
            FROM rp_live_heat_events e LEFT JOIN rp_live_heat_reversals r
            ON r.guild_id=e.guild_id AND r.original_event_key=e.event_key
            WHERE e.guild_id=? AND e.created_at>=? AND e.created_at<? GROUP BY e.host_character_id
            ORDER BY heat DESC,e.host_character_id LIMIT 25''',(str(gid),period_start,min(period_start+PERIOD,hourly_cutoff))).fetchall()
        return {'period_start':period_start,'period_end':period_start+PERIOD,'refresh_at':hourly_cutoff,'rows':[dict(x) for x in rows]}

def room(gid,host):
    with get_connection() as db:
        initialize(db)
        total=total_heat(db,gid,host)
        return {'total_heat':total,'level':level_for_heat(total),'opening_bonus':START_BONUSES[level_for_heat(total)-1], 'followers':int(db.execute('SELECT COUNT(*) FROM rp_live_heat_followers WHERE guild_id=? AND host_character_id=?',(str(gid),int(host))).fetchone()[0])}

def brand_visit(guild_id,user_id,brand_id,session_id,*,now=None):
    """已核准品牌擁有者代表品牌進房；同品牌同直播每小時只計一次。"""
    ts=int(time.time() if now is None else now)
    with transaction() as db:
        initialize(db)
        s=db.execute("SELECT * FROM rp_live_sessions WHERE guild_id=? AND id=? AND status='live' AND (ends_at IS NULL OR ends_at>?)",(str(guild_id),int(session_id),ts)).fetchone()
        if not s:raise ValueError('直播已結束或不存在')
        b=db.execute("SELECT business_id,owner_id,brand_tier FROM rp_business_entities WHERE guild_id=? AND business_id=? AND kind='brand' AND status='active'",(str(guild_id),str(brand_id))).fetchone()
        if not b:raise LookupError('找不到已核准的品牌')
        if str(b['owner_id'])!=str(user_id):raise PermissionError('只有品牌擁有者可以代表品牌進場')
        if str(s['owner_id'])==str(user_id):raise ValueError('不能用自己品牌替自己的直播刷進場熱度')
        bonus=BRAND_BONUSES.get(str(b['brand_tier']))
        if bonus is None:raise ValueError('品牌尚未設定正式規模')
        key=f'brand:{brand_id}:session:{session_id}:hour:{ts//3600}'
        return bonus if _record(db,guild_id,key,session_id,bonus,'brand_entry',ts) else 0
