"""網路論壇發文、關注及好友關係；所有帳號使用現有角色所有權檢查。"""
from __future__ import annotations

import re
from core.database import transaction, get_connection
from core import network_groups as accounts

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS rp_network_forum_bindings (
        guild_id TEXT PRIMARY KEY, forum_id TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS rp_network_posts (
        guild_id TEXT NOT NULL, thread_id TEXT NOT NULL,
        account_id INTEGER NOT NULL, character_id INTEGER NOT NULL,
        tag_ids TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,thread_id))''',
    '''CREATE TABLE IF NOT EXISTS rp_network_interactions (
        guild_id TEXT NOT NULL, thread_id TEXT NOT NULL, message_id TEXT NOT NULL,
        actor_id TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,message_id,actor_id,kind,detail))''',
    '''CREATE TABLE IF NOT EXISTS rp_network_follows (
        guild_id TEXT NOT NULL, follower_account_id INTEGER NOT NULL,
        target_account_id INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,follower_account_id,target_account_id))''',
    '''CREATE TABLE IF NOT EXISTS rp_network_friends (
        guild_id TEXT NOT NULL, requester_account_id INTEGER NOT NULL,
        target_account_id INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,requester_account_id,target_account_id),
        CHECK(state IN ('pending','accepted','declined')))''',
)


def initialize(db):
    accounts._init(db)
    for statement in SCHEMA:
        db.execute(statement)


def bind_forum(guild_id, forum_id):
    if int(forum_id) <= 0:
        raise ValueError('論壇頻道編號無效')
    with transaction() as db:
        initialize(db)
        db.execute('INSERT INTO rp_network_forum_bindings (guild_id,forum_id) VALUES (?,?) '
                   'ON CONFLICT(guild_id) DO UPDATE SET forum_id=excluded.forum_id',
                   (str(guild_id),str(forum_id)))


def bound_forum(guild_id):
    with get_connection() as db:
        initialize(db)
        row=db.execute('SELECT forum_id FROM rp_network_forum_bindings WHERE guild_id=?',
                       (str(guild_id),)).fetchone()
        return int(row['forum_id']) if row else None


def save_post(guild_id,user_id,account_id,thread_id,tag_ids):
    import json
    with transaction() as db:
        initialize(db)
        account=accounts._require_account(db,guild_id,user_id,account_id)
        db.execute('INSERT INTO rp_network_posts '
                   '(guild_id,thread_id,account_id,character_id,tag_ids) VALUES (?,?,?,?,?)',
                   (str(guild_id),str(thread_id),int(account_id),int(account['character_id']),
                    json.dumps([int(x) for x in tag_ids])))


def thread_post(guild_id,thread_id):
    with get_connection() as db:
        initialize(db)
        row=db.execute('SELECT * FROM rp_network_posts WHERE guild_id=? AND thread_id=?',
                       (str(guild_id),str(thread_id))).fetchone()
        return dict(row) if row else None


def resolve_identity(guild_id, handle, character_id):
    """關注/好友要求帳號名稱和角色編號必須同時相符。"""
    with get_connection() as db:
        initialize(db)
        handle=str(handle).lstrip('@').strip()
        row=db.execute('''SELECT a.id,a.character_id,a.handle,c.owner_id
            FROM rp_network_accounts a JOIN characters c
            ON c.guild_id=a.guild_id AND c.id=a.character_id
            WHERE a.guild_id=? AND a.handle=? COLLATE NOCASE AND a.character_id=?
              AND c.status='active' ''',
            (str(guild_id),handle,int(character_id))).fetchone()
        return dict(row) if row else None


def follow(guild_id,user_id,source_account,target_account):
    with transaction() as db:
        initialize(db)
        accounts._require_account(db,guild_id,user_id,source_account)
        if int(source_account)==int(target_account):
            raise ValueError('不能關注自己的帳號')
        row=db.execute('SELECT id FROM rp_network_accounts WHERE guild_id=? AND id=?',
                       (str(guild_id),int(target_account))).fetchone()
        if not row:
            raise LookupError('目標帳號不存在')
        db.execute('INSERT OR IGNORE INTO rp_network_follows VALUES (?,?,?,CURRENT_TIMESTAMP)',
                   (str(guild_id),int(source_account),int(target_account)))


def request_friend(guild_id,user_id,source_account,target_account):
    with transaction() as db:
        initialize(db)
        accounts._require_account(db,guild_id,user_id,source_account)
        if int(source_account)==int(target_account):
            raise ValueError('不能向自己發送好友申請')
        row=db.execute('SELECT id FROM rp_network_accounts WHERE guild_id=? AND id=?',
                       (str(guild_id),int(target_account))).fetchone()
        if not row:
            raise LookupError('目標帳號不存在')
        reverse=db.execute('''SELECT state FROM rp_network_friends WHERE guild_id=?
            AND requester_account_id=? AND target_account_id=?''',
            (str(guild_id),int(target_account),int(source_account))).fetchone()
        if reverse and reverse['state']=='accepted':
            raise ValueError('你們已是好友')
        db.execute('''INSERT INTO rp_network_friends
            (guild_id,requester_account_id,target_account_id,state)
            VALUES (?,?,?,'pending') ON CONFLICT(guild_id,requester_account_id,target_account_id)
            DO UPDATE SET state=CASE WHEN state='declined' THEN 'pending' ELSE state END ''',
            (str(guild_id),int(source_account),int(target_account)))


def pending_friends(guild_id,user_id,target_account):
    with get_connection() as db:
        initialize(db)
        accounts._require_account(db,guild_id,user_id,target_account)
        return [dict(r) for r in db.execute('''SELECT f.requester_account_id,a.handle,
            a.character_id FROM rp_network_friends f JOIN rp_network_accounts a
            ON a.guild_id=f.guild_id AND a.id=f.requester_account_id
            WHERE f.guild_id=? AND f.target_account_id=? AND f.state='pending'
            ORDER BY f.created_at LIMIT 25''',
            (str(guild_id),int(target_account))).fetchall()]


def decide_friend(guild_id,user_id,target_account,requester_account,accept):
    with transaction() as db:
        initialize(db)
        accounts._require_account(db,guild_id,user_id,target_account)
        cur=db.execute('''UPDATE rp_network_friends SET state=? WHERE guild_id=?
            AND target_account_id=? AND requester_account_id=? AND state='pending' ''',
            ('accepted' if accept else 'declined',str(guild_id),int(target_account),int(requester_account)))
        if cur.rowcount != 1:
            raise ValueError('申請不存在或已處理')

# Syntax: @帳號名稱 #角色編號 ✅ / ➕，允許行末有空白。
ACTION_PATTERN = re.compile(r'^@(?P<handle>.+?)\s*[#＃]\s*(?P<cid>\d+)\s*(?P<action>[✅➕])\s*$')


def record_interaction(guild_id, thread_id, message_id, actor_id, kind, detail=''):
    """記錄事件，不將尚未核准的影響量寫進角色數值。"""
    if kind not in ('comment', 'reaction'):
        raise ValueError('未知網路互動類型')
    with transaction() as db:
        initialize(db)
        cur=db.execute("INSERT OR IGNORE INTO rp_network_interactions "
                       "(guild_id,thread_id,message_id,actor_id,kind,detail) "
                       "VALUES (?,?,?,?,?,?)",
                       (str(guild_id), str(thread_id), str(message_id),
                        str(actor_id), kind, str(detail)[:200]))
        return cur.rowcount > 0


def relationship_counts(guild_id, account_id):
    """帳號關注數和好友數，與角色屬性粉絲及黑粉分開。"""
    with get_connection() as db:
        initialize(db)
        followed=db.execute(
            'SELECT COUNT(*) FROM rp_network_follows WHERE guild_id=? AND target_account_id=?',
            (str(guild_id),int(account_id))).fetchone()[0]
        friends=db.execute(
            "SELECT COUNT(*) FROM rp_network_friends WHERE guild_id=? AND state='accepted' "
            "AND (requester_account_id=? OR target_account_id=?)",
            (str(guild_id),int(account_id),int(account_id))).fetchone()[0]
        return {'followers':int(followed),'friends':int(friends)}
