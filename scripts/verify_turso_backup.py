"""Validate an exported SQLite snapshot locally; never writes to the source.
Use: python -m scripts.verify_turso_backup PATH [--compare-remote]
Stop writers before exporting/comparing. Remote comparison checks schema and counts,
not row-by-row equality. Use Turso dashboard Upload SQLite File to a NEW database.
"""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from contextlib import closing


def quote_name(name):
    return '"' + name.replace('"', '""') + '"'


def inventory(db):
    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return {r[0]: {'columns': [tuple(c) for c in db.execute('PRAGMA table_info('+quote_name(r[0])+')')], 'rows': db.execute('SELECT COUNT(*) FROM '+quote_name(r[0])).fetchone()[0]} for r in tables}


def inspect_file(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError('Not a file')
    if Path(str(path)+'-wal').exists() or Path(str(path)+'-journal').exists():
        raise RuntimeError('Use a consistent exported snapshot, not a live database with journal/WAL.')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        if [r[0] for r in db.execute('PRAGMA integrity_check')] != ['ok']:
            raise RuntimeError('SQLite integrity_check failed')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise RuntimeError('Foreign key violations found; do not import before review.')
        tables = inventory(db)
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            digest.update(block)
    return {'file': path.name, 'bytes': path.stat().st_size, 'sha256': digest.hexdigest(), 'tables': tables}


def compare_remote(snapshot):
    from core.turso_backend import connect
    with closing(connect()) as db:
        db.execute('BEGIN')
        try:
            remote = inventory(db)
        finally:
            db.rollback()
    if remote != snapshot['tables']:
        missing = sorted(snapshot['tables'].keys()-remote.keys())
        extra = sorted(remote.keys()-snapshot['tables'].keys())
        different = sorted(k for k in remote.keys() & snapshot['tables'].keys() if remote[k] != snapshot['tables'][k])
        raise RuntimeError(f'Schema/count mismatch: missing={missing}, extra={extra}, changed={different}')
    print('TURSO_BACKUP_SCHEMA_COUNTS_MATCH (not a row-content comparison)')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('file', type=Path)
    p.add_argument('--compare-remote', action='store_true')
    a = p.parse_args()
    report = inspect_file(a.file)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if a.compare_remote:
        compare_remote(report)
    print('SQLITE_SNAPSHOT_OK')


if __name__ == '__main__':
    main()
