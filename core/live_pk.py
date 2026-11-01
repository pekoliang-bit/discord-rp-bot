"""Live PK matches. Consent-based, scored from actual recorded gifts; no minted rewards.

Only imported by /網路 UI. Every operation verifies guild and role ownership.
"""
from __future__ import annotations
import time
from core.database import transaction, get_connection
from core import live_stream as live

DDL = ("""CREATE TABLE IF NOT EXISTS rp_live_pk_matches (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
 challenger_session_id INTEGER NOT NULL, opponent_session_id INTEGER NOT NULL,
 challenger_character_id INTEGER NOT NULL, opponent_character_id INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL,
 expires_at INTEGER NOT NULL, started_at INTEGER, ended_at INTEGER,
 challenger_score INTEGER NOT NULL DEFAULT 0, opponent_score INTEGER NOT NULL DEFAULT 0,
 winner_character_id INTEGER,
 CHECK(status IN ('pending','active','declined','expired','ended','cancelled')),
 CHECK(challenger_session_id != opponent_session_id)
)""",
 "CREATE INDEX IF NOT EXISTS idx_pk_guild_status ON rp_live_pk_matches(guild_id,status)")


def _init(db):
    live._init(db)
    for sql in DDL: db.execute(sql)


def _session(db, gid, session_id):
    row=db.execute("SELECT * FROM rp_live_sessions WHERE guild_id=? AND id=? AND status='live'", (gid,int(session_id))).fetchone()
    if not row or row['stream_type']!='pk': raise ValueError('雙方必須開啟正在進行的 PK 直播')
    return row


def _free(db,gid,sid):
    if db.execute("SELECT 1 FROM rp_live_pk_matches WHERE guild_id=? AND status IN ('pending','active') AND (challenger_session_id=? OR opponent_session_id=?)",(gid,sid,sid)).fetchone():
        raise ValueError('直播間已有等待或進行中的 PK')


def challenge(guild_id,host_user_id,challenger_session_id,opponent_session_id,*,now=None):
    gid,uid=str(guild_id),str(host_user_id)
    ts=int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        a=_session(db,gid,challenger_session_id); b=_session(db,gid,opponent_session_id)
        if a['owner_id']!=uid: raise PermissionError('只有發起直播的主持玩家可邀請 PK')
        if int(a['id'])==int(b['id']) or a['owner_id']==b['owner_id']: raise ValueError('不能和自己的另一個直播間 PK')
        _free(db,gid,a['id']);_free(db,gid,b['id'])
        return db.execute("INSERT INTO rp_live_pk_matches(guild_id,challenger_session_id,opponent_session_id,challenger_character_id,opponent_character_id,status,created_at,expires_at) VALUES(?,?,?,?,?,'pending',?,?)",(gid,a['id'],b['id'],a['host_character_id'],b['host_character_id'],ts,ts+3600)).lastrowid


def pending(guild_id,host_user_id,character_id,*,now=None):
    gid,uid=str(guild_id),str(host_user_id); ts=int(time.time() if now is None else now)
    with get_connection() as db:
        _init(db)
        return [dict(r) for r in db.execute("""SELECT p.* FROM rp_live_pk_matches p JOIN rp_live_sessions s ON s.guild_id=p.guild_id AND s.id=p.opponent_session_id
         WHERE p.guild_id=? AND p.opponent_character_id=? AND s.owner_id=? AND p.status='pending' AND p.expires_at>? AND s.status='live' ORDER BY p.id DESC""",(gid,int(character_id),uid,ts)).fetchall()]


def respond(guild_id,host_user_id,character_id,match_id,accept,*,now=None):
    gid,uid=str(guild_id),str(host_user_id); ts=int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        row=db.execute('SELECT * FROM rp_live_pk_matches WHERE guild_id=? AND id=?',(gid,int(match_id))).fetchone()
        if row is None or row['status']!='pending': raise ValueError('找不到待處理的 PK 邀請')
        b=_session(db,gid,row['opponent_session_id'])
        if b['owner_id']!=uid or int(row['opponent_character_id'])!=int(character_id): raise PermissionError('只能由受邀主持角色本人同意')
        if row['expires_at']<=ts:
            db.execute("UPDATE rp_live_pk_matches SET status='expired' WHERE id=?",(match_id,))
            return 'expired'
        if accept:
            _session(db,gid,row['challenger_session_id'])
            db.execute("UPDATE rp_live_pk_matches SET status='active',started_at=? WHERE id=?",(ts,match_id))
            return 'active'
        db.execute("UPDATE rp_live_pk_matches SET status='declined' WHERE id=?",(match_id,))
        return 'declined'


def _score(db,gid,sid,since):
    # Gift ledger rows are the source of truth. Simulated tips do NOT count.
    try:
        row=db.execute('SELECT COALESCE(SUM(gross_amount),0) FROM rp_live_gifts WHERE guild_id=? AND session_id=? AND created_at>=?',(gid,int(sid),int(since))).fetchone()
    except Exception as exc:
        if 'no such table' in str(exc): return 0
        raise
    return int(row[0])


def status(guild_id,match_id):
    gid=str(guild_id)
    with get_connection() as db:
        _init(db)
        row=db.execute('SELECT * FROM rp_live_pk_matches WHERE guild_id=? AND id=?',(gid,int(match_id))).fetchone()
        if not row: raise LookupError('找不到 PK')
        result=dict(row)
        if row['status']=='active':
            result['challenger_score']=_score(db,gid,row['challenger_session_id'],row['started_at'])
            result['opponent_score']=_score(db,gid,row['opponent_session_id'],row['started_at'])
        return result


def finish(guild_id,host_user_id,match_id,*,now=None):
    gid,uid=str(guild_id),str(host_user_id); ts=int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        row=db.execute('SELECT * FROM rp_live_pk_matches WHERE guild_id=? AND id=?',(gid,int(match_id))).fetchone()
        if not row: raise LookupError('找不到 PK')
        if row['status']=='ended': return dict(row)
        if row['status']!='active': raise ValueError('PK 尚未開始或已取消')
        a=db.execute('SELECT owner_id FROM rp_live_sessions WHERE guild_id=? AND id=?',(gid,row['challenger_session_id'])).fetchone()
        b=db.execute('SELECT owner_id FROM rp_live_sessions WHERE guild_id=? AND id=?',(gid,row['opponent_session_id'])).fetchone()
        if uid not in {a['owner_id'],b['owner_id']}: raise PermissionError('只有雙方主持人能結束 PK')
        sa=_score(db,gid,row['challenger_session_id'],row['started_at']); sb=_score(db,gid,row['opponent_session_id'],row['started_at'])
        winner=int(row['challenger_character_id'] if sa>sb else row['opponent_character_id']) if sa!=sb else None
        db.execute("UPDATE rp_live_pk_matches SET status='ended',ended_at=?,challenger_score=?,opponent_score=?,winner_character_id=? WHERE guild_id=? AND id=?",(ts,sa,sb,winner,gid,match_id))
        return dict(db.execute('SELECT * FROM rp_live_pk_matches WHERE guild_id=? AND id=?',(gid,match_id)).fetchone())
