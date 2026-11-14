"""Offline regression checks for V2.3 catalogue, sourced-only import, and idempotence."""
from __future__ import annotations
import json,os,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CAT=ROOT/'assets/history_v23/catalog_580_inventory.json'
IM=ROOT/'assets/history_v23/verified_import_ready.json'
rows=json.loads(CAT.read_text(encoding='utf8'))
assert len(rows)==580
assert len({r['asset_id'] for r in rows})==580
assert sum(bool(r['import_ready']) for r in rows)==55
assert sum(r['title'] is None for r in rows)==525
assert sum(r['status']=='previously_approved' for r in rows)==45
assert len(json.loads(IM.read_text(encoding='utf8')))==55
print('PASS: 580 IDs complete; 45 previously approved HB restored; 525 missing text; 55 ready')
with tempfile.TemporaryDirectory() as directory:
    db=Path(directory)/'isolated.sqlite3'
    env={**os.environ,'DATABASE_PATH':str(db)}
    subprocess.run([sys.executable,'-c','from core.database import init_db;init_db()'],cwd=ROOT,env=env,check=True)
    base=[sys.executable,str(ROOT/'tools/import_history_v23_assets.py'),'--guild','1544051203493601380']
    subprocess.run(base,cwd=ROOT,env=env,check=True)
    subprocess.run(base+['--apply'],cwd=ROOT,env=env,check=True)
    subprocess.run(base+['--apply'],cwd=ROOT,env=env,check=True)
    import sqlite3
    conn=sqlite3.connect(db)
    rows=conn.execute('SELECT asset_id,title,approved,enabled FROM rp_history_assets_v23').fetchall()
    assert len(rows)==55
    assert all(r[1] and r[2]==1 and r[3]==1 for r in rows)
    assert conn.execute('SELECT COUNT(*) FROM characters').fetchone()[0]==0
    conn.close()
print('PASS: 55 approved entries import idempotently into isolated SQLite, no characters created')
