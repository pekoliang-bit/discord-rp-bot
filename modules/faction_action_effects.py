"""陣營第20階段：限時行動效果帳本（安全預備版）。

不提供對外的施加效果函式；不得用這份帳本直接改動角色屬性。
正式結算器開放後才可在同筆交易中插入實際效果及扣款紀錄。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.database import transaction

EFFECT_TYPES = frozenset({
    'public_favor', 'public_relations', 'popularity',
    'cooperation_success', 'work_success', 'event_success',
})


def initialize():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS faction_action_effects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            proposal_id INTEGER NOT NULL,
            action_type TEXT NOT NULL CHECK(action_type IN ('sabotage','defense')),
            target_character_id INTEGER NOT NULL,
            effect_key TEXT NOT NULL,
            effect_value INTEGER NOT NULL,
            start_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id, proposal_id)
        )''')
        db.execute('''CREATE INDEX IF NOT EXISTS faction_action_effects_target
           ON faction_action_effects(guild_id,target_character_id,effect_key,start_at,expires_at)''')


def _utc(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError('時間必須包含時區。')
        return value.astimezone(timezone.utc)
    raise TypeError('時間必須是具時區的 datetime。')


def _iso(value):
    return _utc(value).isoformat(timespec='seconds')


def is_same_type_cooldown_clear(guild_id, target_character_id, effect_key, *, at=None):
    """同一角色同一種暗害效果，72 小時內至多成功承受一次。

    這裡檢查的是已登記成功的暗害，不會對失敗提案啟動冷卻。
    正式結算仍須在寫入效果的同一筆資料庫交易內再次檢查，以避免併發競態。
    """
    if effect_key not in EFFECT_TYPES:
        raise ValueError('未知效果類型。')
    initialize()
    now = _utc(at)
    lower = _iso(now - timedelta(hours=72))
    now_text = _iso(now)
    with transaction() as db:
        row = db.execute('''SELECT 1 FROM faction_action_effects
            WHERE guild_id=? AND target_character_id=? AND effect_key=?
              AND action_type='sabotage' AND start_at>? AND start_at<=?
              LIMIT 1''',
            (str(guild_id),int(target_character_id),effect_key,lower,now_text)).fetchone()
    return row is None


def list_active_effects(guild_id, target_character_id, *, at=None):
    """供將來效果計算器查詢，僅回傳有效、未撤銷紀錄。

    目前尚未提供授權的角色/陣營身份校驗，因此本函式只限內部
    受信任服務呼叫，不可直接接公開 Discord 按鈕。
    """
    initialize()
    now = _iso(_utc(at))
    with transaction() as db:
        return [dict(r) for r in db.execute('''SELECT id,proposal_id,action_type,
              target_character_id,effect_key,effect_value,start_at,expires_at
              FROM faction_action_effects WHERE guild_id=? AND target_character_id=?
              AND start_at<=? AND expires_at>? AND revoked_at IS NULL ORDER BY id''',
              (str(guild_id),int(target_character_id),now,now)).fetchall()]
