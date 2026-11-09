"""作品選角唯讀查詢。角色與導演權限在資料庫層再次驗證。"""
from __future__ import annotations


def _ensure_director(db, guild_id, pid, user_id):
    from sqlite3 import Row
    row = db.execute("""SELECT p.id FROM productions AS p
        JOIN characters AS c ON c.guild_id=p.guild_id AND c.id=p.owner_character_id
        WHERE p.guild_id=? AND p.id=? AND c.owner_id=?
          AND c.status='active' AND c.character_type='player'""",
        (str(guild_id), int(pid), str(user_id))).fetchone()
    if not row:
        raise PermissionError('只有本作品導演／製作角色持有人可查看報名資料。')


def count_applicants(db, guild_id, pid, user_id):
    _ensure_director(db, guild_id, pid, user_id)
    row = db.execute("""SELECT COUNT(*) FROM production_applications
        WHERE guild_id=? AND production_id=?""", (str(guild_id),int(pid))).fetchone()
    return int(row[0])


def list_applicants(db, guild_id, pid, user_id, *, offset=0, limit=8):
    _ensure_director(db, guild_id, pid, user_id)
    limit = min(25, max(1,int(limit)))
    offset = max(0,int(offset))
    rows = db.execute("""SELECT a.id, a.character_id, a.status,
        c.name AS character_name, p.title AS position_title, p.capacity,
        (SELECT COUNT(*) FROM production_applications AS b
         WHERE b.guild_id=a.guild_id AND b.position_id=a.position_id
           AND b.status='accepted') AS filled
        FROM production_applications AS a
        JOIN characters AS c ON c.guild_id=a.guild_id AND c.id=a.character_id
        JOIN production_positions AS p ON p.guild_id=a.guild_id AND p.id=a.position_id
        WHERE a.guild_id=? AND a.production_id=?
        ORDER BY CASE a.status WHEN 'pending' THEN 0 WHEN 'accepted' THEN 1 ELSE 2 END,
                 a.id ASC LIMIT ? OFFSET ?""",
        (str(guild_id),int(pid),limit,offset)).fetchall()
    return [dict(row) for row in rows]
