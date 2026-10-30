"""公關已付費行動的唯讀評分彙總。

安裝：core/rumor_pr_score_summary.py（非 Discord extension）。
只讀取扣費與效果已配對的紀錄；發現遺漏或不一致時拒絕評級。
不計算未接入的公關能力／事件難度修正，也不發獎。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.database import get_connection


@dataclass(frozen=True)
class PRScoreSummary:
    guild_id: str
    rumor_id: int
    character_id: int
    action_count: int
    score: int
    total_coins_spent: int
    total_paid_actions_spent: int


def summarize_pr_score(guild_id: str | int, rumor_id: int, character_id: int) -> PRScoreSummary:
    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0 or type(character_id) is not int or character_id <= 0:
        raise ValueError('案件、角色或伺服器編號無效')

    with get_connection() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('rp_company_rumors', 'rp_rumor_pr_charges', 'rp_rumor_pr_effects'):
            if table not in tables:
                raise RuntimeError(f'缺少公關資料表：{table}')
        case = db.execute(
            'SELECT character_id FROM rp_company_rumors WHERE guild_id=? AND id=?',
            (gid, rumor_id),
        ).fetchone()
        if case is None or int(case['character_id']) != character_id:
            raise ValueError('找不到指定角色的傳聞案件')

        charges = db.execute('''
            SELECT c.id, c.strategy, c.resource, c.cost, c.effectiveness,
                   e.strategy AS effect_strategy, e.multiplier, e.effective_score
            FROM rp_rumor_pr_charges AS c
            LEFT JOIN rp_rumor_pr_effects AS e
              ON e.guild_id=c.guild_id AND e.action_id=c.id
             AND e.rumor_id=c.rumor_id AND e.character_id=c.character_id
            WHERE c.guild_id=? AND c.rumor_id=? AND c.character_id=?
            ORDER BY c.id
        ''', (gid, rumor_id, character_id)).fetchall()

        # 只要有孤兒效果紀錄也拒絕出分，以免漏算或污染。
        orphan = db.execute('''
            SELECT 1 FROM rp_rumor_pr_effects AS e
            LEFT JOIN rp_rumor_pr_charges AS c
              ON c.guild_id=e.guild_id AND c.id=e.action_id
            WHERE e.guild_id=? AND e.rumor_id=? AND e.character_id=?
              AND (c.id IS NULL OR c.rumor_id!=e.rumor_id
                   OR c.character_id!=e.character_id)
            LIMIT 1
        ''', (gid, rumor_id, character_id)).fetchone()
        if orphan:
            raise RuntimeError('公關效果存在無效的扣費關聯')

        score = coins = actions = 0
        for row in charges:
            if row['effective_score'] is None:
                raise RuntimeError(f"公關扣費 #{row['id']} 缺少效果紀錄")
            if row['strategy'] != row['effect_strategy']:
                raise RuntimeError('公關策略與效果紀錄不一致')
            if Decimal(str(row['effectiveness'])) != Decimal(str(row['multiplier'])):
                raise RuntimeError('公關行動與效果倍率不一致')
            value = int(row['effective_score'])
            cost = int(row['cost'])
            if value < 0 or cost <= 0:
                raise RuntimeError('公關行動數值不合法')
            score += value
            if row['resource'] == 'coins':
                coins += cost
            elif row['resource'] == 'paid_actions':
                actions += cost
            else:
                raise RuntimeError('未知的公關付款資源')

    return PRScoreSummary(gid, rumor_id, character_id, len(charges), score, coins, actions)
