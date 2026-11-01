"""陣營行動第22階段：唯讀金庫驗證，不扣款、不鎖定資金。"""
from __future__ import annotations

from core.database import transaction


def inspect_treasury(guild_id, faction_id, quoted_cost):
    """查看目前經費是否足夠。結果僅作預檢，不能作為稍後扣款的保證。"""
    cost = int(quoted_cost)
    if cost <= 0 or isinstance(quoted_cost, bool) or cost != quoted_cost:
        raise ValueError('費用必須為正整數。')
    with transaction() as db:
        row = db.execute('''SELECT pool FROM faction_groups
            WHERE guild_id=? AND id=?''', (str(guild_id), int(faction_id))).fetchone()
        if row is None:
            raise ValueError('找不到陣營金庫。')
        balance = int(row['pool'])
    return {'faction_id': int(faction_id), 'quoted_cost': cost,
            'available_balance': balance, 'sufficient': balance >= cost,
            'reserved': False, 'execution_enabled': False,
            'notice': '僅為目前餘額快照；正式扣款時仍須在同一筆交易重新驗證。'}


def inspect_proposal_treasury(guild_id, proposal_id):
    """從提案取得所屬陣營與結構化費用，避免呼叫者任意指定報價。"""
    with transaction() as db:
        row = db.execute('''SELECT p.faction_id, s.quoted_cost
            FROM faction_action_proposals p
            JOIN faction_action_preflights s
              ON s.guild_id=p.guild_id AND s.proposal_id=p.id
            WHERE p.guild_id=? AND p.id=? AND p.status='draft' ''',
            (str(guild_id), int(proposal_id))).fetchone()
        if row is None:
            raise ValueError('提案不存在、已關閉或尚未完成預檢。')
    return inspect_treasury(guild_id, row['faction_id'], row['quoted_cost'])
