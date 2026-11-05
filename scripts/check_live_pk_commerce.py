"""Offline SQLite regression test for PK and commerce. Run: python scripts/check_live_pk_commerce.py

Tests use an isolated temporary database and stub only the shared database adapter;
no Discord/network activity, no wallet transfers, no changes to production data.
"""
import contextlib
import importlib.util
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run():
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'test.sqlite3'
        core=types.ModuleType('core'); core.__path__=[str(ROOT/'core')]; sys.modules['core']=core
        database=types.ModuleType('core.database')
        def connect():
            c=sqlite3.connect(path); c.row_factory=sqlite3.Row; return c
        @contextlib.contextmanager
        def tx():
            c=connect()
            try:
                c.execute('BEGIN IMMEDIATE');yield c;c.commit()
            except Exception: c.rollback();raise
            finally:c.close()
        database.transaction=tx;database.get_connection=connect
        sys.modules['core.database']=database
        live=types.ModuleType('core.live_stream')
        live._init=lambda db: None
        live._expire=lambda db,gid,now: None
        sys.modules['core.live_stream']=live
        for name in ('live_pk','live_commerce'):
            spec=importlib.util.spec_from_file_location('core.'+name,ROOT/'core'/f'{name}.py')
            module=importlib.util.module_from_spec(spec);sys.modules['core.'+name]=module;spec.loader.exec_module(module)
        pk=sys.modules['core.live_pk'];sale=sys.modules['core.live_commerce']
        with tx() as db:
            db.executescript('''CREATE TABLE characters(id INTEGER PRIMARY KEY,guild_id TEXT,owner_id TEXT,status TEXT);
            CREATE TABLE rp_live_sessions(id INTEGER PRIMARY KEY,guild_id TEXT,host_character_id INTEGER,owner_id TEXT,stream_type TEXT,status TEXT,ends_at INTEGER);
            CREATE TABLE rp_live_gifts(id INTEGER PRIMARY KEY,guild_id TEXT,session_id INTEGER,gross_amount INTEGER,created_at INTEGER);
            INSERT INTO characters VALUES(1,'1','10','active'),(2,'1','20','active'),(3,'1','30','active');
            INSERT INTO rp_live_sessions VALUES(101,'1',1,'10','pk','live',NULL),(102,'1',2,'20','pk','live',NULL),(103,'1',3,'30','sales','live',NULL);''')
        mid=pk.challenge(1,10,101,102,now=1000)
        assert len(pk.pending(1,20,2,now=1001))==1
        try:pk.respond(1,10,2,mid,True,now=1002);raise AssertionError('owner bypass')
        except PermissionError:pass
        assert pk.respond(1,20,2,mid,True,now=1010)=='active'
        with tx() as db:
            db.execute("INSERT INTO rp_live_gifts(guild_id,session_id,gross_amount,created_at) VALUES('1',101,100,1011)")
            db.execute("INSERT INTO rp_live_gifts(guild_id,session_id,gross_amount,created_at) VALUES('1',102,50,1011)")
        assert pk.status(1,mid)['challenger_score']==100
        final=pk.finish(1,10,mid,now=1200)
        assert final['winner_character_id']==1
        assert pk.finish(1,10,mid,now=1300)['challenger_score']==100
        offer=sale.propose_offer(1,30,103,'product-1','Ã¥ÂÂÃ¥ÂÂÃ§ÂÂ²',500,10)
        assert sale.offers(1,103)[0]['status']=='pending_verification'
        try:sale.verify_offer(1,offer,{'product_ref':'product-1','unit_price':500,'available_stock':10});raise AssertionError('approval bypass')
        except PermissionError:pass
        try:sale.register_interest(1,10,1,offer,1,'test-1');raise AssertionError('unapproved offer')
        except ValueError:pass
        sale.verify_offer(1,offer,{'product_ref':'product-1','unit_price':500,'available_stock':10},trusted=True)
        iid=sale.register_interest(1,10,1,offer,2,'test-2')
        assert sale.register_interest(1,10,1,offer,2,'test-2')==iid
        with connect() as db:
            assert db.execute('SELECT COUNT(*) FROM rp_live_sale_interest').fetchone()[0]==1
        print('PASS: PK consent, score from real gifts, once-only finish; commerce approval gate, ownership, idempotency.')


if __name__=='__main__':run()
