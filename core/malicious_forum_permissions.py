"""/惡意：角色專屬論壇及「機器人使用」帖子授權。

只信任經資料庫綁定的 thread ID；不因帖子同名而授權。
授權時比對 Discord 使用者、角色、論壇與帖子。
"""
from __future__ import annotations

from dataclasses import dataclass
from core.database import get_connection, transaction


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_malicious_bot_threads (
        guild_id TEXT NOT NULL,
        character_id INTEGER NOT NULL,
        forum_id TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        set_by TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id, character_id),
        UNIQUE(guild_id, thread_id)
    )""")


@dataclass(frozen=True)
class ForumAuthorization:
    guild_id: str
    character_id: int
    actor_user_id: str
    forum_id: int
    thread_id: int


def _character(db, gid, cid):
    row = db.execute("""SELECT id, owner_id, status FROM characters
        WHERE guild_id=? AND id=?""", (gid, cid)).fetchone()
    if row is None or row['status'] != 'active':
        raise PermissionError('角色不存在或尚未啟用')
    return row


def register_bot_thread(guild_id: int, character_id: int, forum_id: int,
                        thread_id: int, *, setter_id: int, owner_authorized: bool = False):
    """只允許經 Discord guild.owner_id 核實的群主綁定帖子。

    由上層確認 thread.parent_id == forum_id 且 thread.guild.id == guild_id。
    """
    if not owner_authorized:
        raise PermissionError('只有群主可登記機器人使用帖子')
    if any(type(x) is not int or x <= 0 for x in
           (guild_id, character_id, forum_id, thread_id, setter_id)):
        raise ValueError('Discord ID 或角色 ID 不合法')
    gid = str(guild_id)
    with transaction() as db:
        initialize(db)
        _character(db, gid, character_id)
        link = db.execute("""SELECT forum_id FROM character_forum_links
            WHERE guild_id=? AND character_id=?""", (gid, character_id)).fetchone()
        if link is None or str(link['forum_id']) != str(forum_id):
            raise PermissionError('指定論壇不是這個角色登記的專屬論壇')
        conflict = db.execute("""SELECT character_id FROM rp_malicious_bot_threads
            WHERE guild_id=? AND thread_id=?""", (gid, str(thread_id))).fetchone()
        if conflict is not None and conflict['character_id'] != character_id:
            raise ValueError('此帖子已綁定其他角色')
        db.execute("""INSERT INTO rp_malicious_bot_threads
            (guild_id, character_id, forum_id, thread_id, set_by)
            VALUES (?,?,?,?,?)
            ON CONFLICT(guild_id,character_id) DO UPDATE SET
                forum_id=excluded.forum_id,
                thread_id=excluded.thread_id,
                set_by=excluded.set_by""",
            (gid, character_id, str(forum_id), str(thread_id), str(setter_id)))


def authorize_malicious(guild_id: int, user_id: int, thread_id: int,
                        parent_forum_id: int, *, owner_override: bool = False) -> ForumAuthorization:
    """以所處帖子反查發起角色，防止玩家自行指定其他人的角色。

    owner_override 僅允許經 guild.owner_id 核實的伺服器群主使用；
    無論是否群主，仍須在已登記的「機器人使用」帖子執行。
    """
    if any(type(x) is not int or x <= 0 for x in
           (guild_id, user_id, thread_id, parent_forum_id)):
        raise ValueError('伺服器、使用者、論壇與帖子 ID 必須為正整數')
    gid = str(guild_id)
    with get_connection() as db:
        row = db.execute("""SELECT character_id, forum_id FROM rp_malicious_bot_threads
            WHERE guild_id=? AND thread_id=?""", (gid, str(thread_id))).fetchone()
        if row is None or str(row['forum_id']) != str(parent_forum_id):
            raise PermissionError('只能在自己角色論壇的「機器人使用」帖子執行 /惡意')
        cid = int(row['character_id'])
        link = db.execute("""SELECT forum_id FROM character_forum_links
            WHERE guild_id=? AND character_id=?""", (gid, cid)).fetchone()
        if link is None or str(link['forum_id']) != str(parent_forum_id):
            raise PermissionError('角色論壇登記資料不一致')
        actor = _character(db, gid, cid)
        if not owner_override and str(actor['owner_id']) != str(user_id):
            raise PermissionError('這不是你的角色專屬論壇')
        return ForumAuthorization(gid, cid, str(user_id), parent_forum_id, thread_id)
