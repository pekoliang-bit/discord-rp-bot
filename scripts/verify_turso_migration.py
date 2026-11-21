"""Read-only verification of old SQLite snapshot vs Turso exported SQLite snapshot.
Stop all writers before export. Import into a NEW Turso database using dashboard.
No credentials needed, no database writes, no automatic merging.
"""
import argparse
import hashlib
import json
import sqlite3
import struct
from contextlib import closing
from pathlib import Path
from scripts.verify_turso_backup import inspect_file, quote_name


def encode(value):
    if value is None:
        tag, data = b'N', b''
    elif isinstance(value, int):
        tag, data = b'I', str(value).encode('ascii')
    elif isinstance(value, float):
        tag, data = b'F', struct.pack('>d', value)
    elif isinstance(value, str):
        tag, data = b'T', value.encode('utf-8')
    elif isinstance(value, bytes):
        tag, data = b'B', value
    else:
        raise TypeError(type(value).__name__)
    return tag + len(data).to_bytes(8, 'big') + data


def fingerprint(path):
    report = inspect_file(path)
    path = Path(path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        schema = dict(((r[0],r[1]),r[2]) for r in db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))
        names = list(report['tables'])
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
            names.append('sqlite_sequence')
        hashes = {}
        for name in names:
            rows = []
            for row in db.execute('SELECT * FROM '+quote_name(name)):
                h = hashlib.sha256()
                for value in row:
                    h.update(encode(value))
                rows.append(h.digest())
            # Order-independent, preserves duplicate rows.
            h = hashlib.sha256()
            for digest in sorted(rows):
                h.update(digest)
            hashes[name] = (len(rows), h.hexdigest())
    return schema, hashes


def compare(source, exported):
    if Path(source).resolve() == Path(exported).resolve():
        raise RuntimeError('Use two distinct snapshot files.')
    a, x = fingerprint(source)
    b, y = fingerprint(exported)
    schema_diff = sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))
    data_diff = sorted(k for k in x.keys() | y.keys() if x.get(k) != y.get(k))
    if schema_diff or data_diff:
        raise RuntimeError(f'Migration mismatch: schema={schema_diff}; data={data_diff}. Do not switch production.')
    return {'tables': len(x), 'rows': sum(v[0] for v in x.values())}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('exported', type=Path)
    args = p.parse_args()
    print('TURSO_MIGRATION_CONTENT_OK', json.dumps(compare(args.source,args.exported)))


if __name__ == '__main__':
    main()
