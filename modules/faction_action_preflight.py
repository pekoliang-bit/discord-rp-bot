"""第十八階段：暗害／防護提案的結構化預檢（不扣款、不生效）。

僅為已存在草稿保存目標角色、行動等級、效果識別，並逐次驗證。
所有寫入必須經由預檢；此模組沒有正式審核或結算入口。
"""
from __future__ import annotations

from core.database import transaction
from modules.faction_actions import initialize as initialize_proposals
from modules.faction_policy import cost_for_level, validate_target


ALLOWED_EFFECTS = frozenset({
    'public_favor', 'public_relations', 'popularity',
    'cooperation_success', 'work_success', 'event_success',
})


def initialize():
    initialize_proposals()
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS faction_action_preflights (
            guild_id TEXT NOT NULL,
            proposal_id INTEGER NOT NULL,
            target_character_id INTEGER NOT NULL,
            effect_key TEXT NOT NULL,
            level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 3),
            quoted_cost INTEGER NOT NULL CHECK(quoted_cost > 0),
            checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,proposal_id)
        )''')


def _get_active_character_faction(db, gid, character_id):
    return db.execute('''SELECT m.faction_id FROM characters c
        JOIN faction_members m ON m.guild_id=c.guild_id AND m.character_id=c.id
        WHERE c.guild_id=? AND c.id=? AND c.status='active' ''',
        (gid, int(character_id))).fetchone()


def _check(db, gid, proposal_id, owner_id, target_character_id, level, effect_key):
    prop = db.execute('''SELECT id, faction_id, applicant_character_id, action_type, status
        FROM faction_action_proposals WHERE guild_id=? AND id=?''',
        (gid, int(proposal_id))).fetchone()
    if not prop or prop['status'] != 'draft':
        raise ValueError('提案不存在或已不是草稿。')
    source = db.execute('''SELECT m.faction_id FROM faction_members m
        JOIN characters c ON c.guild_id=m.guild_id AND c.id=m.character_id
        WHERE m.guild_id=? AND m.character_id=? AND c.owner_id=?
          AND c.status='active' AND c.character_type='player' ''',
        (gid, int(prop['applicant_character_id']), str(owner_id))).fetchone()
    if not source or int(source['faction_id']) != int(prop['faction_id']):
        raise ValueError('提案人已無此陣營的操作資格。')
    target = _get_active_character_faction(db, gid, target_character_id)
    if target is None:
        raise ValueError('目標角色必須已啟用並加入有效陣營。')
    validate_target(source['faction_id'], target['faction_id'], prop['action_type'])
    if effect_key not in ALLOWED_EFFECTS:
        raise ValueError('效果類型不在正式允許範圍內。')
    if prop['action_type'] == 'defense' and effect_key not in ALLOWED_EFFECTS:
        raise ValueError('防護類型不合法。')
    cost = cost_for_level(level)
    return {'proposal_id':int(prop['id']), 'action_type':prop['action_type'],
            'source_faction_id':int(source['faction_id']),
            'target_faction_id':int(target['faction_id']),
            'target_character_id':int(target_character_id), 'effect_key':effect_key,
            'level':int(level), 'quoted_cost':cost,
            'execution_enabled':False}


def save_preflight(guild_id, proposal_id, owner_id, target_character_id, level, effect_key):
    """驗證及儲存草稿的結構化參數；不建立付款或實際效果。"""
    initialize()
    gid = str(guild_id)
    with transaction() as db:
        result = _check(db,gid,proposal_id,owner_id,target_character_id,level,effect_key)
        db.execute('''INSERT INTO faction_action_preflights
            (guild_id,proposal_id,target_character_id,effect_key,level,quoted_cost)
            VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,proposal_id) DO UPDATE SET
            target_character_id=excluded.target_character_id,
            effect_key=excluded.effect_key,level=excluded.level,
            quoted_cost=excluded.quoted_cost,checked_at=CURRENT_TIMESTAMP''',
            (gid,int(proposal_id),int(target_character_id),effect_key,int(level),result['quoted_cost']))
        return result


def recheck_preflight(guild_id, proposal_id, owner_id):
    """審核前重新核對角色與陣營；此函式本身不構成批准。"""
    initialize()
    gid = str(guild_id)
    with transaction() as db:
        row = db.execute('''SELECT * FROM faction_action_preflights
            WHERE guild_id=? AND proposal_id=?''', (gid,int(proposal_id))).fetchone()
        if row is None:
            raise ValueError('請先完成提案的目標、效果及等級設定。')
        result = _check(db,gid,proposal_id,owner_id,row['target_character_id'],
                        row['level'],row['effect_key'])
        if row['quoted_cost'] != result['quoted_cost']:
            raise ValueError('行動費用已改變，請重新確認報價。')
        return result
