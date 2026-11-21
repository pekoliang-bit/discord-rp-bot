"""Stage 2: initialize the core schema on an explicitly isolated Turso database.
Never logs in to Discord. Refuses databases containing player/guild records.
"""
import os
import re
import uuid
from core import database
from core.turso_backend import connect, credentials


def require_isolated():
    credentials()
    if os.getenv('RP_TURSO_MODE') != 'staging':
        raise RuntimeError('Set RP_TURSO_MODE=staging only on an isolated test database.')
    with_read = connect()
    try:
        tables = {r[0] for r in with_read.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        # This service may be rerun after a successful empty-schema initialization.
        for name in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            if with_read.execute(f'SELECT 1 FROM {quoted} LIMIT 1').fetchone() is not None:
                raise RuntimeError('Database is not empty; use a separate staging database. Table: ' + name)
    finally:
        with_read.close()


def verify_schema():
    require_isolated()
    database.init_db()
    db = connect()
    try:
        expected = {m.group(1) for s in database.SCHEMA if (m := re.search(r'CREATE TABLE IF NOT EXISTS\s+(\w+)', s, re.I))}
        expected.update(('rp_rumor_pr_strategy_config','rp_rumor_pr_strategy_audit','rp_rumor_pr_effects','rp_rumor_grade_snapshots'))
        actual = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = expected - actual
        if missing:
            raise RuntimeError('Missing core schema tables: ' + ', '.join(sorted(missing)))
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise RuntimeError('Foreign key integrity check failed')
        print(f'TURSO_CORE_SCHEMA_OK: {len(expected)} required tables', flush=True)
    finally:
        db.close()


def verify_ledger_rollback():
    gid = 'stage2-' + uuid.uuid4().hex
    db = connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO guilds(guild_id,name) VALUES (?,?)', (gid,'stage2 probe'))
        cid = db.execute('INSERT INTO characters(guild_id,owner_id,name) VALUES (?,?,?)', (gid,'probe','probe')).lastrowid
        db.execute('INSERT INTO attributes(guild_id,character_id,name,value) VALUES (?,?,?,?)',(gid,cid,'probe',150))
        a = database.get_or_create_account(db,gid,'character',cid)
        b = database.get_or_create_account(db,gid,'system','probe')
        tx = db.execute('INSERT INTO ledger_transactions(guild_id,unique_key) VALUES (?,?)',(gid,'probe')).lastrowid
        db.executemany('INSERT INTO ledger_entries(guild_id,transaction_id,account_id,amount) VALUES (?,?,?,?)',[(gid,tx,a,100),(gid,tx,b,-100)])
        if database.account_balance(db,gid,a) != 100 or database.account_balance(db,gid,b) != -100:
            raise RuntimeError('Ledger balance mismatch')
        if db.execute('SELECT value FROM attributes WHERE guild_id=? AND character_id=?',(gid,cid)).fetchone()['value'] != 150:
            raise RuntimeError('Attribute read mismatch')
    finally:
        try:
            db.rollback()
        finally:
            db.close()
    db = connect()
    try:
        for table in ('guilds','characters','attributes','accounts','ledger_transactions','ledger_entries'):
            if db.execute(f'SELECT 1 FROM {table} WHERE guild_id=?',(gid,)).fetchone() is not None:
                raise RuntimeError('Rollback failed: '+table)
    finally:
        db.close()
    print('TURSO_CORE_LEDGER_ROLLBACK_OK', flush=True)


if __name__ == '__main__':
    verify_schema()
    verify_ledger_rollback()
