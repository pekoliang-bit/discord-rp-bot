"""Remote pre-release probes, no Discord login. Requires isolated staging DB.
Keeps one small persistence marker, never changes existing game rows.
"""
import os
import sqlite3
import uuid
from contextlib import closing
from core import database
from core.turso_backend import connect, credentials
from scripts.verify_turso_schema import verify_ledger_rollback
from scripts.verify_turso_backend import verify as verify_basic

MARKER = 'rp_turso_release_probe_v1'


def require_staging():
    credentials()
    if os.getenv('RP_TURSO_MODE') != 'staging':
        raise RuntimeError('Only isolated staging database is permitted.')
    with closing(connect()) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        for table in sorted(tables - {MARKER}):
            quoted = '"'+table.replace('"','""')+'"'
            if db.execute(f'SELECT 1 FROM {quoted} LIMIT 1').fetchone() is not None:
                raise RuntimeError('Nonempty table; use separate isolated staging DB: '+table)


def persistence_probe():
    with closing(connect()) as db:
        db.execute('CREATE TABLE IF NOT EXISTS '+MARKER+' (id INTEGER PRIMARY KEY CHECK(id=1), marker TEXT NOT NULL)')
        before = db.execute('SELECT marker FROM '+MARKER+' WHERE id=1').fetchone()
        value = before[0] if before else uuid.uuid4().hex
        if not before:
            db.execute('INSERT INTO '+MARKER+' VALUES(1,?)',(value,))
        db.commit()
    with closing(connect()) as db:
        row = db.execute('SELECT marker FROM '+MARKER+' WHERE id=1').fetchone()
        if row is None or row[0] != value:
            raise RuntimeError('Persistent marker missing after reconnect')
    print('TURSO_PERSISTENCE_'+('RESTART_MARKER_FOUND' if before else 'MARKER_CREATED')+': '+value, flush=True)


def constraint_probe():
    name = 'rp_constraint_probe_'+uuid.uuid4().hex
    with closing(connect()) as db:
        db.execute(f'CREATE TABLE {name}(id INTEGER PRIMARY KEY, value INTEGER NOT NULL CHECK(value>=0))')
        db.commit()
        failures=[]
        try:
            db.execute(f'INSERT INTO {name} VALUES(1,10)')
            db.commit()
            for sql in (f'INSERT INTO {name} VALUES(1,20)', f'INSERT INTO {name} VALUES(2,-1)'):
                try:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute(sql)
                except sqlite3.IntegrityError:
                    pass
                except Exception as exc:
                    failures.append(type(exc).__module__+'.'+type(exc).__name__)
                    # Only synthetic probe SQL is used here; avoid dumping driver messages or credentials.
                    print('TURSO_UNMAPPED_PROBE_ERROR:', type(exc).__name__, 'constraint probe', flush=True)
                else:
                    failures.append('constraint unexpectedly accepted')
                finally:
                    db.rollback()
            if db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] != 1:
                raise RuntimeError('Constraint rollback left unexpected rows')
        finally:
            db.rollback()
            db.execute(f'DROP TABLE {name}')
            db.commit()
        if failures:
            raise RuntimeError('SQLite exception compatibility requires correction: '+', '.join(failures))
    print('TURSO_CONSTRAINT_COMPAT_OK', flush=True)


def verify_release():
    require_staging()
    verify_basic()
    database.init_db()
    verify_ledger_rollback()
    persistence_probe()
    constraint_probe()
    print('TURSO_RELEASE_PROBES_OK (not full gameplay acceptance)', flush=True)


if __name__ == '__main__':
    verify_release()
