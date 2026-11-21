"""Read-only safety checks before launching Discord RP with Turso.

Never prints connection strings, credentials, or player records.
Does not grant proof of compatibility for all gameplay modules.
"""
import re
from contextlib import closing
from core.database import SCHEMA
from core.turso_backend import connect, require_production_gate


def verify():
    require_production_gate()
    expected = {m.group(1) for ddl in SCHEMA if (m := re.search(r'CREATE TABLE IF NOT EXISTS\s+(\w+)', ddl, re.I))}
    with closing(connect()) as db:
        tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # The database can be newly initialized by main.setup_hook; don't alter schema here.
        if not tables.intersection(expected):
            print('TURSO_PRODUCTION_SCHEMA_NEW: core tables to be initialized by bot', flush=True)
        else:
            missing = expected - tables
            if missing:
                print('TURSO_PRODUCTION_SCHEMA_PARTIAL: '+str(len(missing))+' tables pending init', flush=True)
        fk = db.execute('PRAGMA foreign_keys').fetchone()
        if not fk or fk[0] != 1:
            raise RuntimeError('TURSO_PRODUCTION_FOREIGN_KEYS_DISABLED')
        violations = db.execute('PRAGMA foreign_key_check').fetchone()
        if violations is not None:
            raise RuntimeError('TURSO_PRODUCTION_FOREIGN_KEY_VIOLATION')
        sanity = db.execute('PRAGMA quick_check').fetchone()
        if not sanity or str(sanity[0]).lower() != 'ok':
            raise RuntimeError('TURSO_PRODUCTION_INTEGRITY_CHECK_FAILED')
    print('TURSO_PRODUCTION_READONLY_OK (not full gameplay acceptance)', flush=True)
    return True


if __name__ == '__main__':
    verify()
