"""娛樂城效果與通知佇列（一般服務模組，非 Discord Extension）。

僅對已結算交易建效果事件；重複掃描不會重複入列。角色屬性、
傳聞、稱號的正式介面尚未接通時不擅自改寫其表格。
"""
from __future__ import annotations

import json
from typing import Any
from core.database import get_connection, transaction

EFFECT_DDL = (
    """CREATE TABLE IF NOT EXISTS casino_effect_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
      event_key TEXT NOT NULL, character_id INTEGER NOT NULL,
      event_kind TEXT NOT NULL, payload_json TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','failed')),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(guild_id,event_key))""",
    """CREATE TABLE IF NOT EXISTS casino_risk_reviews (
      guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
      review_date TEXT NOT NULL, stake_7d INTEGER NOT NULL,
      loss_7d INTEGER NOT NULL, play_count_7d INTEGER NOT NULL,
      risk_level TEXT NOT NULL, evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(guild_id,character_id,review_date))""",
    "CREATE INDEX IF NOT EXISTS idx_casino_effect_pending ON casino_effect_events(guild_id,status,id)",
)


def init_effects_schema() -> None:
    with transaction() as conn:
        for ddl in EFFECT_DDL:
            conn.execute(ddl)


def queue_settled_round_effects(guild_id: int | str, round_id: int) -> list[dict[str, Any]]:
    """建立待派送事件，不會直接公告或改數值；需要通知橋接器確認後處理。"""
    gid = str(guild_id)
    with transaction() as conn:
        row = conn.execute('''SELECT r.*,g.code,g.name,g.settings_json FROM casino_rounds r
          JOIN casino_games g ON g.guild_id=r.guild_id AND g.id=r.game_id
          WHERE r.guild_id=? AND r.id=?''', (gid,int(round_id))).fetchone()
        if row is None or row['status'] != 'settled':
            raise ValueError('僅能處理已結算的娛樂城紀錄')
        outcome = json.loads(row['outcome_json'] or '{}')
        if not isinstance(outcome, dict):
            raise ValueError('無效結算結果')
        settings = json.loads(row['settings_json'] or '{}')
        is_jackpot = bool(outcome.get('jackpot'))
        is_rare = row['game_kind']=='gacha' and outcome.get('rarity')=='UR'
        events = [('private_result', {'round_id':row['id'], 'game_name':row['name'],
                                       'reward_coins':row['reward_coins'], 'category':'private'})]
        if is_jackpot or is_rare:
            events.append(('major_win', {'round_id':row['id'], 'game_name':row['name'],
                                         'reward_coins':row['reward_coins'], 'category':'character_related',
                                         'rarity':outcome.get('rarity'), 'needs_stat_bridge':True}))
        for kind, payload in events:
            key=f'round:{round_id}:{kind}'
            conn.execute('''INSERT OR IGNORE INTO casino_effect_events
                (guild_id,event_key,character_id,event_kind,payload_json)
                VALUES(?,?,?,?,?)''', (gid,key,row['character_id'],kind,json.dumps(payload,ensure_ascii=False)))
        keys = [f'round:{round_id}:{kind}' for kind,_ in events]
        return [dict(r) for r in conn.execute('''SELECT * FROM casino_effect_events
           WHERE guild_id=? AND event_key IN (%s) ORDER BY id''' % ','.join('?'*len(keys)), [gid,*keys])]


def list_pending_events(guild_id: str | int, limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as conn:
        return [dict(r) for r in conn.execute('''SELECT * FROM casino_effect_events
            WHERE guild_id=? AND status='pending' ORDER BY id LIMIT ?''',
            (str(guild_id),max(1,min(int(limit),200))))]


def mark_event_completed(guild_id: str | int, event_id: int) -> bool:
    """僅供成功派送後的可信任通知工作者呼叫。"""
    with transaction() as conn:
        cur = conn.execute('''UPDATE casino_effect_events SET status='completed'
            WHERE guild_id=? AND id=? AND status='pending' ''', (str(guild_id),int(event_id)))
        return cur.rowcount == 1


def leaderboard(guild_id: str | int, board: str = 'profit', limit: int = 10) -> list[dict[str, Any]]:
    """只計金幣；獎勵物品不估價。"""
    if board not in ('profit','largest_win','total_winnings'):
        raise ValueError('不支援的排行榜')
    expr = {'profit':'SUM(reward_coins-ticket_price)',
            'largest_win':'MAX(reward_coins)',
            'total_winnings':'SUM(reward_coins)'}[board]
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(f'''SELECT character_id,{expr} AS score,
             COUNT(*) AS plays FROM casino_rounds WHERE guild_id=? AND status='settled'
             GROUP BY character_id ORDER BY score DESC,character_id ASC LIMIT ?''',
             (str(guild_id),max(1,min(int(limit),50))))]


def assess_risk(guild_id: str | int, character_id: int, *,
                moderate_stake: int = 200_000, high_stake: int = 500_000,
                severe_stake: int = 1_000_000) -> dict[str, Any]:
    """七日金幣風險快照；只記錄評估，不直接扣角色屬性。

    門檻為暫定可配置值，正式數值處分必須接入角色屬性與傳聞系統。
    """
    if not 0 < moderate_stake < high_stake < severe_stake:
        raise ValueError('風險門檻必須遞增')
    gid=str(guild_id)
    with transaction() as conn:
        data=conn.execute('''SELECT COUNT(*) AS n,
           COALESCE(SUM(ticket_price),0) AS stake,
           COALESCE(SUM(CASE WHEN status='settled' THEN reward_coins ELSE 0 END),0) AS rewards
           FROM casino_rounds WHERE guild_id=? AND character_id=? AND status!='cancelled'
           AND created_at>=datetime('now','-7 days')''', (gid,int(character_id))).fetchone()
        stake, count=int(data['stake']),int(data['n'])
        losses=max(0,stake-int(data['rewards']))
        level='severe' if stake>=severe_stake else 'high' if stake>=high_stake else 'moderate' if stake>=moderate_stake else 'normal'
        if losses>=high_stake and level=='moderate': level='high'
        if losses>=severe_stake: level='severe'
        conn.execute('''INSERT INTO casino_risk_reviews(guild_id,character_id,review_date,stake_7d,loss_7d,play_count_7d,risk_level)
         VALUES(?,?,date('now'),?,?,?,?) ON CONFLICT(guild_id,character_id,review_date) DO UPDATE SET
         stake_7d=excluded.stake_7d,loss_7d=excluded.loss_7d,
         play_count_7d=excluded.play_count_7d,risk_level=excluded.risk_level,
         evaluated_at=CURRENT_TIMESTAMP''', (gid,int(character_id),stake,losses,count,level))
        return {'guild_id':gid,'character_id':int(character_id),'plays_7d':count,
                'stake_7d':stake,'loss_7d':losses,'risk_level':level,
                'stat_penalty_applied':False,'rumor_created':False}
