"""原子化場所行動結算（僅核准項目；不把體力當成行動值）。

本階段消耗獨立的付費行動值帳本。沒有付費點數、或尚未實作的
一般免費行動值，不會推測、透支或改動角色 stamina。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from core.database import transaction, get_or_create_account
from core.economy import _payment
from modules.action_reward_rules import validate_action, RESTRICTED_DIRECT_STATS
from modules.leaderboard_points_storage import initialize as initialize_points
from modules.leaderboard_points_catalog import validate_target


class ActionExecutionError(ValueError):
    pass


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_location_action_events (
        guild_id TEXT NOT NULL,
        request_key TEXT NOT NULL,
        character_id INTEGER NOT NULL,
        action_fingerprint TEXT NOT NULL,
        location_key TEXT NOT NULL,
        action_name TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, request_key)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS rp_action_professional_points (
        guild_id TEXT NOT NULL,
        character_id INTEGER NOT NULL,
        balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
        PRIMARY KEY(guild_id, character_id)
    )""")


def _fingerprint(location_key, action):
    raw=json.dumps([location_key, action], sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _spend_paid(db, guild_id, character_id, quantity, now):
    if quantity == 0:
        return
    # 共用商城 paid_action_packs；先過期、再按最早到期日扣除。
    # 不默認體力是免費行動值，不自動建立付費行動包。
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paid_action_packs'").fetchone()
    if not exists:
        raise ActionExecutionError('付費行動值尚未初始化，不能扣除行動值')
    rows=db.execute("""SELECT id, remaining FROM paid_action_packs
      WHERE guild_id=? AND character_id=? AND expires_at>? AND remaining>0
      ORDER BY expires_at, id""", (guild_id, character_id, now)).fetchall()
    if sum(int(r['remaining']) for r in rows)<quantity:
        raise ActionExecutionError('目前可驗證的付費行動值不足，免費行動值尚未接入')
    left=quantity
    for row in rows:
        used=min(int(row['remaining']),left)
        if used:
            db.execute('UPDATE paid_action_packs SET remaining=remaining-? WHERE id=? AND remaining>=?',
                (used,int(row['id']),used))
            left-=used
        if left==0:
            break


def _award_pool(db, guild_id, character_id, pool, amount):
    if amount <= 0:
        return
    if pool == '專業點數':
        db.execute("""INSERT INTO rp_action_professional_points (guild_id, character_id, balance)
          VALUES (?, ?, ?) ON CONFLICT(guild_id, character_id)
          DO UPDATE SET balance=balance+excluded.balance""", (guild_id, character_id, amount))
        return
    pool_id={'自由點數':'free', '技能點數':'skill'}.get(pool)
    if pool_id is None:
        raise ActionExecutionError('未知點數池')
    initialize_points(db)
    db.execute("""INSERT INTO lb_point_balances(guild_id, character_id, pool, balance)
        VALUES(?,?,?,?) ON CONFLICT(guild_id,character_id,pool)
        DO UPDATE SET balance=balance+excluded.balance""", (guild_id, character_id, pool_id, amount))


def execute_action(guild_id, user_id, character_id, location_key, action, request_key):
    """一次完成扣費、耗點、獎勵；相同 request_key 重試不重複發放。"""
    if not isinstance(action, dict) or action.get('approved') is not True or action.get('formal_notice') is True:
        raise ActionExecutionError('此行動尚未核准，無法執行')
    valid, reason=validate_action(action)
    if not valid:
        raise ActionExecutionError(reason)
    if not isinstance(request_key, str) or not 1 <= len(request_key.strip()) <= 150:
        raise ActionExecutionError('請求識別碼無效')
    if not isinstance(location_key, str) or not location_key.strip():
        raise ActionExecutionError('場所識別碼無效')
    guild_id=str(guild_id)
    character_id=int(character_id)
    fp=_fingerprint(location_key, action)
    with transaction() as db:
        initialize(db)
        char=db.execute("""SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?""",
                        (guild_id,character_id)).fetchone()
        if char is None or char['status']!='active':
            raise ActionExecutionError('角色不存在或尚未啟用')
        if str(char['owner_id']) != str(user_id):
            raise PermissionError('不能替其他玩家的角色執行行動')
        old=db.execute("""SELECT character_id, action_fingerprint, result_json FROM rp_location_action_events
          WHERE guild_id=? AND request_key=?""",(guild_id, request_key)).fetchone()
        if old is not None:
            if int(old['character_id']) != character_id or old['action_fingerprint'] != fp:
                raise ActionExecutionError('此請求編號已用於其他行動')
            return dict(json.loads(old['result_json']), duplicated=True)
        ap=int(action.get('action_points_cost',0))
        _spend_paid(db,guild_id,character_id,ap,datetime.now(timezone.utc).isoformat())
        cost=int(action.get('cost_coins',0))
        earn=int(action.get('reward_coins',0))
        if cost or earn:
            wallet=get_or_create_account(db,guild_id,'character',character_id,'wallet')
            treasury=get_or_create_account(db,guild_id,'system','location_actions','treasury')
            if cost:
                _payment(db,guild_id,'location-action-cost:'+request_key,wallet,treasury,cost,'場所行動費用')
            if earn:
                _payment(db,guild_id,'location-action-reward:'+request_key,treasury,wallet,earn,'場所打工薪資')
        attr=action.get('attribute')
        increase=int(action.get('attribute_increase',0))
        if increase:
            name=str(action.get('skill_name') or attr)
            if attr in RESTRICTED_DIRECT_STATS or name in RESTRICTED_DIRECT_STATS:
                raise ActionExecutionError('禁止直接增加特殊管制數值')
            try:
                validate_target('skill','skill',name)
            except ValueError:
                validate_target('free','attribute',name)
                db.execute("""INSERT INTO attributes(guild_id,character_id,name,value)
                  VALUES(?,?,?,?) ON CONFLICT(guild_id,character_id,name)
                  DO UPDATE SET value=value+excluded.value,version=version+1""",
                  (guild_id,character_id,name,increase))
            else:
                db.execute("""INSERT INTO character_skills(guild_id,character_id,skill_name,skill_value)
                  VALUES(?,?,?,?) ON CONFLICT(guild_id,character_id,skill_name)
                  DO UPDATE SET skill_value=skill_value+excluded.skill_value""",
                  (guild_id,character_id,name,increase))
        health=int(action.get('health_delta',0))
        if health:
            # 不清楚角色正式健康值來源時，不可無聲略過懲罰或胡亂改寫。
            raise ActionExecutionError('此行動含健康變動，尚待健康屬性正式連動')
        for pool,amount in (action.get('point_rewards') or {}).items():
            _award_pool(db,guild_id,character_id,pool,int(amount))
        result={'character_id':character_id,'action_name':str(action.get('name','未命名行動')),
                'cost_coins':cost,'reward_coins':earn,'action_points_spent':ap,
                'attribute_increase':increase,'point_rewards':action.get('point_rewards') or {},
                'duplicated':False}
        db.execute("""INSERT INTO rp_location_action_events(
          guild_id,request_key,character_id,action_fingerprint,location_key,action_name,result_json)
          VALUES(?,?,?,?,?,?,?)""",
          (guild_id,request_key,character_id,fp,location_key,result['action_name'],json.dumps(result,ensure_ascii=False)))
        return result
