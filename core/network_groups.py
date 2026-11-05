"""玩家網路帳號與群組資料；Discord 頻道建立由 UI 負責。

所有操作者都透過 Discord user_id -> active character -> account 授權。
本模組的帳號不是系統模擬網友 internet_virtual_users。
"""
from __future__ import annotations
import re
import sqlite3
import json
import unicodedata
from core.database import transaction, get_connection

DEFAULT_GUILD = '1544051203493601380'
DEFAULT_CATEGORY = 1545373646569340988
DEFAULT_GLOBAL = 1567490340909351013
DEFAULT_FACTIONS = {
    '單打獨鬥': 1567491557807099934,
    '另闢蹊徑': 1567491656993738833,
    '抱團取暖': 1567491851588468796,
    '經紀公司': 1567492007293886524,
    'MCN': 1567492172469633134,
}

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS rp_network_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
        character_id INTEGER NOT NULL, handle TEXT NOT NULL COLLATE NOCASE,
        account_kind TEXT NOT NULL DEFAULT 'custom',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(guild_id, handle), UNIQUE(guild_id, character_id, id)
    )''',
    '''CREATE TABLE IF NOT EXISTS rp_network_groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
        founder_account_id INTEGER NOT NULL, name TEXT NOT NULL,
        channel_id TEXT UNIQUE, status TEXT NOT NULL DEFAULT 'creating',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(status IN ('creating','active','closed'))
    )''',
    '''CREATE TABLE IF NOT EXISTS rp_network_group_members (
        group_id INTEGER NOT NULL, account_id INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('invited','joined')),
        joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(group_id, account_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS rp_network_settings (
        guild_id TEXT PRIMARY KEY, category_id TEXT, global_channel_id TEXT,
        faction_channels_json TEXT NOT NULL DEFAULT '{}'
    )''',
    'CREATE INDEX IF NOT EXISTS idx_rp_network_members_account ON rp_network_group_members(account_id,state)',
)

def _init(db):
    for sql in SCHEMA:
        db.execute(sql)
    cols = {r['name'] for r in db.execute('PRAGMA table_info(rp_network_accounts)')}
    if 'account_kind' not in cols:
        db.execute("ALTER TABLE rp_network_accounts ADD COLUMN account_kind TEXT NOT NULL DEFAULT 'custom'")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ix_rp_network_default_accounts
        ON rp_network_accounts(guild_id, character_id, account_kind)
        WHERE account_kind IN ('personal', 'official')""")


def normalize_handle(value):
    value = unicodedata.normalize('NFC', str(value or '').strip())
    if value.casefold() in ('@everyone', '@here'):
        raise ValueError('帳號名稱不可為群體提及')
    if value.startswith('@'):
        value = value[1:].strip()
    if not value or len(value) > 80:
        raise ValueError('帳號名稱需為 1～80 個字元')
    if any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('帳號名稱不可包含控制或不可見字元')
    if '@everyone' in value.casefold() or '@here' in value.casefold():
        raise ValueError('帳號名稱不可包含群體提及')
    return value


def _available_handle(db, guild_id, proposed):
    base = normalize_handle(proposed)
    existing = {r['handle'].casefold() for r in db.execute(
        'SELECT handle FROM rp_network_accounts WHERE guild_id=?', (str(guild_id),))}
    candidate = base
    idx = 2
    while candidate.casefold() in existing:
        suffix = f'-{idx}'
        candidate = base[:80 - len(suffix)] + suffix
        idx += 1
    return candidate


def ensure_default_accounts(guild, character):
    """對已啟用角色冪等補齊本帳、官方帳號。"""
    with transaction() as db:
        _init(db)
        row = db.execute("""SELECT id,name,settings_json FROM characters
            WHERE guild_id=? AND id=? AND status='active'""",
            (str(guild),int(character))).fetchone()
        if row is None:
            raise ValueError('找不到已啟用角色')
        try:
            profile = json.loads(row['settings_json'] or '{}')
        except (ValueError,TypeError):
            profile = {}
        if not isinstance(profile,dict):
            profile = {}
        display = str(profile.get('stage_name') or row['name']).strip() or str(row['name'])
        names = {'personal': display, 'official': display + 'Oficial'}
        for kind, proposed in names.items():
            exists = db.execute("""SELECT id FROM rp_network_accounts
                WHERE guild_id=? AND character_id=? AND account_kind=?""",
                (str(guild),int(character),kind)).fetchone()
            if exists:
                continue
            proposed = normalize_handle(proposed[:80])
            custom = db.execute("""SELECT id,handle FROM rp_network_accounts
                WHERE guild_id=? AND character_id=? AND account_kind='custom'""",
                (str(guild),int(character))).fetchall()
            adopted = next((r for r in custom if r['handle'].casefold()==proposed.casefold()),None)
            if adopted:
                db.execute('UPDATE rp_network_accounts SET account_kind=? WHERE id=?',(kind,adopted['id']))
            else:
                handle = _available_handle(db,guild,proposed)
                db.execute("""INSERT INTO rp_network_accounts
                    (guild_id,character_id,handle,account_kind) VALUES(?,?,?,?)""",
                    (str(guild),int(character),handle,kind))
        return [dict(r) for r in db.execute("""SELECT * FROM rp_network_accounts
            WHERE guild_id=? AND character_id=? ORDER BY id""",(str(guild),int(character)))]


def missing_default_characters(guild,limit=100):
    with get_connection() as db:
        _init(db)
        rows=db.execute("""SELECT c.id FROM characters c
            WHERE c.guild_id=? AND c.status='active' AND (
              SELECT COUNT(DISTINCT a.account_kind) FROM rp_network_accounts a
              WHERE a.guild_id=c.guild_id AND a.character_id=c.id
              AND a.account_kind IN ('personal','official')) < 2
            ORDER BY c.id LIMIT ?""",(str(guild),int(limit))).fetchall()
        return [int(r['id']) for r in rows]

def _require_char(db,guild,user,character):
    row=db.execute('SELECT id,name FROM characters WHERE guild_id=? AND owner_id=? AND id=? AND status=\'active\'',(str(guild),str(user),int(character))).fetchone()
    if not row: raise PermissionError('角色不存在、尚未啟用，或不屬於你')
    return row

def _require_account(db,guild,user,account):
    row=db.execute('''SELECT a.*, c.name AS character_name FROM rp_network_accounts a
    JOIN characters c ON c.id=a.character_id AND c.guild_id=a.guild_id
    WHERE a.guild_id=? AND a.id=? AND c.owner_id=? AND c.status='active' ''',(str(guild),int(account),str(user))).fetchone()
    if not row: raise PermissionError('無權操作這個網路帳號')
    return dict(row)

def accounts(guild,user,character):
    with get_connection() as db:
        _init(db); _require_char(db,guild,user,character)
        return [dict(r) for r in db.execute('SELECT * FROM rp_network_accounts WHERE guild_id=? AND character_id=? ORDER BY id',(str(guild),int(character))).fetchall()]

def create_account(guild,user,character,handle):
    handle=normalize_handle(handle)
    with transaction() as db:
        _init(db); _require_char(db,guild,user,character)
        if any(r['handle'].casefold() == handle.casefold() for r in db.execute('SELECT handle FROM rp_network_accounts WHERE guild_id=?', (str(guild),))):
            raise ValueError('此網路帳號名稱已被使用')
        try:
            cur=db.execute('INSERT INTO rp_network_accounts(guild_id,character_id,handle) VALUES(?,?,?)',(str(guild),int(character),handle))
        except sqlite3.IntegrityError: raise ValueError('此網路帳號名稱已被使用') from None
        return int(cur.lastrowid)

def account_info(guild,user,account):
    with get_connection() as db:
        _init(db); return _require_account(db,guild,user,account)

def settings(guild):
    """Read guild bindings, falling back to original defaults for unset fields."""
    import json
    with get_connection() as db:
        _init(db)
        row = db.execute(
            'SELECT * FROM rp_network_settings WHERE guild_id=?', (str(guild),)
        ).fetchone()
    default = str(guild) == DEFAULT_GUILD
    result = {
        'category_id': DEFAULT_CATEGORY if default else None,
        'global_channel_id': DEFAULT_GLOBAL if default else None,
        'factions': DEFAULT_FACTIONS.copy() if default else {},
    }
    if row:
        for field in ('category_id', 'global_channel_id'):
            if row[field]:
                result[field] = int(row[field])
        try:
            factions = json.loads(row['faction_channels_json'] or '{}')
            if isinstance(factions, dict):
                result['factions'].update({str(k): int(v) for k, v in factions.items() if v})
        except (ValueError, TypeError):
            pass
    return result


def set_settings(guild, *, category_id=None, global_channel_id=None,
                 faction_name=None, faction_channel_id=None):
    """Update only supplied values; do not erase pre-existing faction bindings."""
    import json
    old = settings(guild)  # Must happen before opening a write transaction.
    if category_id is not None:
        old['category_id'] = int(category_id)
    if global_channel_id is not None:
        old['global_channel_id'] = int(global_channel_id)
    if faction_name is not None:
        name = str(faction_name).strip()
        if not name or faction_channel_id is None:
            raise ValueError('請指定群組名稱與頻道 ID')
        old['factions'][name] = int(faction_channel_id)
    with transaction() as db:
        _init(db)
        db.execute("""INSERT INTO rp_network_settings
            (guild_id,category_id,global_channel_id,faction_channels_json)
            VALUES(?,?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET
            category_id=excluded.category_id,
            global_channel_id=excluded.global_channel_id,
            faction_channels_json=excluded.faction_channels_json""",
            (str(guild), str(old['category_id']) if old['category_id'] else None,
             str(old['global_channel_id']) if old['global_channel_id'] else None,
             json.dumps(old['factions'], ensure_ascii=False)))

def begin_group(guild,user,account,name):
    name=str(name).strip()
    if len(name)<2 or len(name)>70: raise ValueError('群組名稱須為 2～70 字')
    with transaction() as db:
        _init(db); _require_account(db,guild,user,account)
        cur=db.execute('INSERT INTO rp_network_groups(guild_id,founder_account_id,name) VALUES(?,?,?)',(str(guild),int(account),name))
        gid=int(cur.lastrowid)
        db.execute("INSERT INTO rp_network_group_members(group_id,account_id,state) VALUES(?,?,'joined')",(gid,int(account)))
        return gid

def activate_group(guild,group,channel_id):
    with transaction() as db:
        _init(db)
        cur=db.execute("UPDATE rp_network_groups SET channel_id=?,status='active' WHERE guild_id=? AND id=? AND status='creating'",(str(channel_id),str(guild),int(group)))
        if cur.rowcount!=1: raise ValueError('群組狀態已改變')

def cancel_group(guild,group):
    with transaction() as db:
        _init(db)
        db.execute("UPDATE rp_network_groups SET status='closed' WHERE guild_id=? AND id=? AND status='creating'",(str(guild),int(group)))

def memberships(guild,user,account):
    with get_connection() as db:
        _init(db); _require_account(db,guild,user,account)
        return [dict(r) for r in db.execute('''SELECT g.id,g.name,g.channel_id,g.founder_account_id,m.state
        FROM rp_network_group_members m JOIN rp_network_groups g ON g.id=m.group_id
        WHERE g.guild_id=? AND m.account_id=? AND g.status='active' ORDER BY g.id''',(str(guild),int(account))).fetchall()]

def _member_group(db,guild,user,account,group,*,founder=False):
    _require_account(db,guild,user,account)
    row=db.execute('''SELECT g.*,m.state FROM rp_network_groups g
        JOIN rp_network_group_members m ON m.group_id=g.id AND m.account_id=?
        WHERE g.guild_id=? AND g.id=? AND g.status='active' ''',(int(account),str(guild),int(group))).fetchone()
    if not row or row['state']!='joined': raise PermissionError('你不是這個群組的正式成員')
    if founder and int(row['founder_account_id'])!=int(account): raise PermissionError('只有成立者可以邀請成員')
    return dict(row)

def resolve_target(db,guild,lookup):
    lookup=str(lookup).strip()
    if lookup.startswith('@'): lookup=lookup[1:]
    row=db.execute('''SELECT a.id,a.character_id,c.owner_id,c.name,a.handle FROM rp_network_accounts a
       JOIN characters c ON c.guild_id=a.guild_id AND c.id=a.character_id
       WHERE a.guild_id=? AND a.handle=? COLLATE NOCASE AND c.status='active' ''',(str(guild),lookup)).fetchone()
    if row: return dict(row)
    match=re.fullmatch(r'(.+?)\s*[#＃]\s*(\d+)',lookup)
    if match:
        row=db.execute('''SELECT a.id,a.character_id,c.owner_id,c.name,a.handle FROM rp_network_accounts a
        JOIN characters c ON c.guild_id=a.guild_id AND c.id=a.character_id
        WHERE a.guild_id=? AND c.id=? AND c.name=? AND c.status='active' ORDER BY a.id''',(str(guild),int(match.group(2)),match.group(1).strip())).fetchall()
        if len(row)==1: return dict(row[0])
        if len(row)>1: raise ValueError('角色有多個帳號，請指定 @帳號名稱')
    raise LookupError('找不到指定帳號；請填 @帳號名稱，或角色全名 #編號')

def invite(guild,user,account,group,lookup):
    with transaction() as db:
        _init(db); _member_group(db,guild,user,account,group,founder=True)
        target=resolve_target(db,guild,lookup)
        if target['id']==int(account): raise ValueError('你已經在群組內')
        existing=db.execute('SELECT state FROM rp_network_group_members WHERE group_id=? AND account_id=?',(int(group),target['id'])).fetchone()
        if existing: raise ValueError('對方已經加入或已有待處理邀請')
        db.execute("INSERT INTO rp_network_group_members(group_id,account_id,state) VALUES(?,?,'invited')",(int(group),target['id']))
        return target

def accept(guild,user,account,group):
    with transaction() as db:
        _init(db); _require_account(db,guild,user,account)
        row=db.execute('''SELECT g.channel_id,g.name FROM rp_network_group_members m JOIN rp_network_groups g ON g.id=m.group_id
        WHERE g.guild_id=? AND g.id=? AND m.account_id=? AND m.state='invited' AND g.status='active' ''',(str(guild),int(group),int(account))).fetchone()
        if not row: raise ValueError('沒有這筆待接受邀請')
        db.execute("UPDATE rp_network_group_members SET state='joined',joined_at=CURRENT_TIMESTAMP WHERE group_id=? AND account_id=?",(int(group),int(account)))
        return dict(row)

def decline(guild,user,account,group):
    with transaction() as db:
        _init(db); _require_account(db,guild,user,account)
        db.execute("DELETE FROM rp_network_group_members WHERE group_id=? AND account_id=? AND state='invited'",(int(group),int(account)))

def leave(guild,user,account,group):
    with transaction() as db:
        _init(db); row=_member_group(db,guild,user,account,group)
        if row['founder_account_id']==int(account): raise ValueError('成立者目前不可退出；請先處理群組管理權')
        db.execute('DELETE FROM rp_network_group_members WHERE group_id=? AND account_id=?',(int(group),int(account)))
        remaining=db.execute('''SELECT a.character_id,c.owner_id FROM rp_network_group_members m
        JOIN rp_network_accounts a ON a.id=m.account_id JOIN characters c ON c.id=a.character_id AND c.guild_id=a.guild_id
        WHERE m.group_id=? AND m.state='joined' AND c.status='active' AND c.owner_id=?''',(int(group),str(user))).fetchone()
        return {'channel_id':row['channel_id'],'still_has_access':bool(remaining)}

def pending(guild,user,account):
    return [r for r in memberships(guild,user,account) if r['state']=='invited']


def undo_accept(guild,user,account,group):
    """Discord 頻道權限同步失敗時撤銷剛才的接受狀態。"""
    with transaction() as db:
        _init(db); _require_account(db,guild,user,account)
        db.execute("UPDATE rp_network_group_members SET state='invited' WHERE group_id=? AND account_id=? AND state='joined' AND group_id IN (SELECT id FROM rp_network_groups WHERE guild_id=?)",(int(group),int(account),str(guild)))
