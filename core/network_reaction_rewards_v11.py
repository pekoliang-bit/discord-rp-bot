"""Network post emoji settlement with original-roll rollback and withdrawal penalty.

Transactions are atomic per guild/thread/Discord reactor/emoji.  No user-supplied amounts.
"""
from __future__ import annotations
import json
import random
from core.database import transaction
from core.world_interaction_rules import reaction_rules,reaction_effects

MAP={'fans':('character_system_tracking','loyal_fans'),
     'haters':('character_system_tracking','active_antis'),
     'heat':('attributes','流量熱度'), 'favor':('attributes','路人緣')}

def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_network_reaction_settlements_v11 (
      guild_id TEXT NOT NULL, thread_id TEXT NOT NULL, reactor_id TEXT NOT NULL,
      emoji TEXT NOT NULL, author_character_id INTEGER NOT NULL,
      applied_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
      withdrawn_by TEXT, penalty_json TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(guild_id,thread_id,reactor_id,emoji))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_network_reaction_penalties_v11 (
      guild_id TEXT NOT NULL, thread_id TEXT NOT NULL, reactor_id TEXT NOT NULL,
      emoji TEXT NOT NULL, character_id INTEGER NOT NULL, metric TEXT NOT NULL,
      amount INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(guild_id,thread_id,reactor_id,emoji))''')
    db.execute('''CREATE TABLE IF NOT EXISTS character_system_tracking (
      guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,
      loyal_fans INTEGER NOT NULL DEFAULT 0,active_antis INTEGER NOT NULL DEFAULT 0,
      public_awareness INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(guild_id,character_id))''')

def _add(db,gid,cid,metric,amount):
    if metric not in MAP:raise ValueError('不支援的社群數值')
    table,name=MAP[metric]
    if table=='character_system_tracking':
        db.execute('INSERT OR IGNORE INTO character_system_tracking(guild_id,character_id) VALUES (?,?)',(gid,cid))
        before=db.execute(f'SELECT {name} FROM character_system_tracking WHERE guild_id=? AND character_id=?',(gid,cid)).fetchone()
        previous=int(before[0])
        after=max(0,previous+amount)
        db.execute(f'UPDATE character_system_tracking SET {name}=?,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND character_id=?',(after,gid,cid))
    else:
        db.execute('INSERT OR IGNORE INTO attributes(guild_id,character_id,name,value) VALUES (?,?,?,0)',(gid,cid,name))
        previous=int(db.execute('SELECT value FROM attributes WHERE guild_id=? AND character_id=? AND name=?',(gid,cid,name)).fetchone()[0])
        after=max(0,previous+amount)
        db.execute('UPDATE attributes SET value=?,version=version+1 WHERE guild_id=? AND character_id=? AND name=?',(after,gid,cid,name))
    return after-previous

def add(guild_id,thread_id,reactor_id,emoji,*,rng=None):
    gid,tid,uid=str(guild_id),str(thread_id),str(reactor_id)
    rng=rng or random.SystemRandom()
    cfg=reaction_rules(gid)
    with transaction() as db:
        setup(db)
        post=db.execute('''SELECT p.character_id,c.owner_id FROM rp_network_posts p
            JOIN characters c ON c.guild_id=p.guild_id AND c.id=p.character_id
            WHERE p.guild_id=? AND p.thread_id=? AND c.status='active' ''',(gid,tid)).fetchone()
        if not post:return 'not_post'
        if str(post['owner_id'])==uid:return 'self_reaction'
        existing=db.execute('SELECT active FROM rp_network_reaction_settlements_v11 WHERE guild_id=? AND thread_id=? AND reactor_id=? AND emoji=?',(gid,tid,uid,str(emoji))).fetchone()
        if existing:return 'already' # even if withdrawn: cannot repeatedly roll again
        cid=int(post['character_id'])
        effects=reaction_effects(cfg,str(emoji))
        changes={}
        for metric,direction in effects.items():
            value=1 if effects=={'heat':1} and not any(str(emoji) in __import__('core.world_interaction_rules',fromlist=['split_emojis']).split_emojis(cfg[k]) for k in ('fans_up','fans_down','heat_up','heat_down','haters_up','haters_down','favor_up','favor_down')) else rng.randint(cfg['random_min'],cfg['random_max'])
            changes[metric]=_add(db,gid,cid,metric,direction*value)
        db.execute('''INSERT INTO rp_network_reaction_settlements_v11
         (guild_id,thread_id,reactor_id,emoji,author_character_id,applied_json)
         VALUES (?,?,?,?,?,?)''',(gid,tid,uid,str(emoji),cid,json.dumps(changes,ensure_ascii=False)))
        return changes

def withdraw(guild_id,thread_id,reactor_id,emoji,*,actor_id=None,penalty=True,authorized=False,rng=None):
    """Only a verified self-withdrawal should set penalty=True.

    Passive Discord raw removals cannot prove which moderator clicked, so those
    are rolled back WITHOUT misattributing penalties to the reactor.
    """
    gid,tid,uid=str(guild_id),str(thread_id),str(reactor_id)
    actor=str(actor_id) if actor_id is not None else uid
    rng=rng or random.SystemRandom()
    cfg=reaction_rules(gid)
    if penalty and actor!=uid and not authorized:raise PermissionError('移除別人的反應需要 Discord 管理員授權')
    with transaction() as db:
        setup(db)
        row=db.execute('SELECT * FROM rp_network_reaction_settlements_v11 WHERE guild_id=? AND thread_id=? AND reactor_id=? AND emoji=?',(gid,tid,uid,str(emoji))).fetchone()
        if not row or not row['active']:return None
        changes=json.loads(row['applied_json']);cid=int(row['author_character_id'])
        for metric,applied in changes.items():_add(db,gid,cid,metric,-int(applied))
        applied_penalty={}
        if penalty and rng.randint(1,100)<=cfg['withdraw_penalty_chance']:
            actor_row=db.execute("SELECT id FROM characters WHERE guild_id=? AND owner_id=? AND status='active' ORDER BY id LIMIT 1",(gid,actor)).fetchone()
            if actor_row:
                penalty_cid=int(actor_row['id']);metric=rng.choice(('fans','heat','haters','favor'))
                num=rng.randint(cfg['withdraw_penalty_min'],cfg['withdraw_penalty_max'])
                actual=_add(db,gid,penalty_cid,metric,num if metric=='haters' else -num)
                applied_penalty={'character_id':penalty_cid,'metric':metric,'amount':actual}
                db.execute('''INSERT INTO rp_network_reaction_penalties_v11 VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)''',(gid,tid,uid,str(emoji),penalty_cid,metric,actual))
        db.execute('''UPDATE rp_network_reaction_settlements_v11 SET active=0,withdrawn_by=?,penalty_json=?
            WHERE guild_id=? AND thread_id=? AND reactor_id=? AND emoji=?''',
            (actor,json.dumps(applied_penalty,ensure_ascii=False),gid,tid,uid,str(emoji)))
        return {'reverted':changes,'penalty':applied_penalty}
