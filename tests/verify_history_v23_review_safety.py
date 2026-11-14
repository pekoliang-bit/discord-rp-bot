"""確認45筆原先核准品牌可直接匯入，無需重新審核。"""
from __future__ import annotations
import json,os,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
folder=ROOT/'assets/history_v23'
source=folder/'HB_0001_0045_approved.json'
rows=json.loads(source.read_text(encoding='utf8'))
assert len(rows)==45
assert [r['asset_id'] for r in rows]==[f'HB-{x:04d}' for x in range(1,46)]
assert all(r['approved'] is True and r['import_ready'] is True and r['title'] for r in rows)
catalog=json.loads((folder/'catalog_580_inventory.json').read_text(encoding='utf8'))
assert len(catalog)==580
assert sum(r['status']=='previously_approved' for r in catalog)==45
assert sum(r['import_ready'] is True for r in catalog)==55
with tempfile.TemporaryDirectory() as d:
    env={**os.environ,'DATABASE_PATH':str(Path(d)/'check.sqlite3')}
    base=[sys.executable,str(ROOT/'tools/import_history_v23_assets.py'),'--guild','1544051203493601380','--file',str(source)]
    subprocess.run(base+['--apply'],cwd=ROOT,env=env,check=True)
    subprocess.run(base+['--apply'],cwd=ROOT,env=env,check=True)
    import sqlite3
    db=sqlite3.connect(env['DATABASE_PATH'])
    assert db.execute("SELECT COUNT(*) FROM rp_history_assets_v23").fetchone()[0]==45
    assert db.execute("SELECT COUNT(*) FROM rp_history_assets_v23 WHERE approved=1 AND enabled=1").fetchone()[0]==45
    db.close()
print('PASS: 45 previously approved brands are imported idempotently without another review')
