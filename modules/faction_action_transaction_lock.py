"""第25階段：不可執行的唯一交易請求鎖，不預留或扣除經費。"""
from __future__ import annotations
from core.database import transaction


def initialize():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS faction_action_transaction_locks (
          guild_id TEXT NOT NULL, proposal_id INTEGER NOT NULL,
          request_key TEXT NOT NULL, quoted_cost INTEGER NOT NULL CHECK(quoted_cost>0),
          state TEXT NOT NULL DEFAULT 'blocked' CHECK(state IN ('blocked','cancelled')),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(guild_id,proposal_id), UNIQUE(guild_id,request_key))""")


def record_blocked_lock(guild_id, proposal_id, request_key, quoted_cost):
    gid,pid,key=str(guild_id),int(proposal_id),str(request_key).strip()
    if pid<=0 or not 1<=len(key)<=120 or isinstance(quoted_cost,bool) or not isinstance(quoted_cost,int) or quoted_cost<=0:
        raise ValueError('提案、金額或請求編號不合法。')
    initialize()
    with transaction() as db:
        existing=db.execute('SELECT * FROM faction_action_transaction_locks WHERE guild_id=? AND (proposal_id=? OR request_key=?)',(gid,pid,key)).fetchall()
        if existing:
            if len(existing)!=1 or int(existing[0]['proposal_id'])!=pid or existing[0]['request_key']!=key or int(existing[0]['quoted_cost'])!=quoted_cost:
                raise ValueError('此提案或請求已被不同的交易鎖占用。')
            return {'created':False,'state':existing[0]['state'],'execution_enabled':False}
        db.execute('INSERT INTO faction_action_transaction_locks(guild_id,proposal_id,request_key,quoted_cost) VALUES(?,?,?,?)',(gid,pid,key,quoted_cost))
        return {'created':True,'state':'blocked','execution_enabled':False}


def cancel_blocked_lock(guild_id, proposal_id):
    initialize()
    with transaction() as db:
        result=db.execute("UPDATE faction_action_transaction_locks SET state='cancelled' WHERE guild_id=? AND proposal_id=? AND state='blocked'",(str(guild_id),int(proposal_id)))
        return result.rowcount==1
