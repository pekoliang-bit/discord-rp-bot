"""玩家申請核准後的場所行動擴充資料；不修改原有預設 JSON。

只有正式 /審核 核准事務可以寫入 approved=1。
讀取方 /行動 只會取經過 validate_action 的核准項目。
"""
from __future__ import annotations

import json

from core.database import get_connection
from modules.action_reward_rules import validate_action


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_channel_action_additions(
        guild_id TEXT NOT NULL, channel_id TEXT NOT NULL,
        channel_name TEXT NOT NULL, category TEXT NOT NULL,
        name TEXT NOT NULL, application_id INTEGER NOT NULL,
        action_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id, channel_id, category, name),
        UNIQUE(guild_id, application_id, category, name)
    )''')


def materialize(proposal: dict) -> dict:
    category = proposal['category']
    price = int(proposal['base_coins'])
    out = {
        'name': str(proposal['name']).strip(),
        'category': category, 'approved': True,
        'cost_coins': 0 if category == '打工' else price,
        'reward_coins': price if category == '打工' else 0,
        'action_points_cost': 0 if category == '課程' else 1,
        'attribute': None, 'attribute_increase': 0,
        'stat_rewards': list(proposal.get('stat_rewards', [])),
        'point_rewards': {}, 'notes': str(proposal.get('notes') or ''),
    }
    ok, reason = validate_action(out)
    if not ok:
        raise ValueError(f'行動 {out["name"]} 的數值規則不合格：{reason}')
    return out


def approve(db, application, guild):
    """被 /審核 既有事務呼叫；成功後與案件狀態一起提交。"""
    if application['kind'] != 'action_expand':
        raise ValueError('不是行動擴充案件')
    payload = json.loads(application['details'])
    if payload.get('schema') != 'action_application_v1' or payload.get('pricing') != 'actual_roll_b':
        raise ValueError('申請格式或計價方式無效')
    metadata = payload.get('metadata')
    if not isinstance(metadata, dict):
        raise ValueError('缺少申請頻道資訊')
    channel_id = str(metadata.get('channel_id') or '')
    if not channel_id.isdecimal():
        raise ValueError('缺少正確的頻道 ID')
    channel = guild.get_channel(int(channel_id)) if guild else None
    if channel is None or str(channel.guild.id) != str(application['guild_id']):
        raise ValueError('目標頻道已刪除或不屬於本伺服器')
    actions = payload.get('actions')
    if not isinstance(actions, list) or not 1 <= len(actions) <= 50:
        raise ValueError('申請行動清單不可空白或超過50項')
    initialize(db)
    seen = set()
    for proposal in actions:
        if not isinstance(proposal, dict):
            raise ValueError('申請行動格式無效')
        action = materialize(proposal)
        key = (action['category'], action['name'])
        if key in seen:
            raise ValueError('申請內有重複的同類型行動名稱')
        seen.add(key)
        # 只查原始 JSON 預設場所；新增資料的重複由同一交易中唯一鍵阻止。
        # 禁止在 SQLite 寫入交易內開第二個連線查詢，避免資料庫鎖定。
        try:
            from modules import (action_location_defaults, leisure_location_defaults,
                training_location_defaults, arts_location_defaults,
                film_location_defaults, business_location_defaults)
            bases = [provider.lookup_channel(application['guild_id'], channel_id)
                     for provider in (action_location_defaults, leisure_location_defaults,
                                      training_location_defaults, arts_location_defaults,
                                      film_location_defaults, business_location_defaults)]
        except (ValueError, TypeError, KeyError):
            bases = []
        if any(base and any(a.get('category') == key[0] and a.get('name') == key[1]
                            for a in base.get('actions', [])) for base in bases):
            raise ValueError(f'此頻道已有 {key[0]}「{key[1]}」')
        db.execute('''INSERT INTO rp_channel_action_additions
            (guild_id,channel_id,channel_name,category,name,application_id,action_json)
            VALUES(?,?,?,?,?,?,?)''',
            (str(application['guild_id']),channel_id,str(channel.name),key[0],key[1],
             int(application['id']),json.dumps(action,ensure_ascii=False,sort_keys=True)))
    return len(actions)


def list_channel_actions(guild_id, channel_id):
    with get_connection() as db:
        present = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_channel_action_additions'").fetchone()
        if not present:
            return [], None
        records = db.execute('''SELECT action_json, channel_name FROM rp_channel_action_additions
            WHERE guild_id=? AND channel_id=? ORDER BY application_id, category, name''',
            (str(guild_id),str(channel_id))).fetchall()
    actions = []
    for record in records:
        try:
            action=json.loads(record['action_json'])
            if action.get('approved') is True and validate_action(action)[0]:
                actions.append(action)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    return actions, (str(records[0]['channel_name']) if records else None)
