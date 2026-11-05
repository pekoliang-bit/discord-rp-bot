
"""Auto-provision a private and an official network account per active character.

Display handles support Unicode scripts; no real Discord accounts are created.
Existing accounts and group membership are never deleted.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import unicodedata

from core.database import get_connection, transaction
from core import network_groups as ng

LOG = logging.getLogger(__name__)
KINDS = ('personal', 'official')


def _name(value: object) -> str:
    value = unicodedata.normalize('NFC', str(value or ''))
    value = re.sub(r'\s+', ' ', value).strip()
    # Never allow role mentions, markdown tags or control characters in generated names.
    value = ''.join(ch for ch in value if unicodedata.category(ch)[0] != 'C')
    value = value.replace('@', '＠').replace('<', '＜').replace('>', '＞').replace('`', 'ˋ')
    return value.strip()


def _stage_name(character: sqlite3.Row) -> str:
    try:
        data = json.loads(character['settings_json'] or '{}')
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for key in ('stage_name', '藝名或稱號', '藝名'):
        name = _name(data.get(key))
        if name:
            return name
    return _name(character['name'])


def _available(db, guild_id: str, candidate: str, character_id: int) -> str:
    # Discord select labels max 100 chars; preserve Unicode and reject no language.
    candidate = candidate[:80].strip() or f'角色{character_id}'
    found = db.execute(
        'SELECT handle FROM rp_network_accounts WHERE guild_id=?', (guild_id,)
    ).fetchall()
    used = {str(r['handle']).casefold() for r in found}
    if candidate.casefold() not in used:
        return candidate
    suffix = f' #{character_id}'
    result = candidate[:80-len(suffix)].rstrip() + suffix
    if result.casefold() not in used:
        return result
    for number in range(2, 10000):
        suffix = f' #{character_id}-{number}'
        result = candidate[:80-len(suffix)].rstrip() + suffix
        if result.casefold() not in used:
            return result
    raise RuntimeError('網路帳號可用名稱已耗盡')


def initialize(db):
    ng._init(db)
    cols = {r['name'] for r in db.execute('PRAGMA table_info(rp_network_accounts)')}
    if 'account_kind' not in cols:
        db.execute("ALTER TABLE rp_network_accounts ADD COLUMN account_kind TEXT NOT NULL DEFAULT 'custom'")
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS idx_rp_network_account_kind
        ON rp_network_accounts(guild_id, character_id, account_kind)
        WHERE account_kind IN ('personal', 'official')''')


def _ensure(db, character: sqlite3.Row):
    gid, cid = str(character['guild_id']), int(character['id'])
    name = _name(character['name']) or f'角色{cid}'
    stage = _stage_name(character)
    wants = {'personal': name, 'official': f'{stage}Oficial'}
    result = []
    for kind, proposed in wants.items():
        row = db.execute('''SELECT id, handle FROM rp_network_accounts
            WHERE guild_id=? AND character_id=? AND account_kind=?
            ORDER BY id LIMIT 1''', (gid, cid, kind)).fetchone()
        if row:
            result.append(int(row['id']))
            continue
        # Adopt an existing account with the exact expected handle, preserving memberships.
        row = db.execute('''SELECT id, handle FROM rp_network_accounts
            WHERE guild_id=? AND character_id=? AND account_kind='custom'
            AND handle=? COLLATE NOCASE ORDER BY id LIMIT 1''', (gid, cid, proposed)).fetchone()
        if row:
            db.execute('UPDATE rp_network_accounts SET account_kind=? WHERE id=?', (kind, row['id']))
            result.append(int(row['id']))
            continue
        handle = _available(db, gid, proposed, cid)
        cur = db.execute('''INSERT INTO rp_network_accounts
            (guild_id, character_id, handle, account_kind) VALUES (?, ?, ?, ?)''',
            (gid, cid, handle, kind))
        result.append(int(cur.lastrowid))
    return result


def ensure_character_accounts(guild_id, character_id):
    """Idempotently add exactly two *automatic* accounts for one active character."""
    with transaction() as db:
        initialize(db)
        char = db.execute('''SELECT id, guild_id, name, settings_json FROM characters
            WHERE guild_id=? AND id=? AND status='active' ''',
            (str(guild_id), int(character_id))).fetchone()
        if not char:
            return []
        return _ensure(db, char)


def backfill_active_characters(batch_size=250):
    """Reconcile all active characters, without mutating retired or deleted ones."""
    processed = 0
    last_id = 0
    while True:
        with get_connection() as db:
            rows = db.execute('''SELECT id, guild_id FROM characters
                WHERE status='active' AND id>? ORDER BY id LIMIT ?''',
                (last_id, int(batch_size))).fetchall()
        if not rows:
            break
        for row in rows:
            try:
                ensure_character_accounts(row['guild_id'], row['id'])
                processed += 1
            except Exception:
                LOG.exception('無法為角色建立網路帳號: guild=%s id=%s',
                              row['guild_id'], row['id'])
        last_id = int(rows[-1]['id'])
    return processed
