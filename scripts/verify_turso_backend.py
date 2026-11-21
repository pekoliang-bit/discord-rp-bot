"""Run against an empty isolated test database; no Discord login."""
import uuid
from core.turso_backend import connect


def verify():
    name = 'rp_probe_' + uuid.uuid4().hex
    db = connect()
    created = False
    try:
        db.execute(f'CREATE TABLE {name} (id INTEGER PRIMARY KEY AUTOINCREMENT, value INTEGER NOT NULL CHECK(value >= 0))')
        db.commit()
        created = True
        db.execute('BEGIN IMMEDIATE')
        assert db.in_transaction
        cur = db.execute(f'INSERT INTO {name}(value) VALUES (?)', (10,))
        key = cur.lastrowid
        assert isinstance(key, int) and key > 0 and cur.rowcount == 1
        db.commit()
        db.close()
        db = connect()
        row = db.execute(f'SELECT id, value FROM {name} WHERE id=?', (key,)).fetchone()
        assert row[0] == key and row['VALUE'] == 10 and dict(row)['value'] == 10
        db.execute('BEGIN IMMEDIATE')
        db.execute(f'UPDATE {name} SET value=99 WHERE id=?', (key,))
        db.rollback()
        assert db.execute(f'SELECT value FROM {name}').fetchone()[0] == 10
        db.execute('BEGIN IMMEDIATE')
        db.executescript(f'UPDATE {name} SET value=20; UPDATE {name} SET value=30;')
        db.rollback()
        assert db.execute(f'SELECT value FROM {name}').fetchone()[0] == 10
        print('TURSO_PREFLIGHT_OK: remote read/write, reconnect, row mapping, row count, rollback, SQL script', flush=True)
    finally:
        try:
            if created:
                db.rollback()
                db.execute(f'DROP TABLE IF EXISTS {name}')
                db.commit()
        finally:
            db.close()


if __name__ == '__main__':
    verify()
