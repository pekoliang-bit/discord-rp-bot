"""Phase 19: non-mutating final readiness check for faction actions.

Even when ready_for_policy is true, never charge, roll outcomes, notify, or apply
any effect: numeric balance/defense/expiry rules are not approved yet.
"""
from __future__ import annotations

from core.database import transaction
from modules.faction_action_reviews import initialize, _officers, review_state_in_db
from modules.faction_action_preflight import _check


def inspect_action(guild_id,proposal_id):
    initialize()
    gid,pid=str(guild_id),int(proposal_id)
    with transaction() as db:
        p=db.execute('''SELECT p.*, c.owner_id AS proposer_owner
          FROM faction_action_proposals p JOIN characters c
          ON c.guild_id=p.guild_id AND c.id=p.applicant_character_id
          WHERE p.guild_id=? AND p.id=?''',(gid,pid)).fetchone()
        if not p:
            raise ValueError('找不到提案。')
        if p['status']!='draft':
            raise ValueError('提案已取消或關閉。')
        spec=db.execute('''SELECT * FROM faction_action_preflights
          WHERE guild_id=? AND proposal_id=?''',(gid,pid)).fetchone()
        if not spec:
            raise ValueError('缺少結構化預檢設定。')
        # Check that the original proposer still owns an active member character,
        # target faction remains valid, and the quoted fee still matches policy.
        valid=_check(db,gid,pid,p['proposer_owner'],spec['target_character_id'],
                     spec['level'],spec['effect_key'])
        if int(spec['quoted_cost'])!=valid['quoted_cost']:
            raise ValueError('原報價已變動，需重新確認。')
        officers=_officers(db,gid,int(p['faction_id']))
        review=review_state_in_db(db,gid,pid)
        for role in ('leader','deputy'):
            vote=review[role]
            if vote is not None and (int(vote['reviewer_character_id'])!=officers[role][0]
                                     or vote['reviewer_owner_id']!=officers[role][1]):
                raise ValueError('幹部已更換，舊審核票不可繼續使用。')
        if str(p['proposer_owner']) in {officers['leader'][1],officers['deputy'][1]}:
            raise ValueError('此提案由幹部本人提出，依防自審規則不可雙人核准。')
        return {**valid,'reviews':review,
                'ready_for_policy':review['approved_by_both'] and not review['rejected'],
                'execution_enabled':False,
                'blocker':'暗害／防護係數、期限與實際結算尚未全部核定；禁止扣款或施加效果。'}
