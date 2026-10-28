"""路人人氣記錄；不新增斜線指令，不允許建立路人。"""
from __future__ import annotations
import json
from core.database import get_connection, transaction


def _is_extra(row):
    try:
        s=json.loads(row['settings_json'] or '{}')
    except (ValueError, TypeError):
        s={}
    if not isinstance(s,dict):return False
    return (s.get('character_category') in ('extra','路人')
            or s.get('public_category') in ('extra','路人')
            or s.get('is_extra') is True
            or s.get('is_bystander') is True)


def init_db():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_extra_likes (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, character_id, user_id)
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_extra_likes_rank
                      ON rp_extra_likes(guild_id, character_id)""")


def count(gid, cid):
    init_db()
    with get_connection() as db:
        row=db.execute('SELECT COUNT(*) AS n FROM rp_extra_likes WHERE guild_id=? AND character_id=?',
                       (str(gid),int(cid))).fetchone()
    return int(row['n'])


def liked(gid, cid, uid):
    init_db()
    with get_connection() as db:
        return db.execute('SELECT 1 FROM rp_extra_likes WHERE guild_id=? AND character_id=? AND user_id=?',
                          (str(gid),int(cid),str(uid))).fetchone() is not None


def toggle(gid, cid, uid):
    init_db()
    with transaction() as db:
        row=db.execute('SELECT id,name,status,settings_json FROM characters WHERE guild_id=? AND id=?',
                       (str(gid),int(cid))).fetchone()
        if row is None or row['status'] not in ('active','retired') or not _is_extra(row):
            raise ValueError('只有已公開的路人可以按愛心。')
        exists=db.execute('SELECT 1 FROM rp_extra_likes WHERE guild_id=? AND character_id=? AND user_id=?',
                          (str(gid),int(cid),str(uid))).fetchone()
        if exists:
            db.execute('DELETE FROM rp_extra_likes WHERE guild_id=? AND character_id=? AND user_id=?',
                       (str(gid),int(cid),str(uid)))
            active=False
        else:
            db.execute('INSERT INTO rp_extra_likes(guild_id,character_id,user_id) VALUES(?,?,?)',
                       (str(gid),int(cid),str(uid)))
            active=True
        n=db.execute('SELECT COUNT(*) AS n FROM rp_extra_likes WHERE guild_id=? AND character_id=?',
                     (str(gid),int(cid))).fetchone()['n']
    return active,int(n)


def leaderboard(gid):
    init_db()
    with get_connection() as db:
        rows=db.execute("""SELECT c.id,c.name,c.public_code,c.settings_json,
                   COUNT(l.user_id) AS hearts
                   FROM characters c
                   LEFT JOIN rp_extra_likes l ON l.guild_id=c.guild_id AND l.character_id=c.id
                   WHERE c.guild_id=? AND c.status IN ('active','retired')
                   GROUP BY c.id ORDER BY hearts DESC,c.id ASC""",(str(gid),)).fetchall()
    return [dict(r) for r in rows if _is_extra(r)]


async def setup(bot):
    init_db()
