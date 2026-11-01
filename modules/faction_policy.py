"""第十七階段陣營正式政策核心。

只提供規則驗證與計算；尚未實作核准、扣款、暗害效果。
不在此擅自設定未經確認的能力係數、曝光機率、期限、上限。
"""
from __future__ import annotations
from dataclasses import dataclass
from core.database import transaction

ACTION_COSTS = {1: 10000, 2: 50000, 3: 100000}
BASE_SUCCESS_RATE = 40.0
MIN_SUCCESS_RATE = 5.0
MAX_SUCCESS_RATE = 90.0
SAME_EFFECT_COOLDOWN_HOURS = 72
TREASURY_DUAL_APPROVAL_THRESHOLD = 100000


def cost_for_level(level: int) -> int:
    try:
        return ACTION_COSTS[int(level)]
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('暗害等級必須為 1、2 或 3。') from exc


def success_rate(ability_adjustment: float, investment_adjustment: float, defense_adjustment: float = 0) -> float:
    """百分點制；外部經正式規則核准後才提供加減值。"""
    return max(MIN_SUCCESS_RATE, min(MAX_SUCCESS_RATE,
        BASE_SUCCESS_RATE + float(ability_adjustment) + float(investment_adjustment) - float(defense_adjustment)))


def requires_dual_approval(amount: int, *, action_type: str | None = None) -> bool:
    return action_type in ('sabotage', 'defense') or int(amount) >= TREASURY_DUAL_APPROVAL_THRESHOLD


def check_approval(leader_id, deputy_id, proposer_id, approvals) -> bool:
    """兩名幹部均核准且提案人不可為任一審核人。"""
    if not leader_id or not deputy_id or str(leader_id) == str(deputy_id):
        return False
    required = {str(leader_id), str(deputy_id)}
    return str(proposer_id) not in required and required <= {str(a) for a in approvals}


def validate_target(source_faction_id, target_faction_id, action_type: str) -> None:
    if action_type not in ('sabotage', 'defense'):
        raise ValueError('未知行動類型。')
    if source_faction_id is None or target_faction_id is None:
        raise ValueError('目標必須有有效陣營。')
    same = int(source_faction_id) == int(target_faction_id)
    if (action_type == 'sabotage' and same) or (action_type == 'defense' and not same):
        raise ValueError('暗害只能對其他陣營，防護只能對本陣營。')


def cooldown_allows(last_success_time, now) -> bool:
    """同目標同類效果最近一次生效之後至少經過 72 小時。"""
    from datetime import timedelta
    return last_success_time is None or now - last_success_time >= timedelta(hours=SAME_EFFECT_COOLDOWN_HOURS)


def initialize_bonus_caps():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS faction_bonus_caps (
            guild_id TEXT NOT NULL, effect_key TEXT NOT NULL, cap INTEGER NOT NULL CHECK(cap>=0),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,effect_key))""")


def get_bonus_cap(guild_id, effect_key, *, db=None):
    """尚未設定回傳 None；不得擅自假設某個總上限。"""
    if db is None:
        initialize_bonus_caps()
        with transaction() as conn:
            return get_bonus_cap(guild_id, effect_key, db=conn)
    row=db.execute('SELECT cap FROM faction_bonus_caps WHERE guild_id=? AND effect_key=?',
                   (str(guild_id),str(effect_key))).fetchone()
    return int(row[0]) if row else None


def clamp_bonus(guild_id, effect_key, raw_bonus, *, db=None):
    raw=max(0,int(raw_bonus))
    cap=get_bonus_cap(guild_id,effect_key,db=db)
    return min(raw,cap) if cap is not None else raw


def set_bonus_cap(guild_id, effect_key, cap):
    """供已驗證群主權限的管理介面呼叫；本函式自身不檢驗 Discord 權限。"""
    from modules.faction_shop_effects import ALLOWED_KEYS
    if effect_key not in ALLOWED_KEYS or not isinstance(cap,int) or cap < 0:
        raise ValueError('加成項目或上限不合法。')
    initialize_bonus_caps()
    with transaction() as db:
        db.execute("""INSERT INTO faction_bonus_caps(guild_id,effect_key,cap) VALUES(?,?,?)
            ON CONFLICT(guild_id,effect_key) DO UPDATE SET cap=excluded.cap,updated_at=CURRENT_TIMESTAMP""",
            (str(guild_id),effect_key,cap))
