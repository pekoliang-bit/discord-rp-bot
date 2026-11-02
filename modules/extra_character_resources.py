
"""路人初始粉絲、認知度與正式錢包雙向分錄；禁止重複初始化。"""
from __future__ import annotations
from core.database import get_or_create_account

def initialize_resources(db, gid, cid, profile):
    gid = str(gid)
    cid = int(cid)
    r = profile['initial_resources']

    db.execute(
        '''CREATE TABLE IF NOT EXISTS character_system_tracking(
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            loyal_fans INTEGER NOT NULL DEFAULT 0,
            active_antis INTEGER NOT NULL DEFAULT 0,
            public_awareness INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,character_id)
        )'''
    )

    db.execute(
        '''INSERT OR IGNORE INTO character_system_tracking
           (guild_id,character_id,loyal_fans,public_awareness)
           VALUES(?,?,?,?)''',
        (gid, cid, r['fans'], r['awareness'])
    )

    amount = int(r['coins'])
    if amount <= 0:
        return

    wallet = get_or_create_account(
        db, gid, 'character', str(cid), 'wallet'
    )
    treasury = get_or_create_account(
        db, gid, 'system', 'extra_initial_grants', 'treasury'
    )

    key = f'extra-initial:{cid}'
    cur = db.execute(
        '''INSERT OR IGNORE INTO ledger_transactions
           (guild_id,unique_key,description)
           VALUES(?,?,?)''',
        (gid, key, '路人角色初始資歷資產')
    )

    if cur.rowcount:
        db.executemany(
            '''INSERT INTO ledger_entries
               (guild_id,transaction_id,account_id,amount)
               VALUES(?,?,?,?)''',
            [
                (gid, cur.lastrowid, treasury, -amount),
                (gid, cur.lastrowid, wallet, amount),
            ]
        )
