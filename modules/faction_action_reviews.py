"""Phase 19: faction action double approval ledger. No money or effects.

Call submit_review only from a Discord interaction after binding actor_owner_id to
interaction.user.id. The reviewer must own the CURRENT leader/deputy character.
"""
from __future__ import annotations

from core.database import transaction
from modules import faction_core
from modules.faction_actions import initialize as initialize_actions
from modules.faction_action_preflight import initialize as initialize_preflight


def initialize():
    initialize_preflight()
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS faction_action_reviews (
          guild_id TEXT NOT NULL, proposal_id INTEGER NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('leader','deputy')),
          reviewer_character_id INTEGER NOT NULL, reviewer_owner_id TEXT NOT NULL,
          decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(guild_id,proposal_id,role),
          UNIQUE(guild_id,proposal_id,reviewer_owner_id)
        )''')


def _officers(db, gid, fid):
    period = faction_core._roll(db, gid)
    row = db.execute('''SELECT leader_id,deputy_id FROM faction_leaders
       WHERE guild_id=? AND faction_id=? AND period=?''',
       (gid,fid,period-1)).fetchone() if period > 1 else None
    if not row or not row['leader_id'] or not row['deputy_id']:
        raise ValueError('å°æªé¸åºææçèå¤§èå¯æã')
    officers = {}
    for role in ('leader','deputy'):
        cid = int(row[role+'_id'])
        owner = db.execute('''SELECT c.owner_id FROM characters c
           JOIN faction_members m ON m.guild_id=c.guild_id AND m.character_id=c.id
           WHERE c.guild_id=? AND c.id=? AND c.status='active'
             AND m.faction_id=?''',(gid,cid,fid)).fetchone()
        if not owner:
            raise ValueError('ç®åå¹¹é¨å·²ä¸å·åææé£çè³æ ¼ã')
        officers[role] = (cid,str(owner['owner_id']))
    if officers['leader'][1] == officers['deputy'][1]:
        raise ValueError('èå¤§èå¯æä¸å¾ç±åä¸ Discord å¸³èå®æéäººæ ¸åã')
    return officers


def submit_review(guild_id, proposal_id, actor_owner_id, decision):
    """Record exactly one decision per officer; do NOT execute an action."""
    if decision not in ('approve','reject'):
        raise ValueError('åªè½æ¹åææçµã')
    initialize()
    gid, pid, actor = str(guild_id),int(proposal_id),str(actor_owner_id)
    with transaction() as db:
        p=db.execute('''SELECT p.*, c.owner_id AS proposer_owner FROM faction_action_proposals p
          JOIN characters c ON c.guild_id=p.guild_id AND c.id=p.applicant_character_id
          WHERE p.guild_id=? AND p.id=?''',(gid,pid)).fetchone()
        if not p or p['status']!='draft':
            raise ValueError('ææ¡ä¸å­å¨æå·²ç¶ééã')
        if actor == str(p['proposer_owner']):
            raise ValueError('ææ¡äººä¸å¾å¯©æ ¸èªå·±çè¡åã')
        configured=db.execute('''SELECT 1 FROM faction_action_preflights
          WHERE guild_id=? AND proposal_id=?''',(gid,pid)).fetchone()
        if not configured:
            raise ValueError('è«åå®æç®æ¨è§è²ãç­ç´åææé æª¢ã')
        officers=_officers(db,gid,int(p['faction_id']))
        matches=[(role,cid) for role,(cid,owner) in officers.items() if owner==actor]
        if len(matches)!=1:
            raise ValueError('åªæç®åèå¤§èå¯æå¯ä»¥å¯©æ ¸ã')
        role,cid=matches[0]
        prior=db.execute('''SELECT decision,reviewer_owner_id FROM faction_action_reviews
          WHERE guild_id=? AND proposal_id=? AND role=?''',(gid,pid,role)).fetchone()
        if prior:
            if prior['decision']==decision and prior['reviewer_owner_id']==actor:
                return review_state_in_db(db,gid,pid)
            raise ValueError('æ­¤å¹¹é¨å·²å®æå¯©æ ¸ï¼ä¸è½æ¹ç¥¨ã')
        db.execute('''INSERT INTO faction_action_reviews
          (guild_id,proposal_id,role,reviewer_character_id,reviewer_owner_id,decision)
          VALUES (?,?,?,?,?,?)''',(gid,pid,role,cid,actor,decision))
        return review_state_in_db(db,gid,pid)


def review_state_in_db(db,gid,pid):
    rows=db.execute('''SELECT role,reviewer_character_id,reviewer_owner_id,decision
       FROM faction_action_reviews WHERE guild_id=? AND proposal_id=?''',(gid,pid)).fetchall()
    decisions={r['role']:dict(r) for r in rows}
    return {'proposal_id':pid,'leader':decisions.get('leader'),
            'deputy':decisions.get('deputy'),
            'approved_by_both':all(decisions.get(k,{}).get('decision')=='approve'
                                   for k in ('leader','deputy')),
            'rejected':any(r['decision']=='reject' for r in rows),
            'execution_enabled':False}


def get_review_state(guild_id,proposal_id):
    initialize()
    with transaction() as db:
        return review_state_in_db(db,str(guild_id),int(proposal_id))
