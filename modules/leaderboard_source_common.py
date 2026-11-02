"""排行榜資料來源共用安全工具。唯讀、不建表、不變更資料。"""
from __future__ import annotations
from core.database import get_connection


def table_columns(db, name: str) -> set[str]:
    if not name.replace('_', '').isalnum():
        return set()
    row = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return {r['name'] for r in db.execute(f'PRAGMA table_info("{name}")')} if row else set()


def ranked(rows, limit=100):
    ordered = sorted(rows, key=lambda x: (-float(x['score']), str(x.get('character_id', x.get('id', '')))))
    previous = object()
    for i, row in enumerate(ordered, 1):
        if row['score'] != previous:
            rank = i
        row['rank'] = rank
        previous = row['score']
    return ordered[:max(1, min(int(limit), 500))]


def active_characters(db, guild_id):
    return db.execute("SELECT id,name FROM characters WHERE guild_id=? AND status='active'", (str(guild_id),)).fetchall()
