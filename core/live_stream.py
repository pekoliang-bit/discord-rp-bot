"""Persistent core for the single /網路 live-stream entry point.

Virtual audience/tips are illustrative only; they never mint spendable currency.
Actual gifts use core.live_social and the existing balanced ledger.
"""
from __future__ import annotations

import time
from core.database import get_connection, transaction

KINDS = frozenset({'personal', 'group', 'sales', 'pk', 'official', 'members'})
DURATIONS = frozenset({15, 30, 60, 120, 240})


def _init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_live_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        host_character_id INTEGER NOT NULL,
        owner_id TEXT NOT NULL,
        title TEXT NOT NULL,
        stream_type TEXT NOT NULL,
        duration_mode TEXT NOT NULL,
        duration_minutes INTEGER,
        status TEXT NOT NULL DEFAULT 'live',
        started_at INTEGER NOT NULL,
        ends_at INTEGER,
        stopped_at INTEGER,
        viewers INTEGER NOT NULL DEFAULT 0,
        peak_viewers INTEGER NOT NULL DEFAULT 0,
        simulated_tips INTEGER NOT NULL DEFAULT 0,
        CHECK(status IN ('live','ended')),
        CHECK(stream_type IN ('personal','group','sales','pk','official','members'))
    )''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_rp_live_sessions_guild_host
                  ON rp_live_sessions(guild_id,host_character_id,status)''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_live_members (
        guild_id TEXT NOT NULL,
        session_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        owner_id TEXT NOT NULL,
        share_bp INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(guild_id,session_id,character_id)
    )''')


def settings(guild_id):
    """Conservative live-stream defaults shared by gift and team logic."""
    return {'max_members': 10, 'platform_fee_pct': 10}


def _expire(db, gid, now):
    db.execute('''UPDATE rp_live_sessions SET status='ended',stopped_at=ends_at
                  WHERE guild_id=? AND status='live' AND ends_at IS NOT NULL
                    AND ends_at<=?''', (gid, now))


def start(guild_id, user_id, character_id, title, kind, duration_mode,
          duration_minutes=None, *, now=None):
    gid, uid, cid = str(guild_id), str(user_id), int(character_id)
    title = str(title).strip()
    if not 1 <= len(title) <= 80:
        raise ValueError('直播標題必須為 1～80 字')
    if kind not in KINDS:
        raise ValueError('不支援的直播類型')
    if duration_mode not in ('realtime', 'fixed'):
        raise ValueError('直播時間模式不正確')
    if duration_mode == 'fixed':
        if type(duration_minutes) is not int or duration_minutes not in DURATIONS:
            raise ValueError('固定直播時長不正確')
    elif duration_minutes is not None:
        raise ValueError('即時直播不能設定固定分鐘數')
    ts = int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        char = db.execute('''SELECT id FROM characters WHERE guild_id=? AND owner_id=?
                             AND id=? AND status='active' ''', (gid, uid, cid)).fetchone()
        if not char:
            raise PermissionError('只能使用自己已啟用的角色開播')
        _expire(db, gid, ts)
        existing = db.execute('''SELECT 1 FROM rp_live_members m
                    JOIN rp_live_sessions s ON s.guild_id=m.guild_id AND s.id=m.session_id
                    WHERE m.guild_id=? AND m.character_id=? AND s.status='live' ''', (gid, cid)).fetchone()
        if existing:
            raise ValueError('角色已有進行中的直播或正在其他直播間')
        # Official streams require a separate explicit official permission flow.
        if kind == 'official':
            raise PermissionError('官方直播需要正式官方權限審核；目前尚未開放一般玩家自行開啟')
        end = ts + duration_minutes * 60 if duration_mode == 'fixed' else None
        sid = db.execute('''INSERT INTO rp_live_sessions
            (guild_id,host_character_id,owner_id,title,stream_type,duration_mode,
             duration_minutes,started_at,ends_at)
            VALUES(?,?,?,?,?,?,?,?,?)''',
            (gid,cid,uid,title,kind,duration_mode,duration_minutes,ts,end)).lastrowid
        db.execute('''INSERT INTO rp_live_members(guild_id,session_id,character_id,owner_id,share_bp)
                      VALUES(?,?,?,?,0)''',(gid,sid,cid,uid))
        return sid


def current(guild_id, character_id, *, now=None):
    gid, cid = str(guild_id), int(character_id)
    ts = int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        _expire(db, gid, ts)
        row = db.execute('''SELECT s.* FROM rp_live_sessions s
            JOIN rp_live_members m ON m.guild_id=s.guild_id AND m.session_id=s.id
            WHERE s.guild_id=? AND m.character_id=? AND s.status='live'
            ORDER BY s.id DESC LIMIT 1''',(gid,cid)).fetchone()
        return dict(row) if row else None


def stop(guild_id, user_id, session_id, *, now=None):
    gid,uid,sid=str(guild_id),str(user_id),int(session_id)
    ts=int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        _expire(db,gid,ts)
        row=db.execute('SELECT * FROM rp_live_sessions WHERE guild_id=? AND id=?',(gid,sid)).fetchone()
        if not row:
            raise LookupError('找不到直播場次')
        if row['owner_id'] != uid:
            raise PermissionError('只有主持人可以結束自己的直播')
        if row['status'] != 'live':
            raise ValueError('這場直播已結束')
        db.execute("UPDATE rp_live_sessions SET status='ended',stopped_at=? WHERE guild_id=? AND id=?",(ts,gid,sid))
        return dict(db.execute('SELECT * FROM rp_live_sessions WHERE guild_id=? AND id=?',(gid,sid)).fetchone())
