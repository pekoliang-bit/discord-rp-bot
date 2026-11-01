"""第25階段：同一資料庫交易中的金庫餘額與報價快照；不扣款。"""
from __future__ import annotations
from core.database import transaction


def inspect_atomic_snapshot(guild_id, proposal_id):
    gid,pid=str(guild_id),int(proposal_id)
    with transaction() as db:
        row=db.execute("""SELECT p.faction_id,p.status AS proposal_status,s.quoted_cost,
             f.pool AS current_pool FROM faction_action_proposals p
             JOIN faction_action_preflights s ON s.guild_id=p.guild_id AND s.proposal_id=p.id
             JOIN faction_groups f ON f.guild_id=p.guild_id AND f.id=p.faction_id
             WHERE p.guild_id=? AND p.id=?""",(gid,pid)).fetchone()
        if row is None or row['proposal_status']!='draft':
            raise ValueError('找不到可預檢的陣營提案。')
        cost=int(row['quoted_cost']); pool=int(row['current_pool'])
        if cost<=0 or pool<0:
            raise ValueError('金庫或報價數值異常。')
        return {'faction_id':int(row['faction_id']),'quoted_cost':cost,
                'pool_balance':pool,'sufficient':pool>=cost,
                'reserved':False,'deducted':False,'execution_enabled':False}
