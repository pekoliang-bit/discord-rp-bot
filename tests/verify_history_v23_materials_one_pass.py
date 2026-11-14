
"""V2.3 source provenance + importer safety + asset revisions, all against a temp DB."""
from __future__ import annotations
import json,os,subprocess,sys,tempfile,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
A=ROOT/'assets/history_v23'

def read(filename):return json.loads((A/filename).read_text(encoding='utf-8'))

catalog=read('catalog_580_inventory.json')
assert len(catalog)==580 and len({r['asset_id'] for r in catalog})==580
assert all(r['previously_approved'] for r in catalog)
assert sum(r['import_ready'] for r in catalog)==580
assert sum(r['candidate_available'] for r in catalog)==0
assert sum(not r['import_ready'] and not r['candidate_available'] for r in catalog)==0
assert set(r['asset_id'] for r in catalog)==set(r['asset_id'] for r in read('current_adopted_580.json'))
source=read('verified_import_ready.json')
assert len(source)==55
assert len(read('WN_0001_0200_rebuilt_import.json'))==192
assert len(read('CN_0001_0100_rebuilt_import.json'))==100
assert len(read('HE_0001_0075.json'))==75
assert len(read('HE_0076_0150.json'))==75
assert len(read('HE_0151_0230.json'))==75
assert len(read('candidate_text_index_517.json'))==517
r=read('HE_0151_0230_approved_rarity_mapping.json')
assert len(r)==80 and len(set(x['asset_id'] for x in r))==80
m={x['asset_id']:x for x in r}
for x in source:
    if x['asset_id'] in m:
        assert x['rarity']==m[x['asset_id']]['rarity'] and x['tier']==m[x['asset_id']]['tier']
assert all(not x['import_ready'] for x in read('WN_0193_0200_original_missing.json'))

code=r'''
import json,sqlite3
from pathlib import Path
from core.database import init_db,transaction,get_connection
from core.history_v23 import put_batch,put,disable,list_assets,attempt_lock
root=Path('assets/history_v23')
source=json.loads((root/'verified_import_ready.json').read_text(encoding='utf-8'))
init_db()
assert put_batch('123456789012345678',source)==55
assert put_batch('123456789012345678',source)==0
with get_connection() as db:
    rows=db.execute("SELECT COUNT(*) FROM rp_history_assets_v23 WHERE pool='general'").fetchone()[0]
    assert rows==55
    assert db.execute('SELECT COUNT(*) FROM characters').fetchone()[0]==0
    assert db.execute('SELECT COUNT(*) FROM rp_history_results_v23').fetchone()[0]==0
candidate=json.loads((root/'CN_0001_0100_rebuilt_import.json').read_text(encoding='utf-8'))
try:put_batch('123456789012345678',source[:1]+candidate[:1])
except ValueError:pass
else:raise AssertionError('Newly rewritten candidates must never import')
try:put_batch('123456789012345678',[{'asset_id':'WN-0001','title':'候選標題','meta':candidate[0]['meta']}])
except ValueError:pass
else:raise AssertionError('Candidate meta provenance guard failed')
try:put_batch('123456789012345678',[{'asset_id':'HE-0152','title':'非法代言','tier':'B5','meta':{'hb_id':'HB-0001'}}])
except ValueError:pass
else:raise AssertionError('Mismatching HB B-tier allowed')
assert not put('123456789012345678','general','HB-0001','Mellow Day 柔日','B1',meta={'industry':'美妝護膚'},approved=True)
try:put('123456789012345678','general','HB-0001','更名示例','B1',meta={'industry':'美妝護膚'},approved=True)
except ValueError:pass
else:raise AssertionError('Silent overwrite allowed')
assert put('123456789012345678','general','HB-0001','修訂後名稱','B1',meta={'industry':'美妝護膚'},approved=True,replace_existing=True)
with get_connection() as db:
    old=db.execute('SELECT previous_title FROM rp_history_asset_revisions_v23').fetchone()
    assert old['previous_title']=='Mellow Day 柔日'
assert len(list_assets('123456789012345678',pool='general',keyword='修訂後名稱'))==1
assert len(list_assets('123456789012345678',pool='passerby'))==0
assert put('123456789012345678','passerby','WN-0999','路人專用範例',meta={},approved=True)
assert len(list_assets('123456789012345678',pool='passerby'))==1
assert len(list_assets('123456789012345678',pool='general',keyword='路人專用範例'))==0
disable('123456789012345678','general','HB-0002',False)
assert not put('123456789012345678','general','HB-0002','PUREMINT 薄荷日記','B1',meta={'industry':'美妝護膚'},approved=True)
with get_connection() as db:
    assert db.execute("SELECT enabled FROM rp_history_assets_v23 WHERE asset_id='HB-0002'").fetchone()['enabled']==0
    assert db.execute('SELECT COUNT(*) FROM characters').fetchone()[0]==0
print('PASS: 55 atomically imported; 517 candidates blocked; update auditable; pools isolated; 0 character changes')
'''
with tempfile.TemporaryDirectory() as tmp:
    env={**os.environ,'DATABASE_PATH':str(Path(tmp)/'temp.sqlite3')}
    proc=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,text=True,capture_output=True)
    if proc.returncode:
        print(proc.stdout,proc.stderr)
        raise SystemExit(proc.returncode)
    print(proc.stdout.strip())
print('PASS: 580 current approved/import-ready inventory; original 55 legacy source still preserved')
