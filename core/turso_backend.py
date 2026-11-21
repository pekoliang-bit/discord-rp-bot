"""Remote libSQL compatibility candidate. Never opens a local database."""
import os
import sqlite3
from urllib.parse import urlsplit


def configured():
    return bool(os.getenv('TURSO_DATABASE_URL') or os.getenv('TURSO_AUTH_TOKEN') or os.getenv('RENDER'))


def credentials():
    url = os.getenv('TURSO_DATABASE_URL', '').strip()
    token = os.getenv('TURSO_AUTH_TOKEN', '').strip()
    p = urlsplit(url)
    if p.scheme != 'libsql' or not p.hostname or p.username or p.password or p.query or p.fragment or not token:
        raise RuntimeError('Turso URL／Token 缺失或格式錯誤；禁止回退本機 SQLite。')
    return url, token


class Row:
    def __init__(self, description, values):
        self._keys = tuple(x if isinstance(x, str) else x[0] for x in description)
        self._values = tuple(values)

    def keys(self):
        return list(self._keys)

    def __getitem__(self, key):
        if isinstance(key, str):
            for i, name in enumerate(self._keys):
                if name.lower() == key.lower():
                    return self._values[i]
            raise IndexError('No item with that key')
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


def statements(script):
    buf = ''
    for ch in script:
        buf += ch
        if ch == ';' and sqlite3.complete_statement(buf):
            yield buf
            buf = ''
    if buf.strip():
        if not sqlite3.complete_statement(buf + '\n;'):
            raise sqlite3.OperationalError('Incomplete SQL script')
        yield buf


class Cursor:
    def __init__(self, connection, raw):
        self.connection = connection
        self.raw = raw

    @property
    def description(self):
        return self.raw.description

    @property
    def rowcount(self):
        return self.raw.rowcount

    @property
    def lastrowid(self):
        return self.raw.lastrowid

    def execute(self, sql, parameters=()):
        self.raw.execute(sql, parameters)
        return self

    def executemany(self, sql, parameters):
        self.raw.executemany(sql, list(parameters))
        return self

    def _row(self, row):
        if row is None or self.connection.row_factory is None:
            return row
        if self.connection.row_factory is sqlite3.Row:
            return Row(self.description, row)
        return self.connection.row_factory(self, row)

    def fetchone(self):
        return self._row(self.raw.fetchone())

    def fetchall(self):
        return [self._row(r) for r in self.raw.fetchall()]

    def fetchmany(self, size=None):
        return [self._row(r) for r in (self.raw.fetchmany() if size is None else self.raw.fetchmany(size))]

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row


class Connection:
    def __init__(self, raw):
        self.raw = raw
        self.row_factory = sqlite3.Row

    @property
    def in_transaction(self):
        return self.raw.in_transaction

    def cursor(self):
        return Cursor(self, self.raw.cursor())

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, parameters):
        return self.cursor().executemany(sql, parameters)

    def executescript(self, script):
        # Keep current transaction intact, unlike sqlite3's implicit COMMIT.
        for sql in statements(script):
            self.execute(sql)
        return self.cursor()

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, typ, value, tb):
        if typ is None:
            try:
                self.commit()
            except Exception:
                self.rollback()
                raise
        else:
            self.rollback()
        return False


def connect():
    url, token = credentials()
    import libsql
    conn = Connection(libsql.connect(database=url, auth_token=token))
    try:
        conn.execute('PRAGMA foreign_keys = ON')
        if conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
            raise RuntimeError('Remote foreign_keys not enabled')
        return conn
    except Exception:
        conn.close()
        raise


def require_staging_gate():
    if configured():
        credentials()
        if os.getenv('RP_TURSO_MODE') != 'staging':
            raise RuntimeError('Turso 尚未完成實機驗證；僅允許 RP_TURSO_MODE=staging 的隔離測試，禁止正式啟動。')
